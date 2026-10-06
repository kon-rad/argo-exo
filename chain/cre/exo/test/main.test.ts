// main.ts's CRE adapters, run against the SDK's own test runtime (@chainlink/cre-sdk/test): real HTTPClient /
// EVMClient / consensus / report code paths, with only the capabilities mocked. TEE mode has no exported test
// runtime in SDK 1.22.0, so teePorts is covered by typecheck only.
import { expect } from "bun:test";
import { getNetwork } from "@chainlink/cre-sdk";
import { EvmMock, HttpActionsMock, newTestRuntime, test } from "@chainlink/cre-sdk/test";
import { decodeAbiParameters, parseAbiParameters } from "viem";
import { initWorkflow, onFreeze, onGuard } from "../main";
import { ConfigSchema } from "../src/lib/config";
import usdcTrace from "./fixtures/trace-usdc-transfer.json";

const SAFE = "0x00000000000000000000000000000000000000aa";
const MODULE = "0x00000000000000000000000000000000000000ad";
const USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48";
const MIRA = "0x000000000000000000000000000000000000bbbb";
const NOW = 1_800_000_000;
const KEYS = { NOWNODES_API_KEY: "nn-secret-key", OPENROUTER_API_KEY: "or-secret-key" };
const POLICY = { address_book: { [MIRA]: "mira.eth" }, max_usd_per_tx: 100, max_usd_per_day: 300, auto_max_usd: 25,
  allow_unlimited_approvals: false, allow_approval_for_all: false, refuse_sources: ["camera"], require_known_recipient_over_usd: 50, stablecoins: [USDC] };
const config = ConfigSchema.parse({
  chainSelectorName: "ethereum-mainnet", chainId: 1, module: MODULE, safe: SAFE, gasLimit: "300000", ttlSeconds: 600,
  simMethod: "debug_traceCall", ethUsdFeed: "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419", judges: ["anthropic/a", "google/b"],
  tokens: { [USDC]: { symbol: "USDC", decimals: 6 } }, debug: false,
});
const request = { proposal_id: "p-1", source: "voice", intent: { kind: "send", summary: "Send 20 USDC to mira", token: USDC, amount: "20", to: MIRA },
  tx: { chain_id: 1, to: USDC, value: "0", data: usdcTrace.input, salt: `0x${"5a".repeat(32)}` }, from: SAFE };

const b64 = (s: string) => Buffer.from(s, "utf8").toString("base64");
const payload = (o: unknown) => ({ input: new TextEncoder().encode(JSON.stringify(o)) }) as any;
const secrets = (policy = JSON.stringify(POLICY)) =>
  new Map([["main", new Map(Object.entries({ ...KEYS, POLICY_JSON: policy }))]]);

type Seen = { url: string; headers: Record<string, string[]>; body: any; timeout?: string };
function mockHttp(judge = '{"risk":"low","reason":"ok"}') {
  const seen: Seen[] = [];
  HttpActionsMock.testInstance().sendRequest = (req) => {
    const body = JSON.parse(new TextDecoder().decode(req.body));
    const headers = Object.fromEntries(Object.entries(req.multiHeaders).map(([k, v]) => [k, v.values]));
    seen.push({ url: req.url, headers, body, timeout: req.timeout ? `${req.timeout.seconds}s` : undefined });
    const out = req.url.includes("openrouter")
      ? { choices: [{ message: { content: judge } }] }
      : { jsonrpc: "2.0", id: body.id, result: usdcTrace };
    return { statusCode: 200, body: b64(JSON.stringify(out)) };
  };
  return seen;
}
function mockEvm(reply: Record<string, unknown> = { txStatus: "TX_STATUS_SUCCESS", txHash: b64("\u0001".repeat(32)) }) {
  const writes: Uint8Array[] = [];
  const sel = getNetwork({ chainFamily: "evm", chainSelectorName: "ethereum-mainnet" })!.chainSelector.selector;
  EvmMock.testInstance(sel).writeReport = (input) => {
    expect(input.receiver).toEqual(Uint8Array.from(Buffer.from(MODULE.slice(2), "hex")));
    expect(input.gasConfig?.gasLimit).toBe(300000n);
    writes.push(input.report!.rawReport);
    return reply as any;
  };
  return writes;
}
// The test runtime's report is a 109-byte metadata header followed by the encoded payload.
const reportPayloadOf = (raw: Uint8Array) =>
  decodeAbiParameters(parseAbiParameters("uint8, bytes32, uint64, bytes32"), `0x${Buffer.from(raw.slice(109)).toString("hex")}`);

