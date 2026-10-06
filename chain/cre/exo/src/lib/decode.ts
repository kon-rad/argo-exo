import { decodeFunctionData, parseAbi, toFunctionSelector } from "viem";
import type { Hex } from "./types";

// ERC-20 and ERC-721 share the approve / transferFrom selectors, so that argument is named for both readings.
const ABI = parseAbi([
  "function transfer(address to, uint256 amount)",
  "function approve(address spender, uint256 amountOrTokenId)",
  "function transferFrom(address from, address to, uint256 amountOrTokenId)",
  "function increaseAllowance(address spender, uint256 addedValue)",
  "function setApprovalForAll(address operator, bool approved)",
  "function safeTransferFrom(address from, address to, uint256 tokenId)",
  "function safeTransferFrom(address from, address to, uint256 tokenId, bytes data)",
]);
const BY_SELECTOR = new Map(ABI.map((f) => [toFunctionSelector(f), f]));

export type Decoded = { name: string; known: boolean; args: Record<string, string> };

/** Decode calldata against the small set of token functions the Guardian understands. Anything else, including a
 *  known selector with malformed arguments, is `{ name: "unknown 0x<selector>", known: false }`. */
export function decodeCall(data: Hex): Decoded {
  const selector = typeof data === "string" ? data.slice(0, 10).toLowerCase() : "0x";
  const unknown = { name: `unknown ${selector}`, known: false, args: {} };
  const item = BY_SELECTOR.get(selector as Hex);
  if (!item || !/^0x[0-9a-f]*$/.test(data)) return unknown;
  try {
    const d = decodeFunctionData({ abi: [item], data });
    const args = (d.args ?? []) as readonly unknown[];
    const str = (type: string, v: unknown) => (type === "address" ? String(v).toLowerCase() : String(v)); // lowercase like Change
    return { name: item.name, known: true, args: Object.fromEntries(item.inputs.map((p, i) => [p.name!, str(p.type, args[i])])) };
  } catch {
    return unknown;
  }
}
