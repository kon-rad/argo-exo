import { encodeAbiParameters, keccak256, parseAbiParameters, toHex } from "viem";
import type { Hex } from "./types";

/** ExoModule.MAX_APPROVAL_TTL: an approve whose expiresAt is beyond block.timestamp + 1 h reverts onchain. */
export const MAX_APPROVAL_TTL_SECONDS = 3600;
/** Any Unix time in seconds is below this (year ~5138); a millisecond timestamp (~1.8e12 today) is above it. */
const SECONDS_CEILING = 100_000_000_000n;

/** Expiry for an approve report: now + ttl, in SECONDS. `nowSeconds` should be the workflow's own clock, not a
 *  timestamp from the request. Throws on a millisecond clock or a TTL outside 1..3600 s. */
export function approvalExpiry(nowSeconds: number | bigint, ttlSeconds: number): bigint {
  if (!Number.isInteger(ttlSeconds) || ttlSeconds < 1 || ttlSeconds > MAX_APPROVAL_TTL_SECONDS)
    throw new RangeError(`approvalExpiry: ttl must be 1..${MAX_APPROVAL_TTL_SECONDS} seconds`);
  if (typeof nowSeconds === "number" && !Number.isInteger(nowSeconds)) throw new RangeError("approvalExpiry: now must be whole seconds");
  const now = BigInt(nowSeconds);
  if (now <= 0n || now >= SECONDS_CEILING) throw new RangeError("approvalExpiry: now must be a Unix time in seconds");
  return now + BigInt(ttlSeconds);
}

/** abi.encode(uint8 kind, bytes32 txHash, uint64 expiresAt, bytes32 reasonHash); kinds 1 approve, 2 refuse,
 *  3 freeze. Throws on anything ExoModule would misread, including an approve with a zero or millisecond expiry. */
export function reportPayload(kind: 1 | 2 | 3, txHash: Hex, expiresAt: bigint, reason: string): Hex {
  if (kind !== 1 && kind !== 2 && kind !== 3) throw new RangeError("reportPayload: kind must be 1, 2 or 3");
  if (typeof txHash !== "string" || !/^0x[0-9a-fA-F]{64}$/.test(txHash)) throw new TypeError("reportPayload: txHash must be bytes32 hex");
  if (typeof expiresAt !== "bigint" || expiresAt < 0n || expiresAt >= 2n ** 64n) throw new RangeError("reportPayload: expiresAt must be a uint64 bigint");
  if (kind === 1 && (expiresAt === 0n || expiresAt >= SECONDS_CEILING)) throw new RangeError("reportPayload: approve expiry must be Unix seconds");
  return encodeAbiParameters(parseAbiParameters("uint8, bytes32, uint64, bytes32"), [kind, txHash, expiresAt, keccak256(toHex(String(reason)))]);
}
