import { expect, test } from "bun:test";
import v from "../../../test-vectors/approval-hash.json";
import { approvalHash } from "../src/lib/hash";

test("matches the cast vector", () => {
  expect(approvalHash(BigInt(v.chain_id), v.module as any, v.to as any, BigInt(v.value), v.data as any, v.salt as any)).toBe(v.hash as any);
});

test("address case does not change the hash", () => {
  expect(approvalHash(BigInt(v.chain_id), v.module.toLowerCase() as any, v.to.toLowerCase() as any, BigInt(v.value), v.data as any, v.salt as any))
    .toBe(v.hash as any);
});

test("a short salt is refused, not padded", () => {
  expect(() => approvalHash(1n, v.module as any, v.to as any, 0n, "0x", "0x01")).toThrow();
});
