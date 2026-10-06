import { HOSTS, type Chain } from "./hosts";

export const rpcUrl = (chain: Chain) => `https://${HOSTS.rpc[chain]}`;
export const rpcRequest = (method: string, params: unknown[], id = 1) =>
  ({ body: JSON.stringify({ jsonrpc: "2.0", id, method, params }) });

export function parseRpc<T>(status: number, bodyText: string): T {
  if (status !== 200) throw new Error(`rpc: HTTP ${status}`);
  let j: any;
  try { j = JSON.parse(bodyText); } catch { throw new Error("rpc: invalid JSON"); }
  if (j === null || typeof j !== "object" || Array.isArray(j)) throw new Error("rpc: malformed reply");
  if (j.error) throw new Error(`rpc: ${j.error.message ?? JSON.stringify(j.error)}`);
  return j.result as T;
}

type Tx = { from: string; to: string; value: bigint; data: string };
const q = (n: bigint) => {
  if (n < 0n) throw new Error("rpc: negative quantity");
  return "0x" + n.toString(16);
};
const call = (tx: Tx) => ({ from: tx.from, to: tx.to, value: q(tx.value), data: tx.data });

export const traceCallParams = (tx: Tx, block = "latest"): unknown[] =>
  [call(tx), block, { tracer: "callTracer", tracerConfig: { withLog: true } }];

export const simulateV1Params = (tx: Tx, block = "latest"): unknown[] =>
  [{ blockStateCalls: [{ calls: [call(tx)] }], traceTransfers: true, validation: false }, block];
