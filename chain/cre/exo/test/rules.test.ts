import { expect, test } from "bun:test";
import { checkRules, parsePolicy, PolicySchema, usdOutflow, type Intent } from "../src/lib/rules";
import type { Change } from "../src/lib/types";

const SELF = "0x00000000000000000000000000000000000000aa";
const MODULE = "0x00000000000000000000000000000000000000ad";
const USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48";
const MIRA = "0x000000000000000000000000000000000000bbbb";
const NFT = "0x000000000000000000000000000000000000cccc";
const STRANGER = "0x00000000000000000000000000000000000000ff";
const meta = { [USDC]: { symbol: "USDC", decimals: 6 } };
const POLICY = { address_book: { [MIRA]: "mira.eth" }, max_usd_per_tx: 100, max_usd_per_day: 300, auto_max_usd: 25,
  allow_unlimited_approvals: false, allow_approval_for_all: false, refuse_sources: ["camera"], require_known_recipient_over_usd: 50, stablecoins: [USDC] };
const policy = PolicySchema.parse(POLICY);
const send = (amount: bigint, to = MIRA): Change[] => [{ kind: "erc20", token: USDC as any, from: SELF as any, to: to as any, amount }];
const intent: Intent = { kind: "send", summary: "send 20 USDC to mira", token: USDC, amount: "20", to: MIRA };
// The tx target and the module are part of every rules input (the module reverts calls to itself or the Safe).
const base = { self: SELF as any, module: MODULE as any, to: USDC as any, source: "voice", intent, spentTodayUsd: 0, meta };

test("usd outflow prices stablecoins and ETH, null for unknown tokens", () => {
  expect(usdOutflow(send(20_000_000n), SELF as any, meta, 3000, [USDC])).toBe(20);
  expect(usdOutflow([{ kind: "native", token: "ETH", from: SELF as any, to: MIRA as any, amount: 10n ** 17n }], SELF as any, meta, 3000, [])).toBe(300);
  expect(usdOutflow([{ kind: "erc20", token: "0x00000000000000000000000000000000000000ee" as any, from: SELF as any, to: MIRA as any, amount: 1n }], SELF as any, meta, 3000, [])).toBeNull();
});

test("usd outflow rounds up to the cent, never down", () => {
  expect(usdOutflow(send(100_000_001n), SELF as any, meta, 3000, [USDC])).toBe(100.01);
  expect(usdOutflow(send(1n), SELF as any, meta, 3000, [USDC])).toBe(0.01);
});

test("usd outflow: NFT outflow, unpriced stablecoin meta or a bad ETH price is unpriced (null)", () => {
  expect(usdOutflow([{ kind: "erc721", token: NFT as any, from: SELF as any, to: MIRA as any, tokenId: 1n }], SELF as any, meta, 3000, [USDC])).toBeNull();
  expect(usdOutflow(send(1_000_000n), SELF as any, {}, 3000, [USDC])).toBeNull();
  const eth: Change[] = [{ kind: "native", token: "ETH", from: SELF as any, to: MIRA as any, amount: 10n ** 18n }];
  for (const bad of [NaN, 0, -1, Infinity]) expect(usdOutflow(eth, SELF as any, meta, bad, [])).toBeNull();
});

test("usd outflow ignores inflows, third-party moves, self-transfers and approvals; checksummed keys resolve", () => {
  const cs: Change[] = [
    { kind: "erc20", token: USDC as any, from: MIRA as any, to: SELF as any, amount: 5_000_000n },
    { kind: "erc20", token: USDC as any, from: MIRA as any, to: STRANGER as any, amount: 5_000_000n },
    { kind: "erc20", token: USDC as any, from: SELF as any, to: SELF as any, amount: 5_000_000n },
    { kind: "approval", token: USDC as any, from: SELF as any, to: MIRA as any, amount: 5_000_000n },
  ];
  expect(usdOutflow(cs, SELF as any, meta, 3000, [USDC])).toBe(0);
  const CS_USDC = "0xA0b86991c6218b36c1D19D4a2e9Eb0cE3606eB48";
  expect(usdOutflow(send(2_000_000n), SELF as any, { [CS_USDC]: { symbol: "USDC", decimals: 6 } }, 3000, [CS_USDC])).toBe(2);
});

test("clean send passes", () => {
  expect(checkRules({ ...base, changes: send(20_000_000n), usdOut: 20 }, policy).violations).toEqual([]);
});

