import { expect, test } from "bun:test";
import { encodeAbiParameters, parseAbiParameters } from "viem";
import { chainForId, ConfigSchema } from "../src/lib/config";
import { judgeFromReply, openRouterRequest, parseEthUsd, rpcHttpRequest, toSdkRequest } from "../src/lib/http";
import { parseFreezeReason, parseGuardRequest } from "../src/lib/request";

import committed from "../config.mainnet.json";
import sendUsdc from "../payloads/send-usdc.json";
import approvalForAll from "../payloads/approval-for-all.json";
import afaFixture from "./fixtures/trace-approval-for-all.json";

test("the committed config.mainnet.json parses, with placeholder addresses and two judge vendors", () => {
  const c = ConfigSchema.parse(committed);
  expect(c.module).toBe("0x0000000000000000000000000000000000000000");
  expect(c.safe).toBe("0x0000000000000000000000000000000000000000");
  expect(c.judges[0].split("/")[0]).not.toBe(c.judges[1].split("/")[0]);
  expect(c.debug).toBe(false);
});

test("config rejects one-vendor judges, a TTL over 1 h, a typo'd key and bad addresses", () => {
  expect(() => ConfigSchema.parse({ ...committed, judges: ["openai/a", "openai/b"] })).toThrow();
  expect(() => ConfigSchema.parse({ ...committed, judges: ["openai/a"] })).toThrow();
  expect(() => ConfigSchema.parse({ ...committed, judges: ["openai/a", "google/b", "x/c"] })).toThrow();
  expect(() => ConfigSchema.parse({ ...committed, ttlSeconds: 3601 })).toThrow();
  expect(() => ConfigSchema.parse({ ...committed, ttlSeconds: 0 })).toThrow();
  expect(() => ConfigSchema.parse({ ...committed, ttlSecond: 600 })).toThrow();
  expect(() => ConfigSchema.parse({ ...committed, module: "0x1234" })).toThrow();
  expect(() => ConfigSchema.parse({ ...committed, gasLimit: "0" })).toThrow();
  expect(() => ConfigSchema.parse({ ...committed, simMethod: "trace_call" })).toThrow();
});

test("chainForId maps known chains and throws otherwise", () => {
  expect(chainForId(1)).toBe("ethereum");
  expect(chainForId(8453)).toBe("base");
  expect(() => chainForId(5)).toThrow();
  expect(() => chainForId("constructor" as any)).toThrow();
});

test("guard request parsing lowercases addresses and hex, keeps value exact, drops unknown keys", () => {
  const r = parseGuardRequest({ proposal_id: "p", source: "voice", intent: { kind: "send", summary: "s" }, extra: 1,
    tx: { chain_id: 1, to: "0xA0b86991c6218b36c1D19D4a2e9Eb0cE3606eB48", value: "123456789012345678901234567890", data: "0xA9059CBB", salt: `0x${"AB".repeat(32)}` },
    from: "0x00000000000000000000000000000000000000AA" });
  expect(r.tx.to).toBe("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48");
  expect(r.tx.value).toBe(123456789012345678901234567890n);
  expect(r.tx.data).toBe("0xa9059cbb");
  expect(r.tx.salt).toBe(`0x${"ab".repeat(32)}`);
  expect(r.from).toBe("0x00000000000000000000000000000000000000aa");
  expect("extra" in r).toBe(false);
});

test("the demo payloads are well-formed guard requests", () => {
  for (const p of [sendUsdc, approvalForAll]) expect(parseGuardRequest(p).tx.chain_id).toBe(1);
  const afa = parseGuardRequest(approvalForAll);
  expect(afa.source).toBe("camera");
  expect(afa.tx.data).toBe(afaFixture.input as `0x${string}`);
});

test("freeze reason: cleaned, capped, defaults to panic", () => {
  expect(parseFreezeReason({ reason: " a\u0000b\n c " })).toBe("a b c");
  expect(parseFreezeReason({ reason: "x".repeat(500) })).toHaveLength(200);
  for (const bad of [null, undefined, [], "s", { reason: 5 }, { reason: "  " }]) expect(parseFreezeReason(bad)).toBe("panic");
});

test("toSdkRequest builds the documented CRE request shape", () => {
  const r = toSdkRequest(rpcHttpRequest("ethereum", "k", "eth_call", [1]));
  expect(r).toMatchObject({ url: "https://eth.nownodes.io", method: "POST", timeout: "10s",
    multiHeaders: { "Content-Type": { values: ["application/json"] }, "api-key": { values: ["k"] } } });
  expect(JSON.parse(Buffer.from(r.body, "base64").toString("utf8"))).toEqual({ jsonrpc: "2.0", id: 1, method: "eth_call", params: [1] });
  const j = openRouterRequest("anthropic/m", "héllo <data>", "key");
  expect(JSON.parse(Buffer.from(toSdkRequest(j).body, "base64").toString("utf8")).messages[0].content).toBe("héllo <data>");
  expect(JSON.parse(j.body)).toMatchObject({ model: "anthropic/m", temperature: 0 });
});

test("judgeFromReply fails closed on anything but a clean 2xx reply", () => {
  const reply = (content: unknown, status = 200) => ({ status, body: JSON.stringify({ choices: [{ message: { content } }] }) });
  expect(judgeFromReply(reply('{"risk":"low","reason":"ok"}'))).toEqual({ risk: "low", reason: "ok" });
  expect(judgeFromReply(reply('{"risk":"low","reason":"ok"}', 500)).risk).toBe("high");
  expect(judgeFromReply(reply('{"risk":"low","reason":"ok"}', 302)).risk).toBe("high");
  expect(judgeFromReply(reply(42)).risk).toBe("high");
  expect(judgeFromReply({ status: 200, body: "<html>" }).risk).toBe("high");
  expect(judgeFromReply({ status: 200, body: "{}" }).risk).toBe("high");
  expect(judgeFromReply(undefined as any).risk).toBe("high");
});

test("parseEthUsd reads 8-decimal answers and rejects stale, zero, negative, future or garbage rounds", () => {
  const now = 1_800_000_000;
  const round = (a: bigint, t: number) => encodeAbiParameters(parseAbiParameters("uint80, int256, uint256, uint256, uint80"), [9n, a, BigInt(t), BigInt(t), 9n]);
  expect(parseEthUsd(round(312345000000n, now - 100), now)).toBe(3123.45);
  expect(parseEthUsd(round(312345000000n, now - 7200), now)).toBe(3123.45);
  expect(parseEthUsd(round(312345000000n, now - 7201), now)).toBeNaN();
  expect(parseEthUsd(round(0n, now), now)).toBeNaN();
  expect(parseEthUsd(round(-5n, now), now)).toBeNaN();
  expect(parseEthUsd(round(312345000000n, 0), now)).toBeNaN();
  expect(parseEthUsd(round(312345000000n, now + 61), now)).toBeNaN();
  expect(parseEthUsd("0x1234", now)).toBeNaN();
});
