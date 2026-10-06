import { formatUnits } from "viem";
import type { Change, Hex, TokenMeta, TraceResult } from "./types";

/** Allowances at or above uint96 max are "unlimited": it covers max uint256, Permit2's uint160 max and the
 *  uint96 max that COMP/UNI-style tokens treat as infinite. Erring low only makes the warning louder. */
export const UNLIMITED_ALLOWANCE = 2n ** 96n - 1n;
export const isUnlimited = (amount: bigint) => amount >= UNLIMITED_ALLOWANCE;

const ZERO = "0x0000000000000000000000000000000000000000";
const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`;
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** Control characters, line/paragraph separators, zero-width and bidi overrides: none belong in a label, and
 *  any of them could forge structure in the judge prompt or hide text from the wearer. */
const UNSAFE = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]/g;
export const SYMBOL_MAX = 16;
export const LABEL_MAX = 32;
/** A token symbol or address-book label made safe to show and to quote to an LLM: unsafe characters removed,
 *  whitespace collapsed, capped. Returns "" when nothing is left (callers fall back to the address). */
export const cleanLabel = (s: unknown, max = LABEL_MAX): string =>
  typeof s === "string" ? s.replace(UNSAFE, " ").replace(/\s+/g, " ").trim().slice(0, max) : "";

/** The trace status from changesFromCallTrace / changesFromSimulateV1. Required: without it explain cannot know
 *  whether the changes list is the whole story. */
export type TraceStatus = Pick<TraceResult, "otherEvents" | "reverted">;

/** Plain-English account of what the changes do to `self`. Changes that do not touch `self` are not narrated.
 *  "Nothing else changes." is only claimed when nothing risky is granted and every event was understood. */
export function explain(changes: Change[], self: Hex, meta: TokenMeta, book: Record<string, string>, status: TraceStatus): string {
  // Runtime check too: a JS caller (or an `as any`) that drops the status must not get a reassuring sentence.
  if (!status || typeof status.reverted !== "boolean" || typeof status.otherEvents !== "number" || !(status.otherEvents >= 0))
    throw new TypeError("explain: trace status { otherEvents, reverted } is required");
  if (status.reverted) return "This transaction reverts: it would fail and change nothing.";
  const me = self.toLowerCase();
  const lowerKeys = <V>(o: Record<string, V>) => Object.fromEntries(Object.entries(o ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
  const metaL = lowerKeys(meta), bookL = lowerKeys(book); // checksummed keys must resolve too
  const who = (a: string) => cleanLabel(Object.hasOwn(bookL, a.toLowerCase()) ? bookL[a.toLowerCase()] : "") || short(a);
  const info = (t: string) => {
    if (t === "ETH") return { symbol: "ETH", decimals: 18 };
    const m = Object.hasOwn(metaL, t.toLowerCase()) ? metaL[t.toLowerCase()] : undefined;
    return m ? { decimals: m.decimals, symbol: cleanLabel(m.symbol, SYMBOL_MAX) || `token ${short(t)}` } : undefined;
  };
  const sym = (t: string) => info(t)?.symbol ?? `token ${short(t)}`;
  // Unknown decimals: show raw units rather than guess 18 and understate a 6-decimal amount by 10^12.
  const value = (c: Change) => {
    const m = info(c.token), a = c.amount ?? 0n;
    return m ? `${formatUnits(a, m.decimals)} ${m.symbol}` : `${a} raw units of ${sym(c.token)}`;
  };
  const lines: string[] = [];
  let risky = false;
  for (const c of changes) {
    const out = c.from.toLowerCase() === me, inn = c.to.toLowerCase() === me;
    if (out === inn) continue; // not ours, or a self-transfer
    const fungible = c.kind === "native" || c.kind === "erc20";
    if (fungible && out) lines.push(`You pay ${value(c)} to ${who(c.to)}.`);
    else if (fungible && inn) lines.push(`You receive ${value(c)} from ${who(c.from)}.`);
    else if (c.kind === "erc721" && out) lines.push(`You give NFT #${c.tokenId} of collection ${who(c.token)} to ${who(c.to)}.`);
    else if (c.kind === "erc721" && inn) lines.push(`You receive NFT #${c.tokenId} of collection ${who(c.token)}.`);
    else if (c.kind === "approval" && out && c.tokenId !== undefined) {
      if (c.to.toLowerCase() === ZERO) lines.push(`The approval on your NFT #${c.tokenId} of collection ${who(c.token)} is cleared.`);
      else { risky = true; lines.push(`You let ${who(c.to)} move your NFT #${c.tokenId} of collection ${who(c.token)}.`); }
    } else if (c.kind === "approval" && out) {
      const a = c.amount ?? 0n;
      if (a === 0n) lines.push(`You remove ${who(c.to)}'s permission to spend your ${sym(c.token)}.`);
      else {
        risky = true;
        lines.push(isUnlimited(a) ? `You let ${who(c.to)} spend an unlimited amount of your ${sym(c.token)}.`
          : `You let ${who(c.to)} spend up to ${value(c)}.`);
      }
    } else if (c.kind === "approval_for_all" && out) {
      if (c.approved) { risky = true; lines.push(`You give ${who(c.to)} control of all your NFTs in collection ${who(c.token)}.`); }
      else lines.push(`You remove ${who(c.to)}'s control of all your NFTs in collection ${who(c.token)}.`);
    }
  }
  const other = status.otherEvents;
  const unread = other > 0
    ? ` Warning: ${plural(other, "effect")} of this transaction couldn't be read, so more may change than this says.` : "";
  if (!lines.length) return other > 0 ? `Nothing changes in your wallet that this check can read.${unread}` : "Nothing changes in your wallet.";
  if (risky || other > 0) return `${lines.join(" ")}${unread}`;
  return `${lines.join(" ")} Nothing else changes.`;
}
