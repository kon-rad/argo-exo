import { parseUnits } from "viem";
import { z } from "zod";
import { isUnlimited } from "./explain";
import type { Change, Hex, TokenMeta } from "./types";

const ADDRESS = z.string().regex(/^0x[0-9a-fA-F]{40}$/, "must be a 0x address");
const USD = z.number().finite().nonnegative();

/** The wearer's rules, from the POLICY_JSON secret. Strict: an unknown key is most likely a typo of a safety
 *  switch (e.g. `allow_aproval_for_all`), so it throws rather than silently keeping the default. */
export const PolicySchema = z.object({
  address_book: z.record(ADDRESS, z.string()),
  max_usd_per_tx: USD, max_usd_per_day: USD, auto_max_usd: USD,
  allow_unlimited_approvals: z.boolean(), allow_approval_for_all: z.boolean(),
  refuse_sources: z.array(z.string()),
  require_known_recipient_over_usd: USD,
  stablecoins: z.array(ADDRESS),
}).strict();
export type Policy = z.infer<typeof PolicySchema>;

/** Parse the POLICY_JSON secret. Throws on bad JSON or a bad policy; the caller treats a throw as a refusal. */
export const parsePolicy = (json: string): Policy => PolicySchema.parse(JSON.parse(json));

/** §4.3 of the architecture. `token` is a 0x address or "ETH" (absent = ETH); `amount` is a decimal string in
 *  whole token units ("20", "0.5"). */
export type Intent = { kind: string; summary: string; token?: string; amount?: string; to?: string };

const lc = (s: string) => String(s).toLowerCase();
const lowerKeys = <V>(o: Record<string, V>) => Object.fromEntries(Object.entries(o ?? {}).map(([k, v]) => [lc(k), v]));
const fungible = (c: Change) => c.kind === "native" || c.kind === "erc20";
/** Something leaving `self` (a payment or an NFT; approvals are permissions, not outflows). */
const isOutflow = (c: Change, me: string) => lc(c.from) === me && lc(c.to) !== me && c.kind !== "approval" && c.kind !== "approval_for_all";

/** Integer micro-dollars per ETH, rounded up. */
const microUsd = (ethUsd: number) => BigInt(Math.ceil(ethUsd * 1e6));
const ceilDiv = (a: bigint, b: bigint) => (a + b - 1n) / b;

/** USD value of everything leaving `self`, in dollars rounded UP to the cent (so a cap is never passed by
 *  rounding). Exact bigint arithmetic; only the returned estimate is a float. Stablecoins are priced at $1.
 *  null — "unpriced" — when any outflow is an NFT, a token that is not a listed stablecoin with known decimals,
 *  or ETH while the price is not a positive finite number. A null is never auto-eligible. */
export function usdOutflow(changes: Change[], self: Hex, meta: TokenMeta, ethUsd: number, stablecoins: string[]): number | null {
  const me = lc(self), metaL = lowerKeys(meta), stable = new Set((stablecoins ?? []).map(lc));
  let cents = 0n;
  for (const c of changes) {
    if (!isOutflow(c, me)) continue;
    if (!fungible(c) || typeof c.amount !== "bigint") return null;
    if (c.token === "ETH") {
      if (!Number.isFinite(ethUsd) || ethUsd <= 0) return null;
      cents += ceilDiv(c.amount * microUsd(ethUsd), 10n ** 22n); // amount / 1e18 * micro / 1e6 * 100
    } else {
      const m = metaL[lc(c.token)];
      if (!stable.has(lc(c.token)) || !m || !Number.isInteger(m.decimals) || m.decimals < 0) return null;
      cents += ceilDiv(c.amount * 100n, 10n ** BigInt(m.decimals));
    }
  }
  return Number(cents) / 100;
}

export type RulesInput = {
  changes: Change[]; self: Hex; source: string; intent: Intent; usdOut: number | null; spentTodayUsd: number; meta: TokenMeta;
  /** The transaction's target, and the ExoModule address. ExoModule reverts an execute aimed at the Safe or itself,
   *  so the Guardian refuses it first rather than approve a hash that can never run (or could, after a redeploy). */
  to: Hex; module: Hex;
};

