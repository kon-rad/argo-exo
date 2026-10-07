import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import * as w from '../static/wallet.mjs';

const SEL = '0x12345678';
const PREORDER_JSON = JSON.parse(readFileSync(new URL('../static/preorder.json', import.meta.url), 'utf8'));
const onPath = () => { try { execFileSync('cast', ['--version'], { stdio: 'ignore' }); return 'cast'; } catch { return null; } };
const CAST = [process.env.CAST, `${homedir()}/.foundry/bin/cast`].find((p) => p && existsSync(p)) || onPath();
const cast = (...args) => execFileSync(CAST, args, { encoding: 'utf8' }).trim();

test('word and address padding', () => {
  assert.equal(w.word(1n), '0'.repeat(63) + '1');
  assert.equal(w.addrWord('0x00000000000000000000000000000000000000AB'), '0'.repeat(62) + 'ab');
  assert.throws(() => w.addrWord('0x123'));
  assert.throws(() => w.word(-1n));
  assert.throws(() => w.word(1n << 256n));
  assert.equal(w.word((1n << 256n) - 1n), 'f'.repeat(64));
});

test('preorder calldata', () => {
  assert.equal(w.preorderData(SEL, 1, 499000000n), SEL + w.word(1n) + w.word(499000000n));
  assert.throws(() => w.preorderData(SEL, 0, 1n));
  assert.throws(() => w.preorderData(SEL, 256, 1n));
  assert.throws(() => w.preorderData(SEL, '1', 1n));
});

test('approve calldata is for the exact amount', () => {
  const spender = '0x' + 'cd'.repeat(20);
  assert.equal(w.approveData(SEL, spender, 499000000n), SEL + '0'.repeat(24) + 'cd'.repeat(20) + w.word(499000000n));
});

test('permit typed data and signature split', () => {
  const td = w.permitTypedData({ name: 'USD Coin', version: '2', chainId: 8453, verifyingContract: '0xU', owner: '0xO', spender: '0xS', value: 5n, nonce: 0n, deadline: 9n });
  assert.equal(td.primaryType, 'Permit');
  assert.deepEqual(td.domain, { name: 'USD Coin', version: '2', chainId: 8453, verifyingContract: '0xU' });
  assert.equal(td.message.value, '5');
  const sig = '0x' + 'aa'.repeat(32) + 'bb'.repeat(32) + '1b';
  assert.deepEqual(w.splitSig(sig), { r: '0x' + 'aa'.repeat(32), s: '0x' + 'bb'.repeat(32), v: 27 });
  assert.equal(w.splitSig('0x' + 'aa'.repeat(32) + 'bb'.repeat(32) + '00').v, 27);   // some wallets return 0/1
  assert.equal(w.splitSig('0x' + 'aa'.repeat(32) + 'bb'.repeat(32) + '01').v, 28);
  assert.throws(() => w.splitSig('0x' + 'aa'.repeat(63)));                                // neither 64 nor 65 bytes
  assert.throws(() => w.splitSig('0x' + 'aa'.repeat(66)));
  assert.throws(() => w.splitSig('0x' + 'zz'.repeat(64)));
  assert.throws(() => w.splitSig('0x' + 'aa'.repeat(32) + 'bb'.repeat(32) + '05'));       // nonsense v
});

test('permit calldata layout', () => {
  const d = w.preorderWithPermitData(SEL, 2, 10n, 99n, { v: 28, r: '0x' + '11'.repeat(32), s: '0x' + '22'.repeat(32) });
  assert.equal(d, SEL + w.word(2n) + w.word(10n) + w.word(99n) + w.word(28n) + '11'.repeat(32) + '22'.repeat(32));
});

test('device number from receipt', () => {
  const receipt = { status: '0x1', logs: [
    { address: '0xOTHER', topics: ['0xtopic', '0x' + w.word(7n)] },
    { address: '0xC0NTRACT', topics: ['0xtopic', '0x' + w.word(42n), '0x' + '0'.repeat(24) + 'bb'.repeat(20)] }] };
  assert.equal(w.deviceFromReceipt(receipt, '0xc0ntract', '0xTOPIC'), 42n);
  assert.equal(w.deviceFromReceipt({ status: '0x0', logs: [] }, '0xc0ntract', '0xtopic'), null);
  // with a buyer, only that buyer's Preordered log counts
  assert.equal(w.deviceFromReceipt(receipt, '0xc0ntract', '0xtopic', '0x' + 'BB'.repeat(20)), 42n);
  assert.equal(w.deviceFromReceipt(receipt, '0xc0ntract', '0xtopic', '0x' + 'cc'.repeat(20)), null);
  assert.equal(w.deviceFromReceipt({ status: '0x1', logs: [{ address: '0xc0ntract', topics: [] }] }, '0xc0ntract', '0xtopic'), null);
});

