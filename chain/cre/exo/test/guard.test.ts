import { expect, test } from "bun:test";
import { decodeAbiParameters, encodeAbiParameters, keccak256, parseAbiParameters, toHex } from "viem";
import { traceCallParams } from "@argo-exo/nownodes";
import { ConfigSchema, type Config } from "../src/lib/config";
import { formatChanges, runFreeze, runGuard, type GuardPorts } from "../src/lib/guard";
import type { HttpRequest } from "../src/lib/http";
import { approvalHash } from "../src/lib/hash";
import type { Judgement } from "../src/lib/judge";
import type { Hex } from "../src/lib/types";
import usdcTrace from "./fixtures/trace-usdc-transfer.json";
import afaTrace from "./fixtures/trace-approval-for-all.json";
import revertedTrace from "./fixtures/trace-reverted-subcall.json";

const SAFE = "0x00000000000000000000000000000000000000aa";
const MODULE = "0x00000000000000000000000000000000000000ad";
const USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48";
const MIRA = "0x000000000000000000000000000000000000bbbb";
const FEED = "0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419";
const SALT = `0x${"5a".repeat(32)}` as Hex;
const NOW = 1_800_000_000;
const KEYS = { NOWNODES_API_KEY: "nn-secret-key", OPENROUTER_API_KEY: "or-secret-key" };
const POLICY = { address_book: { [MIRA]: "mira.eth" }, max_usd_per_tx: 100, max_usd_per_day: 300, auto_max_usd: 25,
  allow_unlimited_approvals: false, allow_approval_for_all: false, refuse_sources: ["camera"], require_known_recipient_over_usd: 50, stablecoins: [USDC] };

const cfg: Config = ConfigSchema.parse({
  chainSelectorName: "ethereum-mainnet", chainId: 1, module: MODULE, safe: SAFE, gasLimit: "300000", ttlSeconds: 600,
  simMethod: "debug_traceCall", ethUsdFeed: FEED, judges: ["anthropic/model-a", "google/model-b"],
  tokens: { [USDC]: { symbol: "USDC", decimals: 6 } }, debug: false,
});

const sendReq = (over: Record<string, unknown> = {}, tx: Record<string, unknown> = {}) => ({
  proposal_id: "p-1", source: "voice", requested_at: NOW,
  intent: { kind: "send", summary: "Send 20 USDC to mira", token: USDC, amount: "20", to: MIRA },
  tx: { chain_id: 1, to: USDC, value: "0", data: usdcTrace.input, salt: SALT, ...tx },
  from: SAFE, context: { spent_today_usd: 0 }, ...over,
});

const round = (answer: bigint, updatedAt: bigint) => encodeAbiParameters(parseAbiParameters("uint80, int256, uint256, uint256, uint80"),
  [1n, answer, updatedAt, updatedAt, 1n]);

type Fake = GuardPorts & { posts: HttpRequest[]; judgeCalls: HttpRequest[]; reports: Hex[]; logs: string[] };
function ports(o: { trace?: unknown; sim?: unknown; price?: Hex; judges?: Judgement[]; now?: number; policy?: string;
  post?: (r: HttpRequest) => never; writeReport?: (p: Hex) => string; secrets?: () => never; judge?: (r: HttpRequest) => Judgement } = {}): Fake {
  const f: Fake = {
    posts: [], judgeCalls: [], reports: [], logs: [],
    nowSeconds: () => o.now ?? NOW,
    secrets: o.secrets ?? (() => ({ ...KEYS, POLICY_JSON: o.policy ?? JSON.stringify(POLICY) })),
    post: (r) => {
      f.posts.push(r);
      if (o.post) o.post(r);
      const { method, id } = JSON.parse(r.body);
      const result = method === "debug_traceCall" ? (o.trace ?? usdcTrace) : method === "eth_simulateV1" ? o.sim
        : method === "eth_call" ? (o.price ?? round(3000n * 10n ** 8n, BigInt(NOW - 60))) : undefined;
      return { status: 200, body: JSON.stringify({ jsonrpc: "2.0", id, result }) };
    },
    judge: (r) => {
      f.judgeCalls.push(r);
      if (o.judge) return o.judge(r);
      const js = o.judges ?? [{ risk: "low", reason: "plain transfer" }, { risk: "low", reason: "matches intent" }];
      return js[f.judgeCalls.length - 1];
    },
    writeReport: (p) => { f.reports.push(p); return o.writeReport ? o.writeReport(p) : "0xreport"; },
    log: (m) => { f.logs.push(m); },
  };
  return f;
}
const decodeReport = (p: Hex) => decodeAbiParameters(parseAbiParameters("uint8, bytes32, uint64, bytes32"), p);
const reasonHash = (reasons: string[]) => keccak256(toHex(reasons.join("; ")));