test("intent mismatch refuses", () => {
  const v = checkRules({ ...base, changes: send(200_000_000n), usdOut: 200 }, policy).violations;
  expect(v.some((x) => x.includes("does not match"))).toBe(true);
});

test("intent mismatch refuses: wrong recipient, wrong token, extra outflow, sub-unit amount, missing fields", () => {
  const mismatch = (changes: Change[], i: Intent = intent, usdOut = 20) =>
    checkRules({ ...base, changes, intent: i, usdOut }, policy).violations.some((x) => x.includes("does not match"));
  expect(mismatch(send(20_000_000n, STRANGER))).toBe(true);                                   // recipient differs
  expect(mismatch(send(20_000_001n))).toBe(true);                                             // off by one unit
  expect(mismatch([{ kind: "native", token: "ETH", from: SELF as any, to: MIRA as any, amount: 20_000_000n }])).toBe(true); // token differs
  expect(mismatch([...send(20_000_000n), ...send(1n, STRANGER)])).toBe(true);                // a second payment rides along
  expect(mismatch([...send(20_000_000n), { kind: "approval", token: USDC as any, from: SELF as any, to: STRANGER as any, amount: 1n }])).toBe(true);
  expect(mismatch([])).toBe(true);                                                            // nothing actually sent
  expect(mismatch(send(20_000_000n), { ...intent, amount: "20.0000001" })).toBe(true);        // more decimals than the token has
  expect(mismatch(send(20_000_000n), { ...intent, amount: "2e1" })).toBe(true);
  expect(mismatch(send(20_000_000n), { ...intent, to: undefined })).toBe(true);
  expect(mismatch(send(20_000_000n), { ...intent, amount: undefined })).toBe(true);
  const unknownToken = "0x00000000000000000000000000000000000000ee";
  expect(mismatch([{ kind: "erc20", token: unknownToken as any, from: SELF as any, to: MIRA as any, amount: 20n }],
    { ...intent, token: unknownToken })).toBe(true);                                          // decimals unknown: cannot verify
  // equivalent spellings still match
  expect(mismatch(send(20_000_000n), { ...intent, amount: "20.00", to: MIRA.toUpperCase().replace("0X", "0x"), token: USDC.toUpperCase().replace("0X", "0x") })).toBe(false);
  expect(mismatch([{ kind: "native", token: "ETH", from: SELF as any, to: MIRA as any, amount: 10n ** 16n }],
    { kind: "send", summary: "0.01 eth to mira", token: "ETH", amount: "0.01", to: MIRA })).toBe(false);
});

test("camera source, approvals, caps, unknown recipient", () => {
  const afa: Change[] = [{ kind: "approval_for_all", token: NFT as any, from: SELF as any, to: "0xc0ffee0000000000000000000000000000000000" as any, approved: true }];
  const v = checkRules({ ...base, to: NFT as any, changes: afa, source: "camera", intent: { kind: "raw", summary: "claim airdrop" }, usdOut: 0 }, policy).violations;
  expect(v).toContain("came from the camera, which is never trusted to move funds");
  expect(v).toContain("gives control of all NFTs in a collection");
  const big = checkRules({ ...base, changes: send(90_000_000n, STRANGER), source: "agent:trader",
    intent: { kind: "raw", summary: "x" }, usdOut: 90, spentTodayUsd: 250 }, policy).violations;
  expect(big).toContain("over the daily limit of $300");
  expect(big).toContain("sends over $50 to an address not in your address book");
  const tx = checkRules({ ...base, changes: send(150_000_000n), intent: { kind: "raw", summary: "x" }, usdOut: 150 }, policy).violations;
  expect(tx).toContain("over the per-transaction limit of $100");
});

test("approval for all refuses", () => {
  const afa: Change[] = [{ kind: "approval_for_all", token: NFT as any, from: SELF as any, to: STRANGER as any, approved: true }];
  const run = (p = policy) => checkRules({ ...base, to: NFT as any, changes: afa, intent: { kind: "raw", summary: "list nft" }, usdOut: 0 }, p).violations;
  expect(run()).toContain("gives control of all NFTs in a collection");
  expect(run(PolicySchema.parse({ ...POLICY, allow_approval_for_all: true }))).not.toContain("gives control of all NFTs in a collection");
  // revoking is fine
  const revoke: Change[] = [{ ...afa[0], approved: false }];
  expect(checkRules({ ...base, to: NFT as any, changes: revoke, intent: { kind: "raw", summary: "revoke" }, usdOut: 0 }, policy).violations).toEqual([]);
});

