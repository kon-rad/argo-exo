import { decodeCall } from "./decode";
import type { Judgement, Risk } from "./judge";
import { exposure } from "./rules";
import type { Change, Hex } from "./types";

const RANK: Record<Risk, number> = { low: 0, medium: 1, high: 2 };
/** Own keys only: `"toString" in RANK` is true, and RANK["__proto__"] is an object, not a rank. */
const isRisk = (r: unknown): r is Risk => typeof r === "string" && Object.hasOwn(RANK, r);

export type Decision = { verdict: "approve" | "refuse"; risk: Risk; auto_eligible: boolean; reasons: string[] };

export type DecideArgs = {
  reverted: boolean;            // from the trace
  known: boolean;               // understood(tx.data): a known token call or a plain ETH send
  otherEvents: number;          // from the trace: events / value moves the library could not read
  violations: readonly string[];
  judges: readonly Judgement[]; // two independent reviewers
  usdOut: number | null;
  autoMaxUsd: number;
  // recipientsKnown / hasApprovals are derived here from these, never taken from the caller.
  changes: readonly Change[];
  self: Hex;
  addressBook: Record<string, string>;
};

/** The calldata is something the Guardian can reason about: empty (a plain ETH send) or a known token function.
 *  An unknown function is refused outright — its changes list may not be the whole story. */
export const understood = (data: Hex): boolean => data === "0x" || (typeof data === "string" && data.length >= 10 && decodeCall(data).known);

const refusal = (reasons: string[]): Decision => ({ verdict: "refuse", risk: "high", auto_eligible: false, reasons });

/** Final verdict. Refuses (risk high) on any violation, a revert, an unknown function, an unread event, fewer than
 *  two well-formed judgements, or a `high` from either judge. Otherwise approves at the higher of the two judges'
 *  risks. auto_eligible only when: approve, risk low, priced outflow <= autoMaxUsd, known recipients, no approvals. */
export function decide(a: DecideArgs): Decision {
  if (!a || typeof a !== "object") return refusal(["the check got no input"]);
  if (!Array.isArray(a.changes) || typeof a.self !== "string" || !a.addressBook || typeof a.addressBook !== "object")
    return refusal(["the check got no changes, wallet or address book"]);
  const reasons: string[] = [];
  if (!Array.isArray(a.violations) || !a.violations.every((x) => typeof x === "string")) reasons.push("the rule check could not be read");
  else reasons.push(...a.violations);
  if (a.reverted !== false) reasons.push("the transaction would fail");
  if (a.known !== true) reasons.push("calls a function this check does not understand");
  if (typeof a.otherEvents !== "number" || !(a.otherEvents === 0)) reasons.push("has effects this check could not read");

  const judges = Array.isArray(a.judges) ? a.judges : [];
  const valid = judges.filter((j): j is Judgement => !!j && typeof j === "object" && typeof j.risk === "string" && isRisk(j.risk));
  if (judges.length < 2) reasons.push("needs two independent reviews");
  if (valid.length !== judges.length) reasons.push("a reviewer's answer could not be read");
  const worst = valid.reduce<Risk>((w, j) => (RANK[j.risk] > RANK[w] ? j.risk : w), "low");
  if (worst === "high") reasons.push(...valid.filter((j) => j.risk === "high").map((j) => `reviewer: ${j.reason}`));

  if (reasons.length) return refusal(reasons);
  const { recipientsKnown, hasApprovals } = exposure(a.changes as Change[], a.self, a.addressBook);
  const auto_eligible = worst === "low" && typeof a.usdOut === "number" && Number.isFinite(a.usdOut) && a.usdOut >= 0
    && Number.isFinite(a.autoMaxUsd) && a.usdOut <= a.autoMaxUsd && recipientsKnown && !hasApprovals;
  return { verdict: "approve", risk: worst, auto_eligible, reasons: valid.map((j) => j.reason) };
}

/** Run the whole guard pipeline; any thrown error (RPC, parse, a TypeError from a lib) becomes a refusal. */
export function failClosed(run: () => Decision): Decision {
  try {
    return run();
  } catch (e) {
    return refusal([`the check failed: ${e instanceof Error ? e.message : String(e)}`.slice(0, 300)]);
  }
}