test('formatting and the claim message', async () => {
  assert.equal(w.formatUsdc(499000000n), '499.00');
  assert.equal(w.formatUsdc(1250500000n), '1,250.50');
  assert.equal(w.pad4(42n), '0042');
  assert.equal(w.claimMessage(42n, 'abc', 1000), 'Argo Exo pre-order No. 42\nShipping details: abc\nSigned at: 1000');
  assert.equal(await w.sha256Hex('a\nb\nc'), 'ea7fb08b7a2dc4619ffb7c7bb38d95a2047935fa165d71b12efd3852a2e6d0cc');   // = printf 'a\nb\nc' | shasum -a 256
});

test('formatUsdc is exact: never rounds away a base unit', () => {
  assert.equal(w.formatUsdc(0n), '0.00');
  assert.equal(w.formatUsdc(1n), '0.000001');
  assert.equal(w.formatUsdc(499000001n), '499.000001');
  assert.equal(w.formatUsdc(499990000n), '499.99');
  assert.equal(w.formatUsdc(499995000n), '499.995');
  assert.equal(w.formatUsdc(1234567890000n), '1,234,567.89');
  assert.throws(() => w.formatUsdc(-1n));
  assert.throws(() => w.formatUsdc(1.5));
});

test('claim details hash matches the API (trimmed fields)', async () => {
  // site/api/tests/test_claims.py::test_message_format_exact pins this hash
  const h = 'efa51c856d09880b33cea2f1ab697f9767d4bfbc51ab42f0da3cbd7201b381ff';
  assert.equal(await w.detailsHash('Ada', 'ada@example.com', 'Japan'), h);   // public-ok
  assert.equal(await w.detailsHash('  Ada ', '\tada@example.com\n', 'Japan'), h);   // public-ok
});

test('claim fields: trimmed like the API, and anything the API would refuse is refused here too', () => {
  assert.deepEqual(w.claimFields('  Ada L. ', '\tada@example.com\n', 'Japan'), { name: 'Ada L.', email: 'ada@example.com', country: 'Japan' });   // public-ok
  // \x1c-\x1f: whitespace to Python's strip(), not to JS trim(). Refused on both sides, never silently dropped.
  for (const bad of ['Ada\x1c', '\x1fAda', 'Ada\x00', 'A\u200bda', 'A\ufeffda', 'Ada\u2028x', 'Ada\x85'])
    assert.throws(() => w.claimFields(bad, 'ada@example.com', 'Japan'), /check: name/, JSON.stringify(bad));   // public-ok
  assert.throws(() => w.claimFields('Ada', 'ada@example.com\x1e', 'Japan'), /check: email/);   // public-ok
  // what JS trim() does remove (BOM, NBSP, U+2028 at the ends) is gone before hashing, so the API sees clean fields
  assert.equal(w.claimFields('\ufeffAda\u00a0', 'ada@example.com', 'Japan').name, 'Ada');   // public-ok
  assert.throws(() => w.claimFields('Ada', 'ada@example.com', 'Japan\x1c'), /check: country/);   // public-ok
});

test('personal_sign payload is the UTF-8 hex of the message', () => {
  assert.equal(w.utf8Hex('Ab\n'), '0x41620a');
  assert.equal(w.utf8Hex('No. ·'), '0x4e6f2e20c2b7');
});

test('known-key vector: the JS claim message signs to the signature pinned in the API tests', { skip: !CAST && 'cast not installed' }, async () => {
  // site/api/tests/test_claims.py::test_known_signature_recovers (anvil account #0, a public test key)
  const key = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';
  const pinned = '0x0ed5a8ce8ccfaa1f8f0ecbe262100614bca1e5395c80eb72f8d6e369eb3ee53e'
    + '6886258c1b24977b62ad13141ff8be455d1d797ea952b27c98c98bcc6ef89f391b';
  const msg = w.claimMessage(1, await w.detailsHash('Ada', 'ada@example.com', 'Japan'), 1000);   // public-ok
  // the browser sends personal_sign the hex of the UTF-8 bytes; cast signs those raw bytes when given 0x-hex
  assert.equal(cast('wallet', 'sign', '--private-key', key, w.utf8Hex(msg)), pinned);
  assert.equal(cast('wallet', 'sign', '--private-key', key, msg), pinned);
});

test('every selector and topic in preorder.json matches cast', { skip: !CAST && 'cast not installed' }, () => {
  const sigs = {
    preorder: 'preorder(uint8,uint256)', preorderWithPermit: 'preorderWithPermit(uint8,uint256,uint256,uint8,bytes32,bytes32)',
    approve: 'approve(address,uint256)', allowance: 'allowance(address,address)', nonces: 'nonces(address)',
    price: 'price(uint8)', paused: 'paused()', totalMinted: 'totalMinted()', maxSupply: 'maxSupply()', balanceOf: 'balanceOf(address)',
  };
  const errs = { NotForSale: 'NotForSale(uint8)', PriceAboveMax: 'PriceAboveMax(uint256,uint256)', SoldOut: 'SoldOut()', EnforcedPause: 'EnforcedPause()' };
  assert.deepEqual(Object.keys(PREORDER_JSON.selectors).sort(), Object.keys(sigs).sort());
  for (const [k, s] of Object.entries(sigs)) assert.equal(PREORDER_JSON.selectors[k], cast('sig', s), k);
  for (const [k, s] of Object.entries(errs)) assert.equal(PREORDER_JSON.errors[k], cast('sig', s), k);
  assert.equal(PREORDER_JSON.preorderedTopic, cast('keccak', 'Preordered(uint256,address,uint8,uint256)'));
});

