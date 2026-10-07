// Every message the checkout can show has a slot in content/copy.json (Konrad's words), so no error is ever blank.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const copy = JSON.parse(read('../content/copy.json'));
const checkout = read('../static/checkout.js');
const purchase = read('../static/purchase.mjs');
const wallet = read('../static/wallet.mjs');

test('every C(key), state and receipt string used by checkout.js has a copy slot', () => {
  for (const [, k] of checkout.matchAll(/\bC\('([a-z_]+)'\)/g)) assert.ok(k in copy.checkout, `checkout.${k}`);
  for (const [, g, k] of checkout.matchAll(/say\('([a-z]+)', '([a-z_]+)'\)/g)) assert.ok(k in copy[g], `${g}.${k}`);
  for (const [, k] of checkout.matchAll(/\bR\('([a-z_]+)'\)/g)) assert.ok(k in copy.receipt, `receipt.${k}`);
});

test('every error kind and stage purchase.mjs can produce has a message', () => {
  const kinds = new Set([...purchase.matchAll(/CheckoutError\('([a-z_]+)'/g)].map((m) => m[1]));
  for (const m of purchase.matchAll(/'cancelled' : '([a-z_]+)'/g)) kinds.add(m[1]);
  for (const m of wallet.matchAll(/kind: '([a-z_]+)'/g)) kinds.add(m[1]);
  kinds.add('no_wallet');
  kinds.delete('send_unknown');                           // never shown: the page watches for the purchase instead
  for (const k of kinds) assert.ok(k in copy.checkout || k === 'paused' || k === 'sold_out', `checkout.${k}`);
  assert.ok('paused' in copy.states && 'sold_out' in copy.states);
  for (const [, s] of purchase.matchAll(/onStage\('([a-z_]+)'\)/g)) assert.ok(s in copy.checkout, `stage ${s}`);
});

test('every checkout message is written (no placeholder, never blank) and the edition keeps its counters', () => {
  for (const [k, v] of Object.entries(copy.checkout)) assert.ok(typeof v === 'string' && v.trim() && !/TODO\(konrad\)/i.test(v), k);
  assert.ok(copy.checkout.edition.includes('{minted}') && copy.checkout.edition.includes('{max}'));
});

test('no untrusted string goes in as HTML', () => {
  for (const src of [checkout, purchase, wallet]) assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(src));
});
