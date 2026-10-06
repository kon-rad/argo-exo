import { toEventSelector } from "viem";
import type { CallFrame, Change, Hex, Log, TraceResult } from "./types";

// Computed, not pasted: a mistyped topic would silently route every real log of that kind into otherEvents.
const TRANSFER = toEventSelector("Transfer(address,address,uint256)");
const APPROVAL = toEventSelector("Approval(address,address,uint256)");
const APPROVAL_FOR_ALL = toEventSelector("ApprovalForAll(address,address,bool)");
/** eth_simulateV1 `traceTransfers` reports native moves as ERC-20-shaped Transfer logs from this address. */
const NATIVE_PSEUDO_TOKEN = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";

const WORD = /^0x[0-9a-f]{64}$/;
const ADDR_WORD = /^0x0{24}[0-9a-f]{40}$/;
const addr = (word: string) => ("0x" + word.slice(-40)) as Hex;
const big = (hex?: string) => (hex && hex !== "0x" ? BigInt(hex) : 0n); // invalid hex throws: callers fail closed
const lower = (s: string) => s.toLowerCase() as Hex;

/** Frames whose `value` does not leave the caller: DELEGATECALL and CALLCODE run foreign code in the caller's
 *  own context, so the value stays where it was. */
const NO_VALUE_MOVE = new Set(["DELEGATECALL", "CALLCODE", "STATICCALL"]);

/** Parse one log into a Change, or null when it is not a well-formed event this library models. */
function parseLog(l: Log): Change | null {
  const t = (l.topics ?? []).map((x) => String(x).toLowerCase());
  const data = String(l.data ?? "0x").toLowerCase();
  const token = lower(String(l.address));
  if (!t.length || !t.every((x) => WORD.test(x))) return null;
  const sig = t[0];
  if (sig === TRANSFER && t.length === 3 && ADDR_WORD.test(t[1]) && ADDR_WORD.test(t[2]) && WORD.test(data))
    return { kind: "erc20", token, from: addr(t[1]), to: addr(t[2]), amount: BigInt(data) };
  if (sig === TRANSFER && t.length === 4 && ADDR_WORD.test(t[1]) && ADDR_WORD.test(t[2]))
    return { kind: "erc721", token, from: addr(t[1]), to: addr(t[2]), tokenId: BigInt(t[3]) };
  if (sig === APPROVAL && t.length === 3 && ADDR_WORD.test(t[1]) && ADDR_WORD.test(t[2]) && WORD.test(data))
    return { kind: "approval", token, from: addr(t[1]), to: addr(t[2]), amount: BigInt(data) };
  if (sig === APPROVAL && t.length === 4 && ADDR_WORD.test(t[1]) && ADDR_WORD.test(t[2]))
    return { kind: "approval", token, from: addr(t[1]), to: addr(t[2]), tokenId: BigInt(t[3]) };
  if (sig === APPROVAL_FOR_ALL && t.length === 3 && ADDR_WORD.test(t[1]) && ADDR_WORD.test(t[2]) && WORD.test(data)) {
    const v = BigInt(data);
    if (v === 0n || v === 1n) return { kind: "approval_for_all", token, from: addr(t[1]), to: addr(t[2]), approved: v === 1n };
  }
  return null;
}

/** Asset changes from a callTracer trace (withLog). A reverted frame changes nothing, so its value, logs and
 *  children are skipped; a reverted root means the transaction does nothing and reports reverted=true. Every log
 *  that is not a well-formed Transfer / Approval / ApprovalForAll, and every value move with no destination,
 *  increments otherEvents — the caller must treat otherEvents > 0 as "not fully understood". */
export function changesFromCallTrace(root: CallFrame): TraceResult {
  if (!root || typeof root !== "object") return { changes: [], reverted: true, otherEvents: 0 };
  if (root.error) return { changes: [], reverted: true, otherEvents: 0 };
  const changes: Change[] = [];
  let otherEvents = 0;
  const walk = (f: CallFrame) => {
    if (f.error) return;
    const type = String(f.type ?? "").toUpperCase();
    const value = big(f.value);
    if (value > 0n && !NO_VALUE_MOVE.has(type)) {
      if (f.to) changes.push({ kind: "native", token: "ETH", from: lower(f.from), to: lower(f.to), amount: value });
      else otherEvents++;
    }
    for (const l of f.logs ?? []) {
      const c = parseLog(l);
      if (c) changes.push(c); else otherEvents++;
    }
    for (const c of f.calls ?? []) walk(c);
  };
  walk(root);
  return { changes, reverted: false, otherEvents };
}

/** Asset changes from an eth_simulateV1 result (array of blocks, each with `calls`), run with
 *  `traceTransfers: true`. Reads every call of every block. Any call without status 0x1, or an empty /
 *  malformed result, makes the whole result reverted=true. Logs of a failed call are not counted. */
export function changesFromSimulateV1(result: any): TraceResult {
  const blocks = Array.isArray(result) ? result : [];
  const calls = blocks.flatMap((b: any) => (Array.isArray(b?.calls) ? b.calls : []));
  if (!calls.length) return { changes: [], reverted: true, otherEvents: 0 };
  const changes: Change[] = [];
  let otherEvents = 0, reverted = false;
  for (const call of calls) {
    if (call?.status !== "0x1" || call?.error) { reverted = true; continue; }
    for (const l of call.logs ?? []) {
      const c = parseLog(l);
      if (c && c.kind === "erc20" && c.token === NATIVE_PSEUDO_TOKEN) changes.push({ ...c, kind: "native", token: "ETH" });
      else if (c && c.token !== NATIVE_PSEUDO_TOKEN) changes.push(c);
      else otherEvents++;
    }
  }
  return { changes, reverted, otherEvents };
}