test('validateConfig accepts the export and refuses anything off', () => {
  const ok = { ...PREORDER_JSON, contract: '0x' + 'ab'.repeat(20) };
  assert.equal(w.validateConfig(ok), ok);
  assert.equal(w.validateConfig(PREORDER_JSON), PREORDER_JSON);   // zero contract is valid (not deployed)
  assert.equal(w.isDeployed(PREORDER_JSON), false);
  assert.equal(w.isDeployed(ok), true);
  for (const bad of [
    { ...ok, chainId: 1 }, { ...ok, chainIdHex: '0x1' }, { ...ok, usdcDecimals: 18 },
    { ...ok, usdc: '0x' + '11'.repeat(20) }, { ...ok, contract: 'nope' },
    { ...ok, selectors: { ...ok.selectors, price: undefined } }, { ...ok, errors: undefined },
    { ...ok, preorderedTopic: '0x12' }, { ...ok, explorer: 'javascript:alert(1)' }, null,
  ]) assert.throws(() => w.validateConfig(bad));
});

test('revert decoding names the sale errors', () => {
  const E = PREORDER_JSON.errors;
  assert.deepEqual(w.decodeRevert(PREORDER_JSON, E.SoldOut), { kind: 'sold_out' });
  assert.deepEqual(w.decodeRevert(PREORDER_JSON, E.EnforcedPause), { kind: 'paused' });
  assert.deepEqual(w.decodeRevert(PREORDER_JSON, E.NotForSale + w.word(1n)), { kind: 'not_open' });
  assert.deepEqual(w.decodeRevert(PREORDER_JSON, E.PriceAboveMax + w.word(600n) + w.word(500n)), { kind: 'price_changed', price: 600n });
  assert.equal(w.decodeRevert(PREORDER_JSON, '0x08c379a0' + w.word(32n)), null);
  assert.equal(w.decodeRevert(PREORDER_JSON, null), null);
  // provider error shapes vary: find the revert data wherever the wallet put it
  const data = E.SoldOut;
  for (const e of [{ data }, { data: { data } }, { error: { data } }, { data: { originalError: { data } } }, { cause: { data } }])
    assert.equal(w.revertData(e), data);
  assert.equal(w.revertData({ message: 'network down' }), null);
});

// Full/compact pairs signed by cast with anvil key #0 (a public test key). EIP-2098: compact = r || (yParity << 255 | s).
const FULL_V27 = '0x0ed5a8ce8ccfaa1f8f0ecbe262100614bca1e5395c80eb72f8d6e369eb3ee53e'
  + '6886258c1b24977b62ad13141ff8be455d1d797ea952b27c98c98bcc6ef89f391b';   // the pinned claim signature
const COMPACT_V27 = FULL_V27.slice(0, 130);                                   // yParity 0: s unchanged
const FULL_V28 = '0x10b9add03a39483243283b98384e4370890092c04e73354b8b7b6a61cb229a07'
  + '16123750d75a5e3d62b0a8b798fb32d77b8095e442e8a4f68380c8ce6e4120941c';     // cast wallet sign "exo c"
const COMPACT_V28 = '0x10b9add03a39483243283b98384e4370890092c04e73354b8b7b6a61cb229a07'
  + '96123750d75a5e3d62b0a8b798fb32d77b8095e442e8a4f68380c8ce6e412094';       // top bit of s set

test('splitSig expands EIP-2098 compact signatures to the same r, s, v as the full form', () => {
  assert.deepEqual(w.splitSig(COMPACT_V27), w.splitSig(FULL_V27));
  assert.deepEqual(w.splitSig(COMPACT_V28), w.splitSig(FULL_V28));
  assert.equal(w.splitSig(COMPACT_V28).v, 28);
  assert.equal(w.splitSig(COMPACT_V28).s, '0x16123750d75a5e3d62b0a8b798fb32d77b8095e442e8a4f68380c8ce6e412094');
});

test('the expanded compact signature verifies for the signer', { skip: !CAST && 'cast not installed' }, () => {
  const { r, s, v } = w.splitSig(COMPACT_V28);
  const full = r + s.slice(2) + v.toString(16);
  assert.equal(full, FULL_V28);
  cast('wallet', 'verify', '--address', '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266', 'exo c', full);   // throws if not
});
