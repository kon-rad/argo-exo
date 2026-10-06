import { expect, test } from "bun:test";
import usdc from "./fixtures/trace-usdc-transfer.json";
import afa from "./fixtures/trace-approval-for-all.json";
import rev from "./fixtures/trace-reverted-subcall.json";
import { changesFromCallTrace, changesFromSimulateV1 } from "../src/lib/trace";
import { decodeCall } from "../src/lib/decode";

const SELF = "0x00000000000000000000000000000000000000aa";
const USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48";
const TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef";
const APPROVAL = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925";
const SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1";
const t = (a: string) => "0x" + a.slice(2).padStart(64, "0");
const u = (n: bigint) => "0x" + n.toString(16).padStart(64, "0");
const MAX = 2n ** 256n - 1n;

test("erc20 transfer from logs", () => {
  const r = changesFromCallTrace(usdc as any);
  expect(r.reverted).toBe(false);
  expect(r.otherEvents).toBe(0);
  expect(r.changes).toEqual([{ kind: "erc20", token: USDC,
    from: SELF, to: "0x000000000000000000000000000000000000bbbb", amount: 20_000_000n }]);
});

test("approvalForAll is surfaced", () => {
  const r = changesFromCallTrace(afa as any);
  expect(r.changes).toHaveLength(1);
  expect(r.changes[0]).toMatchObject({ kind: "approval_for_all", token: "0x000000000000000000000000000000000000cccc",
    from: SELF, to: "0xc0ffee0000000000000000000000000000000000", approved: true });
  expect(r.otherEvents).toBe(0);
});

test("native value and nested frames; reverted frames ignored", () => {
  const frame = { type: "CALL", from: "0xaa", to: "0xbb", value: "0xde0b6b3a7640000", calls: [
    { type: "CALL", from: "0xbb", to: "0xcc", value: "0x1", error: "execution reverted", logs: [] },
    { type: "CALL", from: "0xbb", to: "0xdd", value: "0x2" }] };
  const r = changesFromCallTrace(frame as any);
  expect(r.changes.map((c) => c.amount)).toEqual([10n ** 18n, 2n]);
});

test("top-level revert", () => {
  expect(changesFromCallTrace({ type: "CALL", from: "0xaa", to: "0xbb", value: "0x0", error: "execution reverted" } as any).reverted).toBe(true);
});

test("a top-level revert reports no changes even if the node returned logs", () => {
  const r = changesFromCallTrace({ ...(usdc as any), error: "execution reverted" });
  expect(r).toEqual({ changes: [], reverted: true, otherEvents: 0 });
});

test("reverted subcall fixture: its value, children and logs are dropped; nested native counted; unknown event counted", () => {
  const r = changesFromCallTrace(rev as any);
  expect(r.reverted).toBe(false);
  expect(r.changes).toEqual([
    { kind: "native", token: "ETH", from: SELF, to: "0x000000000000000000000000000000000000dddd", amount: 10n ** 18n },
    { kind: "native", token: "ETH", from: "0x000000000000000000000000000000000000dddd", to: "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", amount: 10n ** 18n },
    { kind: "native", token: "ETH", from: "0x000000000000000000000000000000000000dddd", to: SELF, amount: 10n ** 16n },
  ]);
  expect(r.otherEvents).toBe(1); // WETH Deposit is not an asset-change event this library models
});

test("value on DELEGATECALL / CALLCODE is not a transfer; CREATE and SELFDESTRUCT value is", () => {
  const r = changesFromCallTrace({ type: "CALL", from: SELF, to: "0xbb", value: "0x0", calls: [
    { type: "DELEGATECALL", from: "0xbb", to: "0xcc", value: "0x9" },
    { type: "CALLCODE", from: "0xbb", to: "0xcc", value: "0x8" },
    { type: "CREATE2", from: "0xbb", to: "0xee", value: "0x3" },
    { type: "SELFDESTRUCT", from: "0xee", to: "0xff", value: "0x3" }] } as any);
  expect(r.changes.map((c) => [c.to, c.amount])).toEqual([["0xee", 3n], ["0xff", 3n]]);
});

