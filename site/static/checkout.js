// The pre-order page and the receipt page. All money logic lives in purchase.mjs (tested in node); this file only
// wires it to the DOM. Every string shown comes from content/copy.json (embedded as #checkout-copy) or from the
// chain, and goes in with textContent, never parsed as HTML.
import * as w from './wallet.mjs';
import { CheckoutError, ensureBase, isRejection, purchase, waitApproval, watchPurchase, watchUnknown } from './purchase.mjs';

const $ = (id) => document.getElementById(id);
const PENDING_KEY = 'exo-preorder-pending';
const HASH_RE = /^0x[0-9a-fA-F]{64}$/;
const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;

let COPY = {};
try { COPY = JSON.parse(($('checkout-copy') || {}).textContent || '{}'); } catch { COPY = {}; }
const say = (group, key) => (COPY[group] && typeof COPY[group][key] === 'string' ? COPY[group][key] : '');
const C = (k) => say('checkout', k);
// "opening soon" is the template's own default text in each price line (copy.states.opening_soon); keep it from there
const OPENING_SOON = (document.querySelector('[data-price-for]') || {}).textContent || '';

let CFG = null;
let busy = false;
const shown = new Map();        // tier -> price (BigInt) as last read from the contract and displayed
const openTiers = new Set();

const status = (t) => { const el = $('checkout-status'); if (el) el.textContent = t || ''; };
function fail(text, id = 'checkout-error') {
  const el = $(id);
  if (el) { el.textContent = text || C('failed'); el.hidden = false; }
}
function clearError(id = 'checkout-error') { const el = $(id); if (el) { el.hidden = true; el.textContent = ''; } }

function eth(method, params = []) {
  const p = window.ethereum;
  if (!p || typeof p.request !== 'function') return Promise.reject(new CheckoutError('no_wallet'));
  return p.request({ method, params });
}

function messageFor(e) {
  const kind = e instanceof CheckoutError ? e.kind : (isRejection(e) ? 'cancelled' : 'wallet_error');
  if (kind === 'paused' || kind === 'sold_out') return say('states', kind);
  return C(kind) || C('failed');
}

// ---- the in-flight purchase survives a reload (this tab only) -----------------------------------------------

function loadPending() {
  try {
    const p = JSON.parse(sessionStorage.getItem(PENDING_KEY) || 'null');
    return p && typeof p === 'object' ? p : null;
  } catch { return null; }
}
function remember(rec) {
  try { rec ? sessionStorage.setItem(PENDING_KEY, JSON.stringify(rec)) : sessionStorage.removeItem(PENDING_KEY); } catch { /* storage blocked */ }
}

// ---- buttons ------------------------------------------------------------------------------------------------

const buttons = () => [...document.querySelectorAll('[data-buy]')];
function setBusy(b) {
  busy = b;
  for (const btn of buttons()) btn.disabled = b || !openTiers.has(Number(btn.dataset.buy));
}
function showPrice(tier, text) {
  const el = document.querySelector(`[data-price-for="${Number(tier)}"]`);
  if (el) el.textContent = text;
}
const priceText = (units) => `${w.formatUsdc(units)} USDC`;

function closeAll(label) {
  openTiers.clear();
  for (const b of buttons()) showPrice(b.dataset.buy, label);
}

function toReceipt({ device, hash }) {
  remember(null);
  const q = new URLSearchParams({ n: device.toString() });
  if (HASH_RE.test(hash || '')) q.set('tx', hash);
  location.href = '/receipt.html?' + q.toString();       // buttons stay disabled until the page is gone
}

/** Watch a submitted purchase. Buttons stay disabled until it lands, or until it certainly did not. */
async function follow({ hash, from, fromBlock }) {
  status(C('confirming'));
  try {
    toReceipt(await watchPurchase({ eth, cfg: CFG, hash, from, fromBlock, onPoll: (i) => { if (i === 40) status(C('still_waiting')); } }));
  } catch (e) {
    remember(null); status(''); fail(messageFor(e)); setBusy(false);
  }
}

