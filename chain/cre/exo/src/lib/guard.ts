import { simulateV1Params, traceCallParams, parseRpc } from "@argo-exo/nownodes";
import { formatUnits } from "viem";
import { chainForId, isZeroAddress, lower, type Config } from "./config";
import { decodeCall } from "./decode";
import { cleanLabel, explain, SYMBOL_MAX } from "./explain";
import { approvalHash } from "./hash";
import { judgeFromReply, latestRoundDataParams, openRouterRequest, parseEthUsd, rpcHttpRequest, type HttpReply, type HttpRequest } from "./http";
import { judgePrompt, type Judgement, type Risk } from "./judge";
import { approvalExpiry, reportPayload } from "./payload";
import { parseFreezeReason, parseGuardRequest, type GuardRequest } from "./request";
import { checkRules, parsePolicy, usdOutflow, type Policy } from "./rules";
import { changesFromCallTrace, changesFromSimulateV1 } from "./trace";
import type { Change, Hex, TokenMeta, TraceResult } from "./types";
import { decide, failClosed, understood, type Decision } from "./verdict";

/** Secret ids, as declared in chain/cre/secrets.yaml. */
export const SECRET_IDS = ["NOWNODES_API_KEY", "OPENROUTER_API_KEY", "POLICY_JSON"] as const;
export type Secrets = Record<(typeof SECRET_IDS)[number], string>;

/** Everything the guard pipeline needs from the CRE runtime. main.ts implements it twice (plain `handler` and
 *  `handlerInTee`); tests implement it with fakes. Every port may throw: the pipeline fails closed. */
export type GuardPorts = {
  /** The workflow's own clock (DON time / enclave time), in Unix SECONDS. */
  nowSeconds: () => number;
  secrets: () => Secrets;
  /** A JSON POST whose reply is deterministic (RPC). */
  post: (r: HttpRequest) => HttpReply;
  /** A reviewer call; the port returns judgeFromReply(reply) (in DON mode that runs per node, before consensus). */
  judge: (r: HttpRequest) => Judgement;
  /** Sign the payload as a CRE report and deliver it to config.module; returns the delivery tx hash (0x… or ""). */
  writeReport: (payload: Hex) => string;
  log: (m: string) => void;
};

/** One entry of the result's `changes` (§4.3: asset, from, to, amount), plus the exact raw values. */
export type ChangeOut = {
  kind: Change["kind"]; asset: string; token: string; from: string; to: string;
  amount?: string; raw_amount?: string; token_id?: string; approved?: boolean;
};

/** The workflow's return value, §4.3 of the architecture. */
export type GuardResult = {
  proposal_id: string | null; verdict: "approve" | "refuse"; risk: Risk; auto_eligible: boolean;
  explanation: string; reasons: string[]; tx_hash: Hex | null; expires_at: number; changes: ChangeOut[]; report_tx: string;
  /** USD leaving the Safe, priced from the simulated changes; set on an approval only (null otherwise). The runner
   *  sums it from the ledger into `context.spent_today_usd`, so the daily cap counts what actually got approved. */
  usd_out: number | null;
};

const lowerKeys = <V>(o: Record<string, V>) => Object.fromEntries(Object.entries(o ?? {}).map(([k, v]) => [k.toLowerCase(), v]));

/** Changes as shown to the wearer and the kiosk: token symbol and human amount where the config knows the token. */
export function formatChanges(changes: Change[], meta: TokenMeta): ChangeOut[] {
  const m = lowerKeys(meta);
  return changes.map((c) => {
    const info = c.token === "ETH" ? { symbol: "ETH", decimals: 18 } : (Object.hasOwn(m, c.token.toLowerCase()) ? m[c.token.toLowerCase()] : undefined);
    const asset = (info && cleanLabel(info.symbol, SYMBOL_MAX)) || c.token;
    const out: ChangeOut = { kind: c.kind, asset, token: c.token, from: c.from, to: c.to };
    if (c.amount !== undefined) {
      out.raw_amount = c.amount.toString();
      out.amount = info ? formatUnits(c.amount, info.decimals) : c.amount.toString();
    }
    if (c.tokenId !== undefined) out.token_id = c.tokenId.toString();
    if (c.approved !== undefined) out.approved = c.approved;
    return out;
  });
}

