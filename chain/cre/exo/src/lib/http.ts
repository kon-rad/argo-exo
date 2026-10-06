import { rpcRequest, rpcUrl } from "@argo-exo/nownodes";
import { decodeAbiParameters, encodeFunctionData, parseAbi } from "viem";
import type { RpcChain } from "./config";
import { parseJudge, type Judgement } from "./judge";
import type { Hex } from "./types";

/** One outbound JSON POST, before it becomes a CRE HTTP request. `body` is the JSON text. */
export type HttpRequest = { url: string; headers: Record<string, string>; body: string; timeout: string };
export type HttpReply = { status: number; body: string };

/** CRE `RequestJson`: base64 body, `multiHeaders` (the documented, non-deprecated header field), a duration timeout. */
export function toSdkRequest(r: HttpRequest) {
  const headers = { "Content-Type": "application/json", ...r.headers };
  return {
    url: r.url,
    method: "POST",
    body: Buffer.from(r.body, "utf8").toString("base64"),
    multiHeaders: Object.fromEntries(Object.entries(headers).map(([k, v]) => [k, { values: [v] }])),
    timeout: r.timeout,
  };
}

/** A JSON-RPC call to NOWNodes, authenticated with the `api-key` header. */
export const rpcHttpRequest = (chain: RpcChain, apiKey: string, method: string, params: unknown[]): HttpRequest =>
  ({ url: rpcUrl(chain), headers: { "api-key": apiKey }, body: rpcRequest(method, params).body, timeout: "10s" });

export const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";
/** One reviewer call. CRE caps an HTTP request at 10 s, so the judges must be fast models. */
export const openRouterRequest = (model: string, prompt: string, apiKey: string): HttpRequest => ({
  url: OPENROUTER_URL,
  headers: { Authorization: `Bearer ${apiKey}` },
  body: JSON.stringify({ model, temperature: 0, max_tokens: 300, messages: [{ role: "user", content: prompt }] }),
  timeout: "10s",
});

/** The reviewer's verdict from an OpenRouter reply. Any non-2xx status, unparseable body or missing content reads as
 *  high risk (parseJudge's fail-closed answer). */
export function judgeFromReply(r: HttpReply): Judgement {
  if (!r || typeof r.status !== "number" || r.status < 200 || r.status > 299) return parseJudge("");
  try {
    const content = JSON.parse(r.body)?.choices?.[0]?.message?.content;
    return parseJudge(typeof content === "string" ? content : "");
  } catch {
    return parseJudge("");
  }
}

const FEED_ABI = parseAbi(["function latestRoundData() view returns (uint80, int256, uint256, uint256, uint80)"]);
/** eth_call params for the Chainlink ETH/USD aggregator's latestRoundData. */
export const latestRoundDataParams = (feed: Hex): unknown[] =>
  [{ to: feed, data: encodeFunctionData({ abi: FEED_ABI, functionName: "latestRoundData" }) }, "latest"];

/** ETH/USD mainnet feed: 8 decimals, 1 h heartbeat. Older than two heartbeats = stale. */
export const FEED_DECIMALS = 8;
export const PRICE_MAX_AGE_SECONDS = 7200;

/** USD per ETH from a latestRoundData result, or NaN when the answer is not positive, has no timestamp, is stale, or
 *  claims to be from the future. NaN makes every ETH outflow unpriced (see usdOutflow), never cheap. */
export function parseEthUsd(result: Hex, nowSeconds: number): number {
  try {
    const [, answer, , updatedAt] = decodeAbiParameters(
      [{ type: "uint80" }, { type: "int256" }, { type: "uint256" }, { type: "uint256" }, { type: "uint80" }], result);
    const now = BigInt(Math.floor(nowSeconds));
    if (answer <= 0n || updatedAt === 0n || updatedAt > now + 60n || now - updatedAt > BigInt(PRICE_MAX_AGE_SECONDS)) return NaN;
    return Number(answer) / 10 ** FEED_DECIMALS;
  } catch {
    return NaN;
  }
}
