export type Hex = `0x${string}`;

/** One asset movement or permission grant. Addresses are lowercase. Amounts are always bigint. */
export type Change = {
  kind: "native" | "erc20" | "erc721" | "approval" | "approval_for_all";
  token: Hex | "ETH";
  from: Hex;          // payer / owner
  to: Hex;            // payee / spender / operator
  amount?: bigint;    // native, erc20, erc20 approval
  tokenId?: bigint;   // erc721, erc721 single-token approval
  approved?: boolean; // approval_for_all
};

export type TokenMeta = Record<string, { symbol: string; decimals: number }>;

/** geth `callTracer` frame, with `{ withLog: true }`. */
export type CallFrame = {
  type: string; from: string; to?: string; value?: string; input?: string; output?: string;
  error?: string; revertReason?: string; gas?: string; gasUsed?: string;
  calls?: CallFrame[]; logs?: Log[];
};

export type Log = { address: string; topics: string[]; data: string; position?: string };

export type TraceResult = { changes: Change[]; reverted: boolean; otherEvents: number };