test("unlimited approvals refuse from uint96 max up, unless the policy allows them", () => {
  const appr = (amount: bigint): Change[] => [{ kind: "approval", token: USDC as any, from: SELF as any, to: STRANGER as any, amount }];
  const run = (amount: bigint, p = policy) =>
    checkRules({ ...base, changes: appr(amount), intent: { kind: "approve", summary: "approve" }, usdOut: 0 }, p).violations;
  expect(run(2n ** 256n - 1n)).toContain("grants an unlimited token approval");
  expect(run(2n ** 96n - 1n)).toContain("grants an unlimited token approval");
  expect(run(50_000_000n)).toEqual([]);
  expect(run(2n ** 256n - 1n, PolicySchema.parse({ ...POLICY, allow_unlimited_approvals: true }))).toEqual([]);
});

test("calls to the wallet itself or its guard module refuse", () => {
  for (const to of [SELF, MODULE, SELF.toUpperCase().replace("0X", "0x")]) {
    const v = checkRules({ ...base, to: to as any, changes: [], intent: { kind: "raw", summary: "x" }, usdOut: 0 }, policy).violations;
    expect(v).toContain("calls the wallet or its guard module directly");
  }
});

test("unpriced outflow is noted, and refused when it goes to an address not in the book", () => {
  const tok = "0x00000000000000000000000000000000000000ee";
  const out = (to: string): Change[] => [{ kind: "erc20", token: tok as any, from: SELF as any, to: to as any, amount: 1n }];
  const known = checkRules({ ...base, changes: out(MIRA), intent: { kind: "raw", summary: "x" }, usdOut: null }, policy);
  expect(known.violations).toEqual([]);
  expect(known.notes).toContain("includes a token without a reliable price");
  const unknown = checkRules({ ...base, changes: out(STRANGER), intent: { kind: "raw", summary: "x" }, usdOut: null }, policy);
  expect(unknown.violations).toContain("sends a token without a reliable price to an address not in your address book");
});

test("source matching is case-insensitive and covers agent:<profile> prefixes", () => {
  const p = PolicySchema.parse({ ...POLICY, refuse_sources: ["camera", "agent"] });
  for (const source of ["Camera", "agent:trader", "AGENT:x"])
    expect(checkRules({ ...base, changes: send(20_000_000n), source, usdOut: 20 }, p).violations.some((x) => x.startsWith("came from the"))).toBe(true);
});

test("bad numbers fail closed", () => {
  for (const usdOut of [NaN, -1, Infinity])
    expect(checkRules({ ...base, changes: send(20_000_000n), usdOut }, policy).violations).toContain("the outflow value could not be computed");
  for (const spentTodayUsd of [NaN, -5])
    expect(checkRules({ ...base, changes: send(20_000_000n), usdOut: 20, spentTodayUsd }, policy).violations).toContain("today's spending could not be read");
});

test("missing module or target throws (a thrown error is a refusal upstream)", () => {
  const { module: _m, ...noModule } = base;
  expect(() => checkRules({ ...(noModule as any), changes: [], usdOut: 0 }, policy)).toThrow(TypeError);
  const { to: _t, ...noTo } = base;
  expect(() => checkRules({ ...(noTo as any), changes: [], usdOut: 0 }, policy)).toThrow(TypeError);
});

test("policy parsing is strict: typo'd keys, bad addresses, negative caps and bad JSON throw", () => {
  expect(parsePolicy(JSON.stringify(POLICY)).max_usd_per_tx).toBe(100);
  expect(() => parsePolicy(JSON.stringify({ ...POLICY, allow_aproval_for_all: true }))).toThrow();
  expect(() => parsePolicy(JSON.stringify({ ...POLICY, address_book: { "mira.eth": "mira" } }))).toThrow();
  expect(() => parsePolicy(JSON.stringify({ ...POLICY, stablecoins: ["USDC"] }))).toThrow();
  expect(() => parsePolicy(JSON.stringify({ ...POLICY, max_usd_per_day: -1 }))).toThrow();
  expect(() => parsePolicy(JSON.stringify({ ...POLICY, auto_max_usd: "25" }))).toThrow();
  const { stablecoins: _s, ...missing } = POLICY;
  expect(() => parsePolicy(JSON.stringify(missing))).toThrow();
  expect(() => parsePolicy("not json")).toThrow();
});