test("value sent to an unknown destination is counted as unrecognised, not dropped", () => {
  const r = changesFromCallTrace({ type: "CREATE", from: SELF, value: "0x5" } as any);
  expect(r.changes).toEqual([]);
  expect(r.otherEvents).toBe(1);
});

test("unrecognised and malformed logs increment otherEvents, never silently dropped", () => {
  const logs = [
    { address: "0x000000000000000000000000000000000000ee01", topics: [SYNC], data: "0x" + "00".repeat(64) },
    { address: USDC, topics: [TRANSFER, t(SELF)], data: u(1n) },                                // too few topics
    { address: USDC, topics: [TRANSFER, t(SELF), t("0xbb")], data: "0x" },                      // amount missing
    { address: USDC, topics: [TRANSFER, "0x" + "ff".repeat(32), t("0xbb")], data: u(1n) },      // not an address
    { address: USDC, topics: [TRANSFER], data: "0x" + t(SELF).slice(2) + t("0xbb").slice(2) + u(5n).slice(2) }, // unindexed 721
    { address: USDC, topics: [], data: "0x" },                                                  // anonymous
  ];
  const r = changesFromCallTrace({ type: "CALL", from: SELF, to: USDC, value: "0x0", logs } as any);
  expect(r.changes).toEqual([]);
  expect(r.otherEvents).toBe(logs.length);
});

test("erc721 transfer and single-token approval (4 topics)", () => {
  const nft = "0x000000000000000000000000000000000000cccc";
  const r = changesFromCallTrace({ type: "CALL", from: SELF, to: nft, value: "0x0", logs: [
    { address: nft, topics: [APPROVAL, t(SELF), t("0xc0ffee"), u(7n)], data: "0x" },
    { address: nft, topics: [TRANSFER, t(SELF), t("0xbbbb"), u(42n)], data: "0x" }] } as any);
  expect(r.changes).toEqual([
    { kind: "approval", token: nft, from: SELF, to: "0x0000000000000000000000000000000000c0ffee", tokenId: 7n },
    { kind: "erc721", token: nft, from: SELF, to: "0x000000000000000000000000000000000000bbbb", tokenId: 42n }]);
});

test("max-uint approval is kept exact (bigint) for the unlimited check", () => {
  const r = changesFromCallTrace({ type: "CALL", from: SELF, to: USDC, value: "0x0", logs: [
    { address: USDC, topics: [APPROVAL, t(SELF), t("0xbbbb")], data: u(MAX) }] } as any);
  expect(r.changes).toEqual([{ kind: "approval", token: USDC, from: SELF, to: "0x000000000000000000000000000000000000bbbb", amount: MAX }]);
});

test("approvalForAll with a non-boolean payload is unrecognised", () => {
  const r = changesFromCallTrace({ ...(afa as any), logs: [{ ...(afa as any).logs[0], data: u(2n) }] });
  expect(r.changes).toEqual([]);
  expect(r.otherEvents).toBe(1);
});

// eth_simulateV1 with traceTransfers: native moves come back as Transfer logs from 0xeeee…eeee.
const NATIVE = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
const simBlock = (calls: any[]) => [{ number: "0x1", hash: "0x" + "11".repeat(32), calls }];
const simCall = (logs: any[], status = "0x1") => ({ returnData: "0x", gasUsed: "0x5208", status, logs });