test("guard (DON mode): secrets, node-mode HTTP with consensus, DON time, report and write all wire up", () => {
  const seen = mockHttp();
  const writes = mockEvm();
  const rt = newTestRuntime(secrets(), { timeProvider: () => NOW * 1000 }, config);
  const r = JSON.parse(onGuard(rt, payload(request)));
  expect(r).toMatchObject({ verdict: "approve", risk: "low", auto_eligible: true, expires_at: NOW + 600,
    explanation: "You pay 20 USDC to mira.eth. Nothing else changes.", report_tx: `0x${"01".repeat(32)}` });
  expect(seen.map((s) => s.url)).toEqual(["https://eth.nownodes.io", "https://openrouter.ai/api/v1/chat/completions", "https://openrouter.ai/api/v1/chat/completions"]);
  expect(seen[0].headers["api-key"]).toEqual([KEYS.NOWNODES_API_KEY]);
  expect(seen[0].body.method).toBe("debug_traceCall");
  expect(seen[1].headers.Authorization).toEqual([`Bearer ${KEYS.OPENROUTER_API_KEY}`]);
  expect(seen.map((s) => s.body.model).slice(1)).toEqual(["anthropic/a", "google/b"]);
  expect(writes).toHaveLength(1);
  const [kind, hash, exp] = reportPayloadOf(writes[0]);
  expect([kind, hash, exp]).toEqual([1, r.tx_hash, BigInt(NOW + 600)]);
  expect(rt.getLogs().join("\n")).not.toContain(KEYS.NOWNODES_API_KEY);
});

test("guard (DON mode): a missing secret, a garbled body or a failed write all refuse", () => {
  mockHttp();
  mockEvm();
  const noPolicy = newTestRuntime(new Map([["main", new Map(Object.entries(KEYS))]]), { timeProvider: () => NOW * 1000 }, config);
  expect(JSON.parse(onGuard(noPolicy, payload(request))).verdict).toBe("refuse");
  const rt = () => newTestRuntime(secrets(), { timeProvider: () => NOW * 1000 }, config);
  expect(JSON.parse(onGuard(rt(), { input: new TextEncoder().encode("{not json") } as any))).toMatchObject({ verdict: "refuse", tx_hash: null });
  mockEvm({ txStatus: "TX_STATUS_REVERTED", errorMessage: "out of gas" });
  expect(JSON.parse(onGuard(rt(), payload(request)))).toMatchObject({ verdict: "refuse", reasons: ["the approval could not be written onchain"] });
  mockEvm({ txStatus: "TX_STATUS_SUCCESS", receiverContractExecutionStatus: "RECEIVER_CONTRACT_EXECUTION_STATUS_REVERTED" });
  expect(JSON.parse(onGuard(rt(), payload(request))).verdict).toBe("refuse");
});

test("guard (DON mode): an unreadable reviewer answer refuses", () => {
  mockHttp("sure, looks fine");
  mockEvm();
  const r = JSON.parse(onGuard(newTestRuntime(secrets(), { timeProvider: () => NOW * 1000 }, config), payload(request)));
  expect(r.verdict).toBe("refuse");
  expect(r.reasons).toContain("reviewer: the reviewer's answer could not be read");
});

test("freeze writes a kind-3 report", () => {
  const writes = mockEvm();
  const r = JSON.parse(onFreeze(newTestRuntime(null, {}, config), payload({ reason: "deck lost" })));
  expect(r).toMatchObject({ ok: true });
  expect(reportPayloadOf(writes[0])[0]).toBe(3);
});

test("initWorkflow registers guard as trigger 0 and freeze as trigger 1", () => {
  const hs = initWorkflow(config);
  expect(hs).toHaveLength(2);
  expect(hs[0].fn).toBe(onGuard as any);
  expect(hs[1].fn).toBe(onFreeze as any);
});