const AMOUNT = /^(\d+)(?:\.(\d+))?$/;

/** Does the simulated outcome match a `send` intent exactly: one payment from self, of exactly that token and
 *  amount (bigint, no rounding), to exactly that address, and nothing else leaving or granted? */
function matchesSend(i: RulesInput, me: string): boolean {
  const { to, amount } = i.intent;
  if (typeof to !== "string" || typeof amount !== "string") return false;
  const tok = i.intent.token === undefined ? "eth" : lc(i.intent.token);
  const dec = tok === "eth" ? 18 : lowerKeys(i.meta)[tok]?.decimals;
  const m = AMOUNT.exec(amount.trim());
  if (dec === undefined || !m || (m[2]?.length ?? 0) > dec) return false; // unknown decimals / sub-unit amounts can't be checked
  const want = parseUnits(amount.trim(), dec);
  const mine = i.changes.filter((c) => lc(c.from) === me && lc(c.to) !== me); // every outflow and every grant
  if (mine.length !== 1) return false;
  const c = mine[0];
  return fungible(c) && lc(c.to) === lc(to) && lc(c.token) === tok && c.amount === want;
}

/** The wearer's rules, judged on the simulated `changes` (full addresses, exact bigint amounts), never on the
 *  English explanation. Any violation means refuse. A throw (bad input) must be treated as a refusal. */
export function checkRules(i: RulesInput, p: Policy): { violations: string[]; notes: string[] } {
  if (!i || typeof i.self !== "string" || typeof i.to !== "string" || typeof i.module !== "string" || !Array.isArray(i.changes))
    throw new TypeError("checkRules: self, to, module and changes are required");
  const v: string[] = [], notes: string[] = [];
  const me = lc(i.self), book = lowerKeys(p.address_book);

  const src = lc(String(i.source ?? ""));
  const refused = new Set(p.refuse_sources.map(lc));
  if (refused.has(src) || refused.has(src.split(":")[0])) v.push(`came from the ${i.source}, which is never trusted to move funds`);

  if (lc(i.to) === me || lc(i.to) === lc(i.module)) v.push("calls the wallet or its guard module directly");

  for (const c of i.changes) {
    if (lc(c.from) !== me) continue;
    if (c.kind === "approval_for_all" && c.approved && !p.allow_approval_for_all) v.push("gives control of all NFTs in a collection");
    if (c.kind === "approval" && c.tokenId === undefined && isUnlimited(c.amount ?? 0n) && !p.allow_unlimited_approvals)
      v.push("grants an unlimited token approval");
  }

  const outs = i.changes.filter((c) => isOutflow(c, me));
  const unknownRecipient = outs.some((c) => !book[lc(c.to)]);
  if (i.usdOut !== null) {
    if (typeof i.usdOut !== "number" || !Number.isFinite(i.usdOut) || i.usdOut < 0) v.push("the outflow value could not be computed");
    else {
      if (i.usdOut > p.max_usd_per_tx) v.push(`over the per-transaction limit of $${p.max_usd_per_tx}`);
      if (typeof i.spentTodayUsd !== "number" || !Number.isFinite(i.spentTodayUsd) || i.spentTodayUsd < 0) v.push("today's spending could not be read");
      else if (i.spentTodayUsd + i.usdOut > p.max_usd_per_day) v.push(`over the daily limit of $${p.max_usd_per_day}`);
      if (unknownRecipient && i.usdOut > p.require_known_recipient_over_usd)
        v.push(`sends over $${p.require_known_recipient_over_usd} to an address not in your address book`);
    }
  } else {
    notes.push("includes a token without a reliable price");
    // The caps cannot be checked on an unpriced outflow; at least never send it to a stranger.
    if (unknownRecipient) v.push("sends a token without a reliable price to an address not in your address book");
  }

  if (i.intent?.kind === "send" && !matchesSend(i, me)) v.push("does not match what you asked for");
  return { violations: v, notes };
}