test("a 20 USDC send to a known contact approves, auto-eligible, and writes a kind-1 report bound to the exact tx", () => {
  const p = ports();
  const r = runGuard(sendReq(), cfg, p);
  const hash = approvalHash(1n, MODULE, USDC, 0n, usdcTrace.input as Hex, SALT);
  expect(r).toMatchObject({ proposal_id: "p-1", verdict: "approve", risk: "low", auto_eligible: true, tx_hash: hash,
    expires_at: NOW + 600, report_tx: "0xreport", explanation: "You pay 20 USDC to mira.eth. Nothing else changes.", usd_out: 20 });
  expect(r.reasons).toEqual(["plain transfer", "matches intent"]);
  expect(r.changes).toEqual([{ kind: "erc20", asset: "USDC", token: USDC, from: SAFE, to: MIRA, amount: "20", raw_amount: "20000000" }]);
  expect(p.reports).toHaveLength(1);
  const [kind, h, exp, rh] = decodeReport(p.reports[0]);
  expect([kind, h, exp, rh]).toEqual([1, hash, BigInt(NOW + 600), reasonHash(r.reasons)]);
});

test("the simulation is of the exact transaction, from the Safe, through NOWNodes with the api-key header", () => {
  const p = ports();
  runGuard(sendReq(), cfg, p);
  expect(p.posts).toHaveLength(1); // no ETH leaves, so no price read
  const body = JSON.parse(p.posts[0].body);
  expect(p.posts[0].url).toBe("https://eth.nownodes.io");
  expect(p.posts[0].headers).toEqual({ "api-key": KEYS.NOWNODES_API_KEY });
  expect(body.method).toBe("debug_traceCall");
  expect(body.params).toEqual(traceCallParams({ from: SAFE, to: USDC, value: 0n, data: usdcTrace.input }));
});

test("two reviewers, one per configured model, each sent the OpenRouter key and the quoted prompt", () => {
  const p = ports();
  runGuard(sendReq(), cfg, p);
  expect(p.judgeCalls.map((c) => JSON.parse(c.body).model)).toEqual(["anthropic/model-a", "google/model-b"]);
  for (const c of p.judgeCalls) {
    expect(c.url).toBe("https://openrouter.ai/api/v1/chat/completions");
    expect(c.headers.Authorization).toBe(`Bearer ${KEYS.OPENROUTER_API_KEY}`);
    expect(JSON.parse(c.body).messages[0].content).toContain('<data name="explanation">');
  }
});

test("expiry comes from the workflow clock, never requested_at", () => {
  for (const requested_at of [1, NOW + 10_000_000, NOW * 1000]) expect(runGuard(sendReq({ requested_at }), cfg, ports()).expires_at).toBe(NOW + 600);
  expect(runGuard(sendReq(), cfg, ports({ now: NOW + 5 })).expires_at).toBe(NOW + 605);
});

test("a millisecond clock refuses and writes a kind-2 report with no expiry", () => {
  const p = ports({ now: NOW * 1000 });
  const r = runGuard(sendReq(), cfg, p);
  expect(r).toMatchObject({ verdict: "refuse", risk: "high", auto_eligible: false, expires_at: 0, usd_out: null });
  const [kind, , exp, rh] = decodeReport(p.reports[0]);
  expect([kind, exp, rh]).toEqual([2, 0n, reasonHash(r.reasons)]);
});

