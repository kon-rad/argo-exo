import { z } from "zod";
import { ADDRESS_RE } from "./config";
import type { Intent } from "./rules";
import type { Hex } from "./types";

const Address = z.string().regex(ADDRESS_RE, "must be a 0x address").transform((a) => a.toLowerCase() as Hex);
const UINT256_MAX = 2n ** 256n - 1n;

/** §4.3 of the architecture, plus `requested_at` (informational only: the approval expiry comes from the workflow's
 *  own clock) and `context.spent_today_usd`. Unknown keys are dropped; every field the decision reads is checked. */
export const GuardRequestSchema = z.object({
  proposal_id: z.string().min(1).max(100),
  source: z.string().max(100),
  intent: z.object({
    kind: z.string().max(32),
    summary: z.string().max(500),
    token: z.string().max(64).optional(),
    amount: z.string().max(80).optional(),
    to: z.string().max(64).optional(),
  }),
  requested_at: z.number().optional(),
  context: z.object({ spent_today_usd: z.number().finite().nonnegative().optional() }).optional(),
  tx: z.object({
    chain_id: z.number().int().positive(),
    to: Address,
    value: z.string().regex(/^\d{1,78}$/, "must be a decimal wei amount")
      .transform((v) => BigInt(v)).refine((v) => v <= UINT256_MAX, "exceeds uint256"),
    data: z.string().regex(/^0x([0-9a-fA-F]{2})*$/, "must be 0x-prefixed hex bytes").transform((d) => d.toLowerCase() as Hex),
    salt: z.string().regex(/^0x[0-9a-fA-F]{64}$/, "must be 32 bytes of hex").transform((s) => s.toLowerCase() as Hex),
  }),
  from: Address,
});
export type GuardRequest = z.infer<typeof GuardRequestSchema> & { intent: Intent };

/** Parse the guard trigger input. Throws (→ refusal) on anything malformed. */
export function parseGuardRequest(input: unknown): GuardRequest {
  return GuardRequestSchema.parse(input) as GuardRequest;
}

/** Freeze is safe in every direction (it only stops execution), so any input freezes; the reason is cleaned and capped. */
export function parseFreezeReason(input: unknown): string {
  const r = input && typeof input === "object" && !Array.isArray(input) ? (input as { reason?: unknown }).reason : undefined;
  const s = typeof r === "string" ? r.replace(/[\u0000-\u001f\u007f-\u009f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 200) : "";
  return s || "panic";
}
