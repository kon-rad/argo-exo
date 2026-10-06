import { z } from "zod";
import { MAX_APPROVAL_TTL_SECONDS } from "./payload";
import type { Hex } from "./types";

export const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
export const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";
const Address = z.string().regex(ADDRESS_RE, "must be a 0x address");

/** The vendor of an OpenRouter model id ("anthropic/claude-…" → "anthropic"). */
export const vendorOf = (model: string) => model.split("/")[0]?.trim().toLowerCase() ?? "";

/** config.<target>.json. Strict, like the policy: a typo'd key (e.g. `ttlSecond`) throws instead of defaulting. */
export const ConfigSchema = z.object({
  chainSelectorName: z.string().min(1),
  chainId: z.number().int().positive(),
  module: Address,
  safe: Address,
  gasLimit: z.string().regex(/^[1-9]\d*$/, "must be a decimal gas amount"),
  // ExoModule reverts an approval that outlives block.timestamp + 1 h.
  ttlSeconds: z.number().int().min(1).max(MAX_APPROVAL_TTL_SECONDS),
  simMethod: z.enum(["debug_traceCall", "eth_simulateV1"]),
  ethUsdFeed: Address,
  // Two independent reviewers: exactly two OpenRouter model ids, from two different vendors.
  judges: z.array(z.string().regex(/^[a-z0-9-]+\/\S+$/i, "must be an OpenRouter model id (vendor/model)")).length(2)
    .refine((j) => vendorOf(j[0]) !== vendorOf(j[1]), "the two judges must come from different vendors"),
  tokens: z.record(Address, z.object({ symbol: z.string(), decimals: z.number().int().min(0).max(36) }).strict()),
  debug: z.boolean(),
}).strict();
export type Config = z.infer<typeof ConfigSchema>;

/** NOWNodes chain name for a chain id; throws on a chain the Guardian has no RPC for. */
export type RpcChain = "ethereum" | "base" | "arbitrum" | "polygon";
const CHAINS: Record<number, RpcChain> = { 1: "ethereum", 8453: "base", 42161: "arbitrum", 137: "polygon" };
export function chainForId(chainId: number): RpcChain {
  const c = Object.hasOwn(CHAINS, chainId) ? CHAINS[chainId] : undefined;
  if (!c) throw new Error(`no RPC for chain ${chainId}`);
  return c;
}

export const isZeroAddress = (a: string) => a.toLowerCase() === ZERO_ADDRESS;
export const lower = (a: string) => a.toLowerCase() as Hex;