test("setApprovalForAll from the camera refuses for both reasons", () => {
  const p = ports({ trace: afaTrace });
  const r = runGuard(sendReq({ source: "camera", intent: { kind: "raw", summary: "scan" } }, { to: afaTrace.to, data: afaTrace.input }), cfg, p);
  expect(r.verdict).toBe("refuse");
  expect(r.reasons.join(" | ")).toContain("camera");
  expect(r.reasons.join(" | ")).toContain("all NFTs");
  expect(decodeReport(p.reports[0])[0]).toBe(2);
  expect(r.explanation.startsWith("Refused: ")).toBe(true);
});

test("a reverted trace and an unknown function both refuse", () => {
  expect(runGuard(sendReq(), cfg, ports({ trace: revertedTrace })).verdict).toBe("refuse");
  const r = runGuard(sendReq({}, { data: "0xdeadbeef" }), cfg, ports());
  expect(r.verdict).toBe("refuse");
  expect(r.reasons).toContain("calls a function this check does not understand");
});

test("any throwing port is a refusal (failClosed), and the refusal is still reported for a known hash", () => {
  for (const [o, reason] of [[{ post: () => { throw new Error("rpc down"); } }, "the check failed: rpc down"],
    [{ secrets: () => { throw new Error("vault said NOWNODES_API_KEY=abc"); } }, "secrets unavailable"]] as const) {
    const p = ports(o as any);
    const r = runGuard(sendReq(), cfg, p);
    expect(r).toMatchObject({ verdict: "refuse", risk: "high", reasons: [reason] });
    expect(decodeReport(p.reports[0])[0]).toBe(2);
  }
});

test("an unreadable policy refuses with a fixed reason that quotes none of it", () => {
  for (const policy of [JSON.stringify({ ...POLICY, allow_aproval_for_all: true }), "{address_book: mira}", "not json",
    JSON.stringify({ ...POLICY, address_book: { "0xmira": "mira.eth" } })]) {
    const p = ports({ policy });
    const r = runGuard(sendReq(), cfg, p);
    expect(r).toMatchObject({ verdict: "refuse", reasons: ["policy unreadable"], explanation: "Refused: policy unreadable." });
    expect(JSON.stringify(r)).not.toMatch(/mira|aproval|address_book/);
    expect(decodeReport(p.reports[0])[0]).toBe(2); // the request itself is bound to the Safe: its refusal is reported
  }
});

test("a malformed request refuses with no hash and writes nothing", () => {
  for (const bad of [{ ...sendReq(), tx: { ...sendReq().tx, salt: "0x1234" } }, { ...sendReq(), from: "mira" }, null, "x",
    { ...sendReq(), tx: { ...sendReq().tx, value: "-1" } }, { ...sendReq(), tx: { ...sendReq().tx, value: (2n ** 256n).toString() } },
    { ...sendReq(), tx: { ...sendReq().tx, data: "0xabc" } }]) {
    const p = ports();
    const r = runGuard(bad, cfg, p);
    expect(r).toMatchObject({ verdict: "refuse", tx_hash: null, report_tx: "", changes: [] });
    expect(p.reports).toHaveLength(0);
    expect(p.posts).toHaveLength(0);
  }
});

test("a request not from the Safe, or for another chain, refuses before simulating", () => {
  const p = ports();
  const r = runGuard(sendReq({ from: MIRA }), cfg, p);
  expect(r.reasons).toEqual(["is not from the agent Safe this Guardian protects"]);
  expect(p.posts).toHaveLength(0);
  // Not bound to this Safe/chain: refuse in the result, but write nothing (it would revoke a real approval sharing
  // the hash, since the hash does not bind `from`).
  expect(p.reports).toHaveLength(0);
  expect(r.report_tx).toBe("");
  const c = ports();
  expect(runGuard(sendReq({}, { chain_id: 8453 }), cfg, c).reasons).toEqual(["is for chain 8453, not chain 1"]);
  expect(c.reports).toHaveLength(0);
  // checksummed / uppercase input addresses are the same Safe
  expect(runGuard(sendReq({ from: SAFE.toUpperCase().replace("0X", "0x") }), cfg, ports()).verdict).toBe("approve");
});

