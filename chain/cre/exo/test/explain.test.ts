import { expect, test } from "bun:test";
import { explain, isUnlimited } from "../src/lib/explain";

const SELF = "0x00000000000000000000000000000000000000aa";
const USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48";
const NFT = "0x000000000000000000000000000000000000cccc";
const MIRA = "0x000000000000000000000000000000000000bbbb";
const meta = { [USDC]: { symbol: "USDC", decimals: 6 } };
const book = { [MIRA]: "mira.eth" };
const OK = { otherEvents: 0, reverted: false };

test("send reads like the demo line", () => {
  expect(explain([{ kind: "erc20", token: USDC, from: SELF, to: MIRA, amount: 20_000_000n }], SELF, meta, book, OK))
    .toBe("You pay 20 USDC to mira.eth. Nothing else changes.");
});

test("approval for all is spelled out", () => {
  const s = explain([{ kind: "approval_for_all", token: NFT, from: SELF, to: "0xc0ffee0000000000000000000000000000000000", approved: true }], SELF, meta, book, OK);
  expect(s).toBe("You give 0xc0ff…0000 control of all your NFTs in collection 0x0000…cccc.");
});

test("unlimited approval", () => {
  const s = explain([{ kind: "approval", token: USDC, from: SELF, to: MIRA, amount: 2n ** 256n - 1n }], SELF, meta, book, OK);
  expect(s).toBe("You let mira.eth spend an unlimited amount of your USDC.");
});

test("receive and nothing", () => {
  expect(explain([{ kind: "native", token: "ETH", from: MIRA, to: SELF, amount: 10n ** 17n }], SELF, meta, book, OK))
    .toBe("You receive 0.1 ETH from mira.eth. Nothing else changes.");
  expect(explain([], SELF, meta, book, OK)).toBe("Nothing changes in your wallet.");
});

test("very large allowances count as unlimited (uint96 max and up), smaller ones are stated", () => {
  expect(isUnlimited(2n ** 96n - 1n)).toBe(true);
  expect(isUnlimited(2n ** 160n - 1n)).toBe(true);
  expect(isUnlimited(2n ** 96n - 2n)).toBe(false);
  expect(explain([{ kind: "approval", token: USDC, from: SELF, to: MIRA, amount: 2n ** 96n - 1n }], SELF, meta, book, OK))
    .toBe("You let mira.eth spend an unlimited amount of your USDC.");
  expect(explain([{ kind: "approval", token: USDC, from: SELF, to: MIRA, amount: 50_000_000n }], SELF, meta, book, OK))
    .toBe("You let mira.eth spend up to 50 USDC.");
});

test("an unknown token shows raw units, never a guessed decimal count", () => {
  const X = "0x1111111111111111111111111111111111112222";
  expect(explain([{ kind: "erc20", token: X, from: SELF, to: MIRA, amount: 20_000_000n }], SELF, meta, book, OK))
    .toBe("You pay 20000000 raw units of token 0x1111…2222 to mira.eth. Nothing else changes.");
});

test("revokes are stated, not hidden", () => {
  expect(explain([{ kind: "approval_for_all", token: NFT, from: SELF, to: MIRA, approved: false }], SELF, meta, book, OK))
    .toBe("You remove mira.eth's control of all your NFTs in collection 0x0000…cccc. Nothing else changes.");
  expect(explain([{ kind: "approval", token: USDC, from: SELF, to: MIRA, amount: 0n }], SELF, meta, book, OK))
    .toBe("You remove mira.eth's permission to spend your USDC. Nothing else changes.");
});

test("NFT moves and single-NFT approvals", () => {
  expect(explain([{ kind: "erc721", token: NFT, from: SELF, to: MIRA, tokenId: 42n }], SELF, meta, book, OK))
    .toBe("You give NFT #42 of collection 0x0000…cccc to mira.eth. Nothing else changes.");
  expect(explain([{ kind: "approval", token: NFT, from: SELF, to: MIRA, tokenId: 7n }], SELF, meta, book, OK))
    .toBe("You let mira.eth move your NFT #7 of collection 0x0000…cccc.");
});

test("changes that do not touch the wallet are not narrated", () => {
  expect(explain([{ kind: "erc20", token: USDC, from: MIRA, to: NFT, amount: 1n }], SELF, meta, book, OK))
    .toBe("Nothing changes in your wallet.");
});

test("unreadable effects: no reassuring ending, an explicit warning instead", () => {
  const pay = [{ kind: "erc20" as const, token: USDC as any, from: SELF as any, to: MIRA as any, amount: 20_000_000n }];
  const s = explain(pay, SELF, meta, book, { otherEvents: 1, reverted: false });
  expect(s).toBe("You pay 20 USDC to mira.eth. Warning: 1 effect of this transaction couldn't be read, so more may change than this says.");
  expect(s).not.toContain("Nothing else changes");
  expect(explain(pay, SELF, meta, book, { otherEvents: 2, reverted: false })).toContain("2 effects of this transaction couldn't be read");
  const none = explain([], SELF, meta, book, { otherEvents: 1, reverted: false });
  expect(none).toBe("Nothing changes in your wallet that this check can read. Warning: 1 effect of this transaction couldn't be read, so more may change than this says.");
  expect(none).not.toBe("Nothing changes in your wallet.");
});

test("a reverting transaction says it would fail, even with changes passed in", () => {
  const pay = [{ kind: "erc20" as const, token: USDC as any, from: SELF as any, to: MIRA as any, amount: 20_000_000n }];
  expect(explain(pay, SELF, meta, book, { otherEvents: 0, reverted: true })).toBe("This transaction reverts: it would fail and change nothing.");
  expect(explain([], SELF, meta, book, { otherEvents: 0, reverted: true })).not.toBe("Nothing changes in your wallet.");
});

test("the trace status is required: omitting or mangling it throws instead of reassuring", () => {
  expect(() => (explain as any)([], SELF, meta, book)).toThrow(TypeError);
  expect(() => (explain as any)([], SELF, meta, book, {})).toThrow(TypeError);
  expect(() => (explain as any)([], SELF, meta, book, { otherEvents: "1", reverted: false })).toThrow(TypeError);
  expect(() => (explain as any)([], SELF, meta, book, { otherEvents: NaN, reverted: false })).toThrow(TypeError);
});

test("checksummed meta and book keys resolve", () => {
  const cmeta = { "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": { symbol: "USDC", decimals: 6 } };
  const cbook = { "0x000000000000000000000000000000000000BbBB": "mira.eth" };
  expect(explain([{ kind: "erc20", token: USDC, from: SELF, to: MIRA, amount: 20_000_000n }], SELF, cmeta, cbook, OK))
    .toBe("You pay 20 USDC to mira.eth. Nothing else changes.");
});
