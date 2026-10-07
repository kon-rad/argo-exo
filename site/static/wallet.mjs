// Pure helpers for the pre-order checkout: ABI words by hand (static types only), EIP-2612 typed data, receipt
// parsing, revert decoding, and the shipping-claim message. No DOM, no network: tested with node --test.
// Every selector comes from preorder.json (written by site/contracts/export.sh with cast), never from here.

const MAX_UINT = (1n << 256n) - 1n;
export const ZERO_ADDRESS = '0x0000000000000000000000000000000000000000';
export const BASE_CHAIN_ID = 8453;
export const BASE_USDC = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913';

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
const SEL_RE = /^0x[0-9a-f]{8}$/;
const B32_RE = /^0x[0-9a-fA-F]{64}$/;

export const word = (n) => {
  const b = BigInt(n);
  if (b < 0n || b > MAX_UINT) throw new Error('uint256 out of range');
  return b.toString(16).padStart(64, '0');
};
export const addrWord = (a) => { if (!ADDR_RE.test(a)) throw new Error('bad address'); return a.slice(2).toLowerCase().padStart(64, '0'); };
const tierWord = (t) => { if (!Number.isInteger(t) || t < 1 || t > 255) throw new Error('bad tier'); return word(t); };
const b32 = (h) => { if (!B32_RE.test(h)) throw new Error('bad bytes32'); return h.slice(2).toLowerCase(); };

export const callData = (sel, ...words) => sel + words.join('');
export const preorderData = (sel, tier, maxPrice) => callData(sel, tierWord(tier), word(maxPrice));
export const approveData = (sel, spender, amount) => callData(sel, addrWord(spender), word(amount));
export const preorderWithPermitData = (sel, tier, maxPrice, deadline, { v, r, s }) =>
  callData(sel, tierWord(tier), word(maxPrice), word(deadline), word(v), b32(r), b32(s));

/** A uint256 eth_call result as BigInt; anything else (including "0x" from an address with no code) throws. */
export function uintResult(hex) {
  if (typeof hex !== 'string' || !/^0x[0-9a-fA-F]{1,64}$/.test(hex)) throw new Error('malformed call result');
  return BigInt(hex);
}

export function permitTypedData({ name, version, chainId, verifyingContract, owner, spender, value, nonce, deadline }) {
  return {
    types: {
      EIP712Domain: [{ name: 'name', type: 'string' }, { name: 'version', type: 'string' }, { name: 'chainId', type: 'uint256' }, { name: 'verifyingContract', type: 'address' }],
      Permit: [{ name: 'owner', type: 'address' }, { name: 'spender', type: 'address' }, { name: 'value', type: 'uint256' }, { name: 'nonce', type: 'uint256' }, { name: 'deadline', type: 'uint256' }],
    },
    primaryType: 'Permit',
    domain: { name, version, chainId, verifyingContract },
    message: { owner, spender, value: String(value), nonce: String(nonce), deadline: String(deadline) },
  };
}

/** 65-byte r||s||v (v of 0/1 normalised to 27/28), or a 64-byte EIP-2098 compact r||yParityAndS expanded to the
 *  same; anything else throws. */
export function splitSig(sig) {
  if (typeof sig !== 'string') throw new Error('bad signature');
  const h = sig.startsWith('0x') ? sig.slice(2) : sig;
  if (/^[0-9a-fA-F]{128}$/.test(h)) {
    const vs = BigInt('0x' + h.slice(64));
    const yParity = vs >> 255n;
    return { r: '0x' + h.slice(0, 64).toLowerCase(), s: '0x' + word(vs & ((1n << 255n) - 1n)), v: 27 + Number(yParity) };
  }
  if (!/^[0-9a-fA-F]{130}$/.test(h)) throw new Error('bad signature');
  let v = parseInt(h.slice(128, 130), 16);
  if (v < 27) v += 27;
  if (v !== 27 && v !== 28) throw new Error('bad signature v');
  return { r: '0x' + h.slice(0, 64), s: '0x' + h.slice(64, 128), v };
}

const lc = (s) => String(s ?? '').toLowerCase();

/** The device number from a successful receipt's Preordered log (optionally only the given buyer's), else null. */
export function deviceFromReceipt(receipt, contract, topic, buyer) {
  if (!receipt || receipt.status !== '0x1') return null;
  const log = (receipt.logs || []).find((l) => isPreordered(l, contract, topic, buyer));
  return log ? BigInt(log.topics[1]) : null;
}

export function isPreordered(log, contract, topic, buyer) {
  const t = (log && log.topics) || [];
  if (lc(log && log.address) !== lc(contract) || lc(t[0]) !== lc(topic) || t.length < 2) return false;
  return !buyer || (t.length >= 3 && lc(t[2]) === '0x' + addrWord(buyer));
}