/** Problems with the request itself, before any simulation: each one is a violation (refusal). */
export function requestViolations(req: GuardRequest, cfg: Config): string[] {
  const v: string[] = [];
  if (isZeroAddress(cfg.module) || isZeroAddress(cfg.safe)) v.push("the Guardian is not configured (module or Safe address unset)");
  if (req.tx.chain_id !== cfg.chainId) v.push(`is for chain ${req.tx.chain_id}, not chain ${cfg.chainId}`);
  // The approval hash does not bind `from`: ExoModule always executes from the Safe. Simulating any other sender
  // would judge a different transaction than the one that runs.
  if (req.from !== lower(cfg.safe)) v.push("is not from the agent Safe this Guardian protects");
  return v;
}

const refuseWith = (reasons: string[]): Decision => ({ verdict: "refuse", risk: "high", auto_eligible: false, reasons });

const isNativeOutflow = (c: Change, me: string) => c.kind === "native" && c.from.toLowerCase() === me && c.to.toLowerCase() !== me;

/** The full guard pipeline (§4.3): parse, simulate the exact transaction, price, rules, explain, two judges, decide,
 *  then write a kind-1 report for an approval (a refusal writes nothing onchain). Any throw
 *  anywhere before the verdict is a refusal (failClosed). */
export function runGuard(input: unknown, cfg: Config, ports: GuardPorts): GuardResult {
  // Filled in as the pipeline gets that far; whatever is missing after a throw stays at its safe default.
  // `bound` is set only once the request is proven to be for this Safe, chain and module: only then may a report be
  // written. Otherwise anyone able to fire the trigger could revoke a pending approval with a mis-bound request
  // that shares its hash.
  const ctx: { req?: GuardRequest; txHash?: Hex; bound: boolean; sim?: TraceResult; explanation: string; expiresAt: bigint; usdOut: number | null } =
    { bound: false, explanation: "", expiresAt: 0n, usdOut: null };
  const meta = lowerKeys(cfg.tokens) as TokenMeta;

  let d: Decision = failClosed(() => {
    const r = (ctx.req = parseGuardRequest(input));
    const self = r.from;
    ctx.txHash = approvalHash(BigInt(cfg.chainId), lower(cfg.module), r.tx.to, r.tx.value, r.tx.data, r.tx.salt);
    const early = requestViolations(r, cfg);
    if (early.length) return refuseWith(early);
    ctx.bound = true;

    // Fixed reasons, never e.message: a JSON parse error quotes the policy text ("Unexpected identifier \"mira\"").
    let s: Secrets;
    try { s = ports.secrets(); } catch { return refuseWith(["secrets unavailable"]); }
    let policy: Policy;
    try { policy = parsePolicy(s.POLICY_JSON); } catch { return refuseWith(["policy unreadable"]); }
    const chain = chainForId(cfg.chainId);
    const rpc = <T>(method: string, params: unknown[]): T => {
      const reply = ports.post(rpcHttpRequest(chain, s.NOWNODES_API_KEY, method, params));
      return parseRpc<T>(reply.status, reply.body);
    };

    // 1-3. Decode and simulate the exact transaction, from the Safe.
    const tx = { from: self, to: r.tx.to, value: r.tx.value, data: r.tx.data };
    const sim = (ctx.sim = cfg.simMethod === "debug_traceCall"
      ? changesFromCallTrace(rpc("debug_traceCall", traceCallParams(tx)))
      : changesFromSimulateV1(rpc("eth_simulateV1", simulateV1Params(tx)), { from: self, value: tx.value }));
    const changes = sim.changes;

    // 4. ETH/USD from the Chainlink feed, only when ETH leaves the Safe. A missing or stale price refuses that send.
    const extra: string[] = [];
    let ethUsd = NaN;
    if (changes.some((c) => isNativeOutflow(c, self))) {
      ethUsd = parseEthUsd(rpc<Hex>("eth_call", latestRoundDataParams(lower(cfg.ethUsdFeed))), ports.nowSeconds());
      if (!Number.isFinite(ethUsd)) extra.push("the ETH price could not be read, so the ETH sent can't be checked against your limits");
    }

    // 5. Rules, on the simulated changes (never on the English).
    const usdOut = (ctx.usdOut = usdOutflow(changes, self, meta, ethUsd, policy.stablecoins));
    const rules = checkRules({ changes, self, source: r.source, intent: r.intent, usdOut,
      spentTodayUsd: r.context?.spent_today_usd ?? 0, meta, to: r.tx.to, module: lower(cfg.module) }, policy);
    const violations = [...rules.violations, ...extra];

    // 6-7. Explain, then two independent reviewers (an unreachable reviewer reads as high risk).
    ctx.explanation = explain(changes, self, meta, policy.address_book, { otherEvents: sim.otherEvents, reverted: sim.reverted });
    const prompt = judgePrompt({ intent: r.intent, decoded: decodeCall(r.tx.data), changes, explanation: ctx.explanation, violations });
    const judges = cfg.judges.map((model) => {
      try { return ports.judge(openRouterRequest(model, prompt, s.OPENROUTER_API_KEY)); } catch { return judgeFromReply({ status: 0, body: "" }); }
    });

    // 8. Verdict.
    const verdict = decide({ reverted: sim.reverted, known: understood(r.tx.data), otherEvents: sim.otherEvents, violations,
      judges, usdOut, autoMaxUsd: policy.auto_max_usd, changes, self, addressBook: policy.address_book });
    // The expiry comes from the workflow's own clock, never from the request; a bad clock throws → refuse.
    if (verdict.verdict === "approve") ctx.expiresAt = approvalExpiry(ports.nowSeconds(), cfg.ttlSeconds);
    return verdict;
  });
  if (d.verdict !== "approve") ctx.expiresAt = 0n;

  let reportTx = "";
  // Only an approval is written onchain (kind 1), and only for a request bound to this Safe, chain and module (so
  // its hash is ours to approve). A refusal writes nothing: its salt is fresh, so no pending approval shares its
  // hash and a kind-2 write would be pure gas. Approvals that are never executed expire on their own (ttl, <= 1 h).
  if (d.verdict === "approve" && ctx.bound && ctx.txHash && !isZeroAddress(cfg.module)) {
    try {
      reportTx = ports.writeReport(reportPayload(1, ctx.txHash, ctx.expiresAt, d.reasons.join("; ")));
    } catch {
      // An approval that never lands onchain cannot execute: refuse, so nothing queues it for the key.
      d = { verdict: "refuse", risk: "high", auto_eligible: false, reasons: ["the approval could not be written onchain"] };
      ctx.expiresAt = 0n;
    }
  }
  if (cfg.debug) ports.log(`guard ${ctx.req?.proposal_id ?? "?"}: ${d.verdict} (${d.risk}), report ${reportTx || "none"}`);

  return {
    proposal_id: ctx.req?.proposal_id ?? null,
    verdict: d.verdict, risk: d.risk, auto_eligible: d.auto_eligible,
    explanation: d.verdict === "approve" ? ctx.explanation
      : `Refused: ${d.reasons[0] ?? "the check failed"}.${ctx.explanation ? ` ${ctx.explanation}` : ""}`,
    reasons: d.reasons,
    tx_hash: ctx.txHash ?? null,
    expires_at: Number(ctx.expiresAt),
    changes: ctx.sim ? formatChanges(ctx.sim.changes, meta) : [],
    report_tx: reportTx,
    usd_out: d.verdict === "approve" && ctx.usdOut !== null && Number.isFinite(ctx.usdOut) ? ctx.usdOut : null,
  };
}

/** The freeze handler: a kind-3 report to the module. Any input freezes; only the module owner can unfreeze. */
export function runFreeze(input: unknown, cfg: Config, writeReport: (payload: Hex) => string): { ok: boolean; report_tx: string; error?: string } {
  const reason = parseFreezeReason(input);
  if (isZeroAddress(cfg.module)) return { ok: false, report_tx: "", error: "module address unset" };
  try {
    return { ok: true, report_tx: writeReport(reportPayload(3, `0x${"0".repeat(64)}`, 0n, reason)) };
  } catch (e) {
    return { ok: false, report_tx: "", error: `freeze report failed: ${e instanceof Error ? e.message : String(e)}`.slice(0, 300) };
  }
}
