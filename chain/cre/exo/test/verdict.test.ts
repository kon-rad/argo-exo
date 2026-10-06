import { expect, test } from "bun:test";
import { decide, failClosed, understood } from "../src/lib/verdict";
import { approvalExpiry, MAX_APPROVAL_TTL_SECONDS, reportPayload } from "../src/lib/payload";
import type { Change } from "../src/lib/types";
import { decodeAbiParameters, encodeFunctionData, keccak256, parseAbi, parseAbiParameters, toHex } from "viem";

const SELF = "0x00000000000000000000000000000000000000aa";
const USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48";
const MIRA = "0x000000000000000000000000000000000000bbbb";
const STRANGER = "0x00000000000000000000000000000000000000ff";
const pay = (to: string): Change => ({ kind: "erc20", token: USDC, from: SELF, to: to as any, amount: 20_000_000n });
const ok = { reverted: false, known: true, otherEvents: 0, violations: [], judges: [{ risk: "low", reason: "a" }, { risk: "low", reason: "b" }],
  usdOut: 20, autoMaxUsd: 25, changes: [pay(MIRA)], self: SELF, addressBook: { [MIRA]: "mira.eth" } } as const;

test("approve + auto eligible only when everything is low and small", () => {
  expect(decide(ok)).toMatchObject({ verdict: "approve", risk: "low", auto_eligible: true });
  expect(decide({ ...ok, usdOut: 25 }).auto_eligible).toBe(true);
  expect(decide({ ...ok, usdOut: 30 }).auto_eligible).toBe(false);
  expect(decide({ ...ok, changes: [pay(STRANGER)] }).auto_eligible).toBe(false);
  expect(decide({ ...ok, addressBook: {} }).auto_eligible).toBe(false);
  expect(decide({ ...ok, changes: [pay(MIRA), { kind: "approval", token: USDC, from: SELF, to: MIRA, amount: 1n }] }).auto_eligible).toBe(false);
  expect(decide({ ...ok, changes: [pay(MIRA), { kind: "approval", token: USDC, from: SELF, to: MIRA, amount: 0n }] }).auto_eligible).toBe(false); // any approval
  // checksummed address-book keys still count as known
  expect(decide({ ...ok, addressBook: { [MIRA.toUpperCase().replace("0X", "0x")]: "mira.eth" } }).auto_eligible).toBe(true);
  expect(decide({ ...ok, judges: [{ risk: "low", reason: "" }, { risk: "medium", reason: "" }] })).toMatchObject({ verdict: "approve", risk: "medium", auto_eligible: false });
});

test("the two judges disagreeing takes the higher risk", () => {
  expect(decide({ ...ok, judges: [{ risk: "medium", reason: "" }, { risk: "low", reason: "" }] }).risk).toBe("medium");
  expect(decide({ ...ok, judges: [{ risk: "low", reason: "" }, { risk: "high", reason: "drainer" }] }))
    .toMatchObject({ verdict: "refuse", risk: "high", auto_eligible: false });
  expect(decide({ ...ok, judges: [{ risk: "high", reason: "x" }, { risk: "medium", reason: "" }] }).risk).toBe("high");
});

test("refuse on any violation, high judge, revert or unknown function with no changes", () => {
  expect(decide({ ...ok, violations: ["x"] })).toMatchObject({ verdict: "refuse", risk: "high", auto_eligible: false });
  expect(decide({ ...ok, judges: [{ risk: "high", reason: "" }, { risk: "low", reason: "" }] }).verdict).toBe("refuse");
  expect(decide({ ...ok, reverted: true }).verdict).toBe("refuse");
  expect(decide({ ...ok, usdOut: null }).auto_eligible).toBe(false);
  expect(decide({ ...ok, usdOut: null }).verdict).toBe("approve"); // unpriced: a human must confirm, never auto
});

test("unknown function or unread events refuse (fail closed, not just 'not auto')", () => {
  expect(decide({ ...ok, known: false })).toMatchObject({ verdict: "refuse", risk: "high", auto_eligible: false });
  expect(decide({ ...ok, otherEvents: 1 })).toMatchObject({ verdict: "refuse", risk: "high", auto_eligible: false });
});

test("fewer than two judges, or a malformed judgement, refuses", () => {
  expect(decide({ ...ok, judges: [] }).verdict).toBe("refuse");
  expect(decide({ ...ok, judges: [{ risk: "low", reason: "" }] }).verdict).toBe("refuse");
  expect(decide({ ...ok, judges: [{ risk: "low", reason: "" }, { risk: "tiny" as any, reason: "" }] })).toMatchObject({ verdict: "refuse", risk: "high" });
  expect(decide({ ...ok, judges: [{ risk: "low", reason: "" }, null as any] }).verdict).toBe("refuse");
});

