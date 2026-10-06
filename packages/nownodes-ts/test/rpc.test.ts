import { expect, test } from "bun:test";
import { HOSTS, parseRpc, rpcRequest, rpcUrl, traceCallParams, simulateV1Params } from "../src";

test("hosts and url", () => {
  expect(HOSTS.blockbook.polygon).toBe("maticbook.nownodes.io");
  expect(rpcUrl("base")).toBe("https://base.nownodes.io");
});

test("hosts mirror the Python package", async () => {
  const py = await Bun.file(new URL("../../nownodes-py/exo_nownodes/hosts.py", import.meta.url)).text();
  for (const host of [...Object.values(HOSTS.rpc), ...Object.values(HOSTS.blockbook)]) expect(py).toContain(`"${host}"`);
  for (const chain of Object.keys(HOSTS.rpc)) expect(py).toContain(`"${chain}"`);
});

test("request body", () => {
  expect(JSON.parse(rpcRequest("eth_chainId", [], 7).body)).toEqual({ jsonrpc: "2.0", id: 7, method: "eth_chainId", params: [] });
});

test("parse result and errors", () => {
  expect(parseRpc<string>(200, '{"result":"0x1"}')).toBe("0x1");
  expect(() => parseRpc(200, '{"error":{"message":"execution reverted"}}')).toThrow("rpc: execution reverted");
  expect(() => parseRpc(429, "")).toThrow("rpc: HTTP 429");
  expect(() => parseRpc(200, "<html>")).toThrow("rpc: invalid JSON");
  expect(() => parseRpc(200, "null")).toThrow("rpc: malformed reply");
});

test("trace params encode value as hex quantity", () => {
  const p = traceCallParams({ from: "0x1", to: "0x2", value: 10n ** 18n, data: "0x" });
  expect(p[0]).toEqual({ from: "0x1", to: "0x2", value: "0xde0b6b3a7640000", data: "0x" });
  expect(p[2]).toEqual({ tracer: "callTracer", tracerConfig: { withLog: true } });
  expect((simulateV1Params({ from: "0x1", to: "0x2", value: 0n, data: "0x" })[0] as any).traceTransfers).toBe(true);
});

test("negative value rejected", () => {
  expect(() => traceCallParams({ from: "0x1", to: "0x2", value: -1n, data: "0x" })).toThrow();
});