// ---- revert decoding --------------------------------------------------------

/** Find revert data in an EIP-1193 error, wherever the wallet nested it. */
export function revertData(e) {
  const seen = new Set();
  const walk = (x, depth) => {
    if (x == null || depth > 4 || seen.has(x)) return null;
    if (typeof x === 'string') return /^0x[0-9a-fA-F]{8,}$/.test(x) ? x : null;
    if (typeof x !== 'object') return null;
    seen.add(x);
    for (const k of ['data', 'originalError', 'error', 'cause']) {
      const r = walk(x[k], depth + 1);
      if (r) return r;
    }
    return null;
  };
  return walk(e, 0);
}

/** ExoPreorder's sale errors as a kind the page can name; null for anything else. */
export function decodeRevert(cfg, data) {
  if (typeof data !== 'string' || data.length < 10) return null;
  const sel = data.slice(0, 10).toLowerCase(), E = cfg.errors;
  if (sel === E.SoldOut) return { kind: 'sold_out' };
  if (sel === E.EnforcedPause) return { kind: 'paused' };
  if (sel === E.NotForSale) return { kind: 'not_open' };
  if (sel === E.PriceAboveMax && data.length >= 10 + 64) return { kind: 'price_changed', price: BigInt('0x' + data.slice(10, 74)) };
  return null;
}

// ---- config -----------------------------------------------------------------

const SELECTORS = ['preorder', 'preorderWithPermit', 'approve', 'allowance', 'nonces', 'price', 'paused', 'totalMinted', 'maxSupply', 'balanceOf'];
const ERRORS = ['NotForSale', 'PriceAboveMax', 'SoldOut', 'EnforcedPause'];

/** Refuse a preorder.json that isn't Base mainnet native USDC with every selector the checkout needs. */
export function validateConfig(c) {
  const bad = (what) => { throw new Error('preorder.json: ' + what); };
  if (!c || typeof c !== 'object') bad('missing');
  if (c.chainId !== BASE_CHAIN_ID || c.chainIdHex !== '0x2105') bad('not Base mainnet');
  if (lc(c.usdc) !== BASE_USDC || c.usdcDecimals !== 6) bad('not Base native USDC');
  if (typeof c.usdcName !== 'string' || typeof c.usdcVersion !== 'string') bad('USDC domain');
  if (!ADDR_RE.test(c.contract || '')) bad('contract');
  if (!c.selectors || SELECTORS.some((k) => !SEL_RE.test(c.selectors[k] || ''))) bad('selectors');
  if (!c.errors || ERRORS.some((k) => !SEL_RE.test(c.errors[k] || ''))) bad('errors');
  if (!B32_RE.test(c.preorderedTopic || '')) bad('topic');
  if (!/^https:\/\/[^\s"'<>]+$/.test(c.explorer || '')) bad('explorer');
  return c;
}

export const isDeployed = (c) => lc(c && c.contract) !== ZERO_ADDRESS && ADDR_RE.test((c && c.contract) || '');

// ---- shipping claim -----------------------------------------------------------

export const claimMessage = (n, hash, at) => `Argo Exo pre-order No. ${n}\nShipping details: ${hash}\nSigned at: ${at}`;

export async function sha256Hex(text) {
  const buf = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Same as the API's details_hash over the trimmed fields it stores (name and email trimmed, country exact). */
export const claimFields = (name, email, country) => ({ name: String(name).trim(), email: String(email).trim(), country: String(country) });
export async function detailsHash(name, email, country) {
  const f = claimFields(name, email, country);
  return sha256Hex(`${f.name}\n${f.email}\n${f.country}`);
}

/** personal_sign's message parameter: the UTF-8 bytes as 0x-hex (what EIP-1193 wallets expect). */
export const utf8Hex = (text) => '0x' + [...new TextEncoder().encode(text)].map((b) => b.toString(16).padStart(2, '0')).join('');

// ---- display ------------------------------------------------------------------

/** USDC base units as dollars, exact: two decimals minimum, more when the amount has them (1n -> "0.000001"). */
export function formatUsdc(units) {
  if (typeof units !== 'bigint') throw new Error('formatUsdc takes a BigInt');
  if (units < 0n) throw new Error('negative amount');
  const whole = (units / 1000000n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  let frac = (units % 1000000n).toString().padStart(6, '0').replace(/0+$/, '');
  if (frac.length < 2) frac = frac.padEnd(2, '0');
  return `${whole}.${frac}`;
}

export const pad4 = (n) => String(n).padStart(4, '0');