/** The wallet errored on the send without a hash: it may still have gone out, so look for it before re-enabling. */
async function followUnknown(from, fromBlock, nonce) {
  status(C('confirming'));
  try {
    toReceipt(await watchUnknown({ eth, cfg: CFG, from, fromBlock, nonce, onStill: () => status(C('still_waiting')) }));
  } catch (e) {
    remember(null); status(''); fail(messageFor(e)); setBusy(false);
  }
}

async function buy(tier) {
  if (busy) return;
  clearError();
  const agree = $('agree');
  if (!agree || !agree.checked) return fail(C('agree_first'));
  const price = shown.get(tier);
  if (price === undefined || !openTiers.has(tier)) return fail(C('not_open'));
  setBusy(true);                                          // synchronously, before the first await: no double click
  let release = true;
  try {
    const accounts = await eth('eth_requestAccounts');
    const from = Array.isArray(accounts) ? accounts[0] : null;
    if (!ADDR_RE.test(from || '')) throw new CheckoutError('wallet_error');
    await ensureBase(eth, CFG);
    const sent = await purchase({ eth, cfg: CFG, tier, shownPrice: price, from, remember, onStage: (s) => status(C(s)) });
    release = false;
    await follow(sent);
  } catch (e) {
    if (e instanceof CheckoutError && e.kind === 'send_unknown') {
      release = false;
      await followUnknown(e.from, e.fromBlock, e.nonce);
      return;
    }
    if (e instanceof CheckoutError && e.kind === 'price_changed' && typeof e.price === 'bigint') {
      shown.set(tier, e.price);                           // the number just read; the buyer clicks again to accept it
      showPrice(tier, priceText(e.price));
    }
    if (e instanceof CheckoutError && (e.kind === 'paused' || e.kind === 'sold_out')) closeAll(say('states', e.kind));
    if (e instanceof CheckoutError && e.kind === 'not_open') { openTiers.delete(tier); showPrice(tier, OPENING_SOON); }
    status('');
    fail(messageFor(e));
  } finally {
    if (release) setBusy(false);
  }
}

/** A reload during a purchase: pick the watch back up instead of offering a second purchase. */
async function resume(p) {
  const ok = p && typeof p.contract === 'string' && p.contract.toLowerCase() === CFG.contract.toLowerCase()
    && ADDR_RE.test(p.from || '') && /^0x[0-9a-fA-F]{1,16}$/.test(p.fromBlock || '');
  if (!ok) { remember(null); setBusy(false); return; }
  setBusy(true);
  status(C('resuming'));
  try {
    await ensureBase(eth, CFG);
  } catch (e) {
    status(''); fail(messageFor(e));                     // stays disabled: reload once the wallet is on Base
    return;
  }
  if (p.stage === 'preorder' && HASH_RE.test(p.hash || '')) return follow(p);
  if (p.stage === 'sending') return followUnknown(p.from, p.fromBlock, p.nonce);
  if (p.stage === 'approve' && HASH_RE.test(p.hash || '') && /^\d+$/.test(p.price || '')) {
    try {
      await waitApproval({ eth, cfg: CFG, hash: p.hash, from: p.from, price: BigInt(p.price) });
      status(C('approved_click_again'));
    } catch (e) {
      status(''); fail(messageFor(e));
    }
  } else {
    status('');
  }
  remember(null);
  setBusy(false);
}

// ---- the pre-order page ---------------------------------------------------------------------------------------

const safeInt = (x) => Number.isSafeInteger(x) && x >= 0;
function validSale(s) {
  if (!s || typeof s !== 'object' || typeof s.open !== 'boolean' || typeof s.paused !== 'boolean') return null;
  if (!safeInt(s.minted) || !safeInt(s.max_supply) || !Array.isArray(s.tiers)) return null;
  if (s.tiers.some((t) => !t || !Number.isInteger(t.tier) || !safeInt(t.price_units))) return null;
  return s;
}