test("simulateV1: native synthetic logs and erc20 logs, in order", () => {
  const r = changesFromSimulateV1(simBlock([simCall([
    { address: NATIVE, topics: [TRANSFER, t(SELF), t("0xbbbb")], data: u(10n ** 17n) },
    { address: USDC, topics: [TRANSFER, t(SELF), t("0xbbbb")], data: u(20_000_000n) },
    { address: USDC, topics: [SYNC], data: "0x" }])]));
  expect(r.reverted).toBe(false);
  expect(r.otherEvents).toBe(1);
  expect(r.changes).toEqual([
    { kind: "native", token: "ETH", from: SELF, to: "0x000000000000000000000000000000000000bbbb", amount: 10n ** 17n },
    { kind: "erc20", token: USDC, from: SELF, to: "0x000000000000000000000000000000000000bbbb", amount: 20_000_000n }]);
});

test("simulateV1: failed call, missing status or empty result is reverted", () => {
  expect(changesFromSimulateV1(simBlock([{ ...simCall([], "0x0"), error: { code: 3, message: "execution reverted" } }])).reverted).toBe(true);
  expect(changesFromSimulateV1(simBlock([{ returnData: "0x", logs: [] }])).reverted).toBe(true);
  expect(changesFromSimulateV1([]).reverted).toBe(true);
  expect(changesFromSimulateV1(null).reverted).toBe(true);
  expect(changesFromSimulateV1({ error: "x" }).reverted).toBe(true);
});

test("simulateV1: every call in every block is read; one revert marks the whole result reverted", () => {
  const ok = simCall([{ address: NATIVE, topics: [TRANSFER, t(SELF), t("0xbbbb")], data: u(1n) }]);
  const r = changesFromSimulateV1([...simBlock([ok]), ...simBlock([ok, simCall([], "0x0")])]);
  expect(r.changes).toHaveLength(2);
  expect(r.reverted).toBe(true);
});

test("decode known and unknown", () => {
  expect(decodeCall((usdc as any).input)).toMatchObject({ name: "transfer", known: true,
    args: { to: "0x000000000000000000000000000000000000bbbb", amount: "20000000" } });
  expect(decodeCall((afa as any).input)).toMatchObject({ name: "setApprovalForAll", known: true,
    args: { operator: "0xc0ffee0000000000000000000000000000000000", approved: "true" } });
  expect(decodeCall("0xdeadbeef")).toEqual({ name: "unknown 0xdeadbeef", known: false, args: {} });
});

test("decode covers approve, transferFrom, increaseAllowance and both safeTransferFrom overloads", async () => {
  const { encodeFunctionData, parseAbi } = await import("viem");
  const abi = parseAbi(["function approve(address,uint256)", "function transferFrom(address,address,uint256)",
    "function increaseAllowance(address,uint256)", "function safeTransferFrom(address,address,uint256)",
    "function safeTransferFrom(address,address,uint256,bytes)"]);
  const a = "0x000000000000000000000000000000000000bbbb";
  expect(decodeCall(encodeFunctionData({ abi, functionName: "approve", args: [a, MAX] })))
    .toEqual({ name: "approve", known: true, args: { spender: a, amountOrTokenId: MAX.toString() } });
  expect(decodeCall(encodeFunctionData({ abi, functionName: "transferFrom", args: [SELF, a, 5n] })).name).toBe("transferFrom");
  expect(decodeCall(encodeFunctionData({ abi, functionName: "increaseAllowance", args: [a, 5n] })).name).toBe("increaseAllowance");
  expect(decodeCall(encodeFunctionData({ abi, functionName: "safeTransferFrom", args: [SELF, a, 9n] })).args.tokenId).toBe("9");
  expect(decodeCall(encodeFunctionData({ abi, functionName: "safeTransferFrom", args: [SELF, a, 9n, "0x1234"] })))
    .toMatchObject({ name: "safeTransferFrom", known: true, args: { tokenId: "9", data: "0x1234" } });
});

test("known selector with malformed arguments is not known", () => {
  expect(decodeCall("0xa9059cbb0000")).toEqual({ name: "unknown 0xa9059cbb", known: false, args: {} });
  expect(decodeCall("0x")).toEqual({ name: "unknown 0x", known: false, args: {} });
  expect(decodeCall("0xA9059CBB" as any).known).toBe(false);
});