test("an unconfigured module or Safe refuses and writes no report", () => {
  for (const c of [{ ...cfg, module: "0x0000000000000000000000000000000000000000" }, { ...cfg, safe: "0x0000000000000000000000000000000000000000" }]) {
    const p = ports();
    const r = runGuard(sendReq(), c, p);
    expect(r.verdict).toBe("refuse");
    expect(r.reasons[0]).toContain("not configured");
    expect(p.reports).toHaveLength(0);
  }
});

test("a request with the same hash as a pending approval but the wrong from cannot revoke it; a bound refusal can", () => {
  const legit = ports();
  const ok = runGuard(sendReq(), cfg, legit);
  expect(ok.verdict).toBe("approve");
  const attack = ports();
  const r = runGuard(sendReq({ from: MIRA, source: "camera" }), cfg, attack);
  expect(r).toMatchObject({ verdict: "refuse", tx_hash: ok.tx_hash, report_tx: "" });
  expect(attack.reports).toHaveLength(0);
  const bound = ports();
  const refused = runGuard(sendReq({ source: "camera" }), cfg, bound); // same hash, bound to the Safe, refused by rules
  expect(refused.verdict).toBe("refuse");
  const [kind, h] = decodeReport(bound.reports[0]);
  expect([kind, h]).toEqual([2, ok.tx_hash!]);
});

test("calling the module or the Safe directly refuses", () => {
  expect(runGuard(sendReq({}, { to: MODULE }), cfg, ports()).reasons).toContain("calls the wallet or its guard module directly");
});

test("reviewers: a high, an unreadable answer or a failed call refuses; medium approves without auto", () => {
  expect(runGuard(sendReq(), cfg, ports({ judges: [{ risk: "low", reason: "" }, { risk: "high", reason: "drainer" }] })).verdict).toBe("refuse");
  expect(runGuard(sendReq(), cfg, ports({ judges: [{ risk: "low", reason: "" }, { risk: "toString" as any, reason: "" }] })).verdict).toBe("refuse");
  expect(runGuard(sendReq(), cfg, ports({ judge: () => { throw new Error("timeout"); } })).verdict).toBe("refuse");
  expect(runGuard(sendReq(), cfg, ports({ judges: [{ risk: "medium", reason: "" }, { risk: "low", reason: "" }] })))
    .toMatchObject({ verdict: "approve", risk: "medium", auto_eligible: false });
});

test("an approval that can't be written onchain becomes a refusal; a failed refusal write stays a refusal", () => {
  const boom = () => { throw new Error("tx reverted"); };
  expect(runGuard(sendReq(), cfg, ports({ writeReport: boom })))
    .toMatchObject({ verdict: "refuse", reasons: ["the approval could not be written onchain"], expires_at: 0, report_tx: "" });
  expect(runGuard(sendReq({ source: "camera" }), cfg, ports({ writeReport: boom })).verdict).toBe("refuse");
});

test("ETH sends read the Chainlink price; a stale, zero or future price refuses", () => {
  const ethTrace = { type: "CALL", from: SAFE, to: MIRA, value: "0x38d7ea4c68000", input: "0x", output: "0x" }; // 0.001 ETH
  const req = sendReq({ intent: { kind: "send", summary: "0.001 ETH to mira", amount: "0.001", to: MIRA } }, { to: MIRA, value: "1000000000000000", data: "0x" });
  const p = ports({ trace: ethTrace });
  const ok = runGuard(req, cfg, p);
  expect(ok).toMatchObject({ verdict: "approve", auto_eligible: true }); // $3, under auto_max
  const call = JSON.parse(p.posts[1].body);
  expect(call.method).toBe("eth_call");
  expect(call.params[0].to).toBe(FEED);
  expect(call.params[0].data).toBe("0xfeaf968c"); // latestRoundData()
  for (const price of [round(3000n * 10n ** 8n, BigInt(NOW - 7201)), round(0n, BigInt(NOW)), round(-1n, BigInt(NOW)),
    round(3000n * 10n ** 8n, BigInt(NOW + 3600)), "0x" as Hex]) {
    const r = runGuard(req, cfg, ports({ trace: ethTrace, price }));
    expect(r.verdict).toBe("refuse");
    expect(r.reasons.join(" ")).toMatch(/ETH price could not be read|the check failed/);
  }
  // priced above the per-tx cap: 0.05 ETH at $3000 = $150 > $100
  const big = sendReq({ intent: { kind: "send", summary: "x", amount: "0.05", to: MIRA } }, { to: MIRA, value: "50000000000000000", data: "0x" });
  expect(runGuard(big, cfg, ports({ trace: { ...ethTrace, value: "0xb1a2bc2ec50000" } })).reasons).toContain("over the per-transaction limit of $100");
});