async function renderSale() {
  for (const b of buttons()) b.addEventListener('click', () => buy(Number(b.dataset.buy)));
  if (!w.isDeployed(CFG)) { setBusy(busy); return; }      // not deployed: the template's "opening soon" stands
  let sale = null;
  try {
    const r = await fetch('/api/sale', { cache: 'no-store', headers: { Accept: 'application/json' } });
    if (r.ok) sale = validSale(await r.json());
  } catch { sale = null; }
  if (!sale) { closeAll(say('states', 'unavailable')); setBusy(busy); return; }
  const soldOut = sale.minted >= sale.max_supply;
  for (const b of buttons()) {
    const tier = Number(b.dataset.buy);
    const t = sale.tiers.find((x) => x.tier === tier);
    if (sale.paused) showPrice(tier, say('states', 'paused'));
    else if (soldOut) showPrice(tier, say('states', 'sold_out'));
    else if (!t || t.price_units === 0 || !sale.open) showPrice(tier, OPENING_SOON);
    else {
      const units = BigInt(t.price_units);                 // read from the contract by /api/sale; re-read before signing
      shown.set(tier, units);
      openTiers.add(tier);
      showPrice(tier, priceText(units));
    }
  }
  const ed = $('edition');
  if (ed && C('edition')) ed.textContent = C('edition').replaceAll('{minted}', String(sale.minted)).replaceAll('{max}', String(sale.max_supply));
  setBusy(busy);
}

// ---- the receipt page -------------------------------------------------------------------------------------------

function para(cls, text) {
  const el = document.createElement('p');
  if (cls) el.className = cls;
  el.textContent = text;
  return el;
}

function renderReceipt() {
  const R = (k) => say('receipt', k);
  const box = $('receipt');
  const q = new URLSearchParams(location.search);
  const n = q.get('n') || '', tx = q.get('tx') || '';
  if (!/^[1-9]\d{0,9}$/.test(n)) { box.replaceChildren(para('error', R('no_number'))); return; }
  const parts = [para('edition', R('heading')), para('number', `${R('number_label')} ${w.pad4(n)}`), para('', R('nft_note'))];
  if (CFG && HASH_RE.test(tx)) {
    const a = document.createElement('a');
    a.href = `${CFG.explorer}/tx/${tx}`;
    a.rel = 'noopener';
    a.textContent = R('tx_link');
    const p = document.createElement('p');
    p.append(a);
    parts.push(p);
  }
  box.replaceChildren(...parts);
  const claimN = $('claim-n');
  if (claimN) claimN.textContent = w.pad4(n);
  const form = $('claim-form');
  if (!form) return;
  form.hidden = false;
  const submit = form.querySelector('button[type="submit"]');
  let claiming = false;
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (claiming) return;
    claiming = true;
    if (submit) submit.disabled = true;
    clearError('claim-error');
    try {
      const fd = new FormData(form);
      const f = w.claimFields(fd.get('name') ?? '', fd.get('email') ?? '', fd.get('country') ?? '');
      const accounts = await eth('eth_requestAccounts');
      const from = Array.isArray(accounts) ? accounts[0] : null;
      if (!ADDR_RE.test(from || '')) throw new CheckoutError('wallet_error');
      const at = Math.floor(Date.now() / 1000);
      const msg = w.claimMessage(n, await w.detailsHash(f.name, f.email, f.country), at);
      const signature = await eth('personal_sign', [w.utf8Hex(msg), from]);
      const r = await fetch('/api/claims', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_number: Number(n), name: f.name, email: f.email, country: f.country, signed_at: at, signature }) });
      let j = null;
      try { j = await r.json(); } catch { j = null; }
      if (!r.ok) throw new Error((j && typeof j.error === 'string' && j.error) || `HTTP ${r.status}`);
      form.replaceChildren(para('status-line', R('saved')));
    } catch (e) {
      const text = e instanceof CheckoutError || isRejection(e) ? messageFor(e) : (e && e.message) || C('failed');
      fail(text, 'claim-error');
      if (submit) submit.disabled = false;
    } finally {
      claiming = false;
    }
  });
}

// ---- start --------------------------------------------------------------------------------------------------------

async function loadConfig() {
  try {
    const r = await fetch('/static/preorder.json', { cache: 'no-store' });
    return r.ok ? w.validateConfig(await r.json()) : null;
  } catch { return null; }
}

(async () => {
  CFG = await loadConfig();
  if ($('receipt')) { renderReceipt(); return; }
  if (!CFG) return;                                        // no usable config: every button stays disabled
  const pending = w.isDeployed(CFG) ? loadPending() : null;
  if (pending) busy = true;                                // keep the buttons down while the sale renders
  await renderSale();
  if (pending) await resume(pending);
})();
