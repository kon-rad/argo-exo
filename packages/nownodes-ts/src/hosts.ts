export type Chain = "ethereum" | "base" | "arbitrum" | "polygon";
export const HOSTS = {
  rpc: { ethereum: "eth.nownodes.io", base: "base.nownodes.io", arbitrum: "arbitrum.nownodes.io", polygon: "matic.nownodes.io" } as Record<Chain, string>,
  blockbook: { ethereum: "eth-blockbook.nownodes.io", base: "base-blockbook.nownodes.io", arbitrum: "arb-blockbook.nownodes.io", polygon: "maticbook.nownodes.io" } as Record<Chain, string>,
};