test("eth_simulateV1 mode simulates with the submitted from/value", () => {
  const c = { ...cfg, simMethod: "eth_simulateV1" as const };
  const log = usdcTrace.calls[0].logs[0];
  const p = ports({ sim: [{ calls: [{ status: "0x1", logs: [log] }] }] });
  expect(runGuard(sendReq(), c, p).verdict).toBe("approve");
  expect(JSON.parse(p.posts[0].body).method).toBe("eth_simulateV1");
  expect(runGuard(sendReq(), c, ports({ sim: [{ calls: [{ status: "0x0", logs: [] }] }] })).verdict).toBe("refuse");
  expect(runGuard(sendReq(), c, ports({ sim: [] })).verdict).toBe("refuse");
});

test("no secret value appears in the result or the debug log, and nothing is logged unless debug", () => {
  const p = ports();
  const r = runGuard(sendReq(), cfg, p);
  expect(p.logs).toEqual([]);
  const q = ports({ post: () => { throw new Error(`boom ${KEYS.NOWNODES_API_KEY.length}`); } });
  const dbg = runGuard(sendReq(), { ...cfg, debug: true }, q);
  expect(q.logs).toHaveLength(1);
  for (const s of [JSON.stringify(r), JSON.stringify(dbg), q.logs.join("\n")]) {
    for (const k of Object.values(KEYS)) expect(s).not.toContain(k);
    expect(s).not.toContain("auto_max_usd");
  }
});

test("formatChanges shows symbols and human amounts only for configured tokens", () => {
  const out = formatChanges([
    { kind: "erc20", token: "0x00000000000000000000000000000000000000ee", from: SAFE, to: MIRA, amount: 5n },
    { kind: "native", token: "ETH", from: SAFE, to: MIRA, amount: 10n ** 18n },
    { kind: "approval_for_all", token: afaTrace.to as Hex, from: SAFE, to: MIRA, approved: true },
  ], cfg.tokens);
  expect(out[0]).toMatchObject({ asset: "0x00000000000000000000000000000000000000ee", amount: "5", raw_amount: "5" });
  expect(out[1]).toMatchObject({ asset: "ETH", amount: "1" });
  expect(out[2]).toEqual({ kind: "approval_for_all", asset: afaTrace.to, token: afaTrace.to, from: SAFE, to: MIRA, approved: true });
});

test("freeze writes a kind-3 report with a cleaned reason; any input freezes", () => {
  const got: Hex[] = [];
  const w = (p: Hex) => { got.push(p); return "0xfrozen"; };
  expect(runFreeze({ reason: "lost\nthe deck" }, cfg, w)).toEqual({ ok: true, report_tx: "0xfrozen" });
  expect(decodeReport(got[0])).toEqual([3, `0x${"0".repeat(64)}`, 0n, keccak256(toHex("lost the deck"))]);
  runFreeze("garbage", cfg, w);
  expect(decodeReport(got[1])[3]).toBe(keccak256(toHex("panic")));
  expect(runFreeze({ reason: "x" }, { ...cfg, module: "0x0000000000000000000000000000000000000000" }, w).ok).toBe(false);
  expect(runFreeze({}, cfg, () => { throw new Error("no gas"); })).toMatchObject({ ok: false, report_tx: "" });
});