test("prototype-key risk words refuse (toString, constructor, __proto__)", () => {
  for (const r of ["toString", "constructor", "__proto__", "hasOwnProperty", "valueOf"]) {
    expect(decide({ ...ok, judges: [{ risk: r as any, reason: "" }, { risk: "low", reason: "" }] }))
      .toMatchObject({ verdict: "refuse", risk: "high", auto_eligible: false });
    expect(decide({ ...ok, judges: [{ risk: r as any, reason: "" }, { risk: r as any, reason: "" }] }).verdict).toBe("refuse");
  }
});

test("three judges, one high, still refuses", () => {
  expect(decide({ ...ok, judges: [...ok.judges, { risk: "high", reason: "z" }] }).verdict).toBe("refuse");
});

test("malformed decide inputs refuse rather than approve", () => {
  expect(decide({ ...ok, reverted: 0 as any }).verdict).toBe("refuse");
  expect(decide({ ...ok, changes: undefined as any }).verdict).toBe("refuse");
  expect(decide({ ...ok, self: undefined as any }).verdict).toBe("refuse");
  expect(decide({ ...ok, addressBook: undefined as any }).verdict).toBe("refuse");
  expect(decide({ ...ok, reverted: undefined as any }).verdict).toBe("refuse");
  expect(decide({ ...ok, known: "yes" as any }).verdict).toBe("refuse");
  expect(decide({ ...ok, otherEvents: NaN }).verdict).toBe("refuse");
  expect(decide({ ...ok, violations: undefined as any }).verdict).toBe("refuse");
  expect(decide({ ...ok, usdOut: NaN }).auto_eligible).toBe(false);
  expect(decide({ ...ok, autoMaxUsd: NaN }).auto_eligible).toBe(false);
  expect(decide(undefined as any).verdict).toBe("refuse");
});

test("auto_eligible is never true alongside a refuse", () => {
  for (const d of [{ violations: ["x"] }, { reverted: true }, { known: false }, { otherEvents: 2 }])
    expect(decide({ ...ok, ...d }).auto_eligible).toBe(false);
});

test("failClosed turns a thrown error anywhere in the pipeline into a refusal", () => {
  const d = failClosed(() => { throw new Error("rpc timeout"); });
  expect(d).toMatchObject({ verdict: "refuse", risk: "high", auto_eligible: false });
  expect(d.reasons[0]).toContain("rpc timeout");
  expect(failClosed(() => decide(ok)).verdict).toBe("approve");
});

test("understood: known token calls and plain ETH sends; anything else is not", () => {
  expect(understood("0x")).toBe(true);
  expect(understood(encodeFunctionData({ abi: parseAbi(["function transfer(address,uint256)"]), functionName: "transfer",
    args: ["0x000000000000000000000000000000000000bbbb", 1n] }))).toBe(true);
  expect(understood("0x12345678")).toBe(false);
  expect(understood("" as any)).toBe(false);
  expect(understood(undefined as any)).toBe(false);
});

test("report payload round-trips", () => {
  const h = keccak256(toHex("tx"));
  const p = reportPayload(1, h, 1800000600n, "ok");
  expect(decodeAbiParameters(parseAbiParameters("uint8, bytes32, uint64, bytes32"), p)).toEqual([1, h, 1800000600n, keccak256(toHex("ok"))]);
  const f = reportPayload(3, `0x${"0".repeat(64)}`, 0n, "panic");
  expect(decodeAbiParameters(parseAbiParameters("uint8, bytes32, uint64, bytes32"), f)[0]).toBe(3);
});

test("report payload rejects a ms expiry, a zero approve expiry, bad kinds and bad hashes", () => {
  const h = keccak256(toHex("tx"));
  expect(() => reportPayload(1, h, BigInt(Date.now()), "ok")).toThrow();       // milliseconds
  expect(() => reportPayload(1, h, 0n, "ok")).toThrow();
  expect(() => reportPayload(1, h, 1800000600 as any, "ok")).toThrow();         // not a bigint
  expect(() => reportPayload(4 as any, h, 1800000600n, "ok")).toThrow();
  expect(() => reportPayload(1, "0x1234", 1800000600n, "ok")).toThrow();
  expect(() => reportPayload(2, h, 2n ** 64n, "x")).toThrow();
});

test("approval expiry is in seconds and never beyond now + 1 hour", () => {
  expect(MAX_APPROVAL_TTL_SECONDS).toBe(3600);
  expect(approvalExpiry(1800000000, 600)).toBe(1800000600n);
  expect(approvalExpiry(1800000000n, 3600)).toBe(1800003600n);
  expect(() => approvalExpiry(1800000000, 3601)).toThrow();
  expect(() => approvalExpiry(1800000000, 0)).toThrow();
  expect(() => approvalExpiry(1800000000, 1.5)).toThrow();
  expect(() => approvalExpiry(Date.now(), 600)).toThrow();                      // a ms clock would be ~1000x too far out
  expect(() => approvalExpiry(NaN, 600)).toThrow();
  const e = approvalExpiry(Math.floor(Date.now() / 1000), 600);
  expect(e - BigInt(Math.floor(Date.now() / 1000)) <= 3600n).toBe(true);
});
