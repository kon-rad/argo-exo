// The pre-order purchase against an EIP-1193 wallet (`eth(method, params)`), with no DOM so every money rule is
// tested in node (site/js-tests/purchase.test.mjs):
//  - the price is read from the contract right before signing and must equal the one the page showed; maxPrice is
//    exactly that price, and a different one stops with `price_changed` so the buyer clicks again
//  - a wallet with any code at its address (smart account, EIP-7702 delegation `0xef0100…`) never gets a permit;
//    for an EOA the permit purchase is simulated first and a revert falls back to approve(exact price) + preorder
//  - nothing is sent unless the wallet reports Base (8453) at that moment
//  - a rejection stops; nothing is retried. Each sent hash is handed to `remember` before any waiting, so a reload
//    can resume watching it instead of buying again.
import * as w from './wallet.mjs';

export class CheckoutError extends Error {
  constructor(kind, extra = {}, cause) {
    super(kind);
    this.kind = kind;
    Object.assign(this, extra);
    if (cause !== undefined) this.cause = cause;
  }
}

export const isRejection = (e) => !!e && (e.code === 4001 || e.code === 'ACTION_REJECTED'
  || /user (rejected|denied|cancel)/i.test(String(e.message || '')));
const isRevert = (e) => !!e && (e.code === 3 || w.revertData(e) !== null || /revert/i.test(String(e.message || '')));
const defaultSleep = (ms) => new Promise((res) => setTimeout(res, ms));
const HASH_RE = /^0x[0-9a-fA-F]{64}$/;
const toHex = (n) => '0x' + n.toString(16);

async function rpc(eth, method, params) {
  try { return await eth(method, params); } catch (e) { throw new CheckoutError(isRejection(e) ? 'cancelled' : 'rpc', {}, e); }
}

async function readUint(eth, to, data) {
  const r = await rpc(eth, 'eth_call', [{ to, data }, 'latest']);
  try { return w.uintResult(r); } catch (e) { throw new CheckoutError('rpc', {}, e); }
}

async function onBase(eth, cfg) {
  const id = await rpc(eth, 'eth_chainId', []);
  try { return BigInt(id) === BigInt(cfg.chainId); } catch { return false; }
}

/** Throws `wrong_chain` unless the wallet is on Base right now. Called before every signature and send. */
export async function requireBase(eth, cfg) {
  if (!(await onBase(eth, cfg))) throw new CheckoutError('wrong_chain');
}

/** Ask the wallet to switch to Base, adding it if the wallet doesn't know it; then verify. */
export async function ensureBase(eth, cfg) {
  if (await onBase(eth, cfg)) return;
  const switchTo = () => eth('wallet_switchEthereumChain', [{ chainId: cfg.chainIdHex }]);
  const unknownChain = (e) => e && (e.code === 4902 || (e.data && e.data.originalError && e.data.originalError.code === 4902));
  try {
    await switchTo();
  } catch (e) {
    if (isRejection(e)) throw new CheckoutError('cancelled', {}, e);
    if (!unknownChain(e)) throw new CheckoutError('wrong_chain', {}, e);
    try {
      await eth('wallet_addEthereumChain', [{ chainId: cfg.chainIdHex, chainName: cfg.chainName || 'Base',
        nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 }, rpcUrls: ['https://mainnet.base.org'], blockExplorerUrls: [cfg.explorer] }]);
      await switchTo();
    } catch (e2) {
      throw new CheckoutError(isRejection(e2) ? 'cancelled' : 'wrong_chain', {}, e2);
    }
  }
  await requireBase(eth, cfg);
}

/** Live sale state for one tier, straight from the contract through the wallet's RPC. */
export async function readLive(eth, cfg, tier) {
  if (!Number.isInteger(tier) || tier < 1 || tier > 255) throw new CheckoutError('not_open');
  const C = cfg.contract, S = cfg.selectors;
  const paused = await readUint(eth, C, S.paused);
  const minted = await readUint(eth, C, S.totalMinted);
  const maxSupply = await readUint(eth, C, S.maxSupply);
  const price = await readUint(eth, C, w.callData(S.price, w.word(tier)));
  return { paused: paused !== 0n, minted, maxSupply, price };
}

async function livePrice(eth, cfg, tier) {
  const s = await readLive(eth, cfg, tier);
  if (s.paused) throw new CheckoutError('paused');
  if (s.minted >= s.maxSupply) throw new CheckoutError('sold_out');
  if (s.price === 0n) throw new CheckoutError('not_open');
  return s.price;
}

/** eth_call the exact transaction. 'ok', or 'reverted' for a revert that isn't one of the sale's own errors
 *  (those throw by name); a failure that isn't a revert at all (RPC down) throws `rpc`. */
async function simulate(eth, cfg, from, data) {
  try {
    await eth('eth_call', [{ from, to: cfg.contract, data }, 'latest']);
    return 'ok';
  } catch (e) {
    if (isRejection(e) || !isRevert(e)) throw new CheckoutError(isRejection(e) ? 'cancelled' : 'rpc', {}, e);
    const k = w.decodeRevert(cfg, w.revertData(e));
    if (k) throw new CheckoutError(k.kind, k.price !== undefined ? { price: k.price } : {}, e);
    return 'reverted';
  }
}

/** preorderWithPermit calldata signed for exactly `price`, or null when the wallet can't produce a usable permit
 *  (unsupported method, malformed signature). A rejection throws `cancelled`. */
async function permitCalldata(eth, cfg, from, tier, price, now) {
  let nonce;
  try { nonce = await readUint(eth, cfg.usdc, w.callData(cfg.selectors.nonces, w.addrWord(from))); } catch { return null; }
  const deadline = BigInt(Math.floor(now() / 1000) + 1800);
  const td = w.permitTypedData({ name: cfg.usdcName, version: cfg.usdcVersion, chainId: cfg.chainId, verifyingContract: cfg.usdc,
    owner: from, spender: cfg.contract, value: price, nonce, deadline });
  await requireBase(eth, cfg);
  try {
    const sig = w.splitSig(await eth('eth_signTypedData_v4', [from, JSON.stringify(td)]));
    return w.preorderWithPermitData(cfg.selectors.preorderWithPermit, tier, price, deadline, sig);
  } catch (e) {
    if (isRejection(e)) throw new CheckoutError('cancelled', {}, e);
    return null;
  }
}

/** The account's next nonce including pending txs, or null when the wallet can't say. */
async function pendingNonce(eth, from) {
  try { return w.uintResult(await eth('eth_getTransactionCount', [from, 'pending'])); } catch { return null; }
}

/** Send the purchase transaction. `remember` gets a 'sending' record before the wallet prompt (a reload while the
 *  prompt is open must not lose track of it), carrying the account's pending nonce at that moment, and the hash as
 *  soon as there is one. */
async function sendPurchase(eth, cfg, remember, base, tx) {
  await requireBase(eth, cfg);
  const n = await pendingNonce(eth, base.from);
  const rec = { ...base, nonce: n === null ? null : n.toString() };
  remember({ ...rec, stage: 'sending' });
  const unknown = { from: base.from, fromBlock: base.fromBlock, nonce: rec.nonce };
  let hash;
  try {
    hash = await eth('eth_sendTransaction', [{ ...tx, chainId: cfg.chainIdHex }]);
  } catch (e) {
    if (isRejection(e)) { remember(null); throw new CheckoutError('cancelled', {}, e); }
    throw new CheckoutError('send_unknown', unknown, e);   // it may have gone out
  }
  if (!HASH_RE.test(String(hash))) throw new CheckoutError('send_unknown', unknown);
  remember({ ...rec, stage: 'preorder', hash });
  return hash;
}

/**
 * Run one purchase up to the moment the pre-order transaction is submitted. Returns {hash, from, fromBlock};
 * the caller then watches it with watchPurchase. Throws CheckoutError with a `kind` the page names.
 */
export async function purchase({ eth, cfg, tier, shownPrice, from, now = Date.now, onStage = () => {}, remember = () => {}, sleep = defaultSleep }) {
  if (!w.isDeployed(cfg) || typeof shownPrice !== 'bigint' || shownPrice <= 0n) throw new CheckoutError('not_open');
  w.addrWord(from);
  const S = cfg.selectors;
  await requireBase(eth, cfg);
  onStage('checking');
  const price = await livePrice(eth, cfg, tier);
  if (price !== shownPrice) throw new CheckoutError('price_changed', { price });
  const balance = await readUint(eth, cfg.usdc, w.callData(S.balanceOf, w.addrWord(from)));
  if (balance < price) throw new CheckoutError('low_balance', { price, balance });

  let code = null;
  try { code = await eth('eth_getCode', [from, 'latest']); } catch { /* unknown: treat as a contract wallet */ }
  // The purchase can only land after the current head, so the watch never mistakes an earlier pre-order for it.
  let fromBlock;
  try { fromBlock = toHex(w.uintResult(await rpc(eth, 'eth_blockNumber', [])) + 1n); } catch (e) { throw e instanceof CheckoutError ? e : new CheckoutError('rpc', {}, e); }
  const base = { contract: cfg.contract, from, tier, price: price.toString(), fromBlock };

  if (code === '0x') {                                   // a plain key account: try the one-click permit
    onStage('signing_permit');
    const data = await permitCalldata(eth, cfg, from, tier, price, now);
    if (data && (await simulate(eth, cfg, from, data)) === 'ok') {
      onStage('confirm_preorder');
      return { hash: await sendPurchase(eth, cfg, remember, base, { from, to: cfg.contract, data }), from, fromBlock };
    }
  }

  // two steps: approve exactly the price (never unlimited), then preorder
  const allowance = await readUint(eth, cfg.usdc, w.callData(S.allowance, w.addrWord(from), w.addrWord(cfg.contract)));
  if (allowance < price) {
    onStage('approving');
    await requireBase(eth, cfg);
    let hash;
    try {
      hash = await eth('eth_sendTransaction', [{ from, to: cfg.usdc, data: w.approveData(S.approve, cfg.contract, price), chainId: cfg.chainIdHex }]);
    } catch (e) {
      throw new CheckoutError(isRejection(e) ? 'cancelled' : 'wallet_error', {}, e);
    }
    if (!HASH_RE.test(String(hash))) throw new CheckoutError('wallet_error');
    remember({ ...base, stage: 'approve', hash });
    onStage('approving_wait');
    await waitApproval({ eth, cfg, hash, from, price, sleep });
    remember(null);
    await requireBase(eth, cfg);
    onStage('checking');
    const again = await livePrice(eth, cfg, tier);
    if (again !== price) throw new CheckoutError('price_changed', { price: again });
  }
  const data = w.preorderData(S.preorder, tier, price);
  if ((await simulate(eth, cfg, from, data)) !== 'ok') throw new CheckoutError('reverted');
  onStage('confirm_preorder');
  return { hash: await sendPurchase(eth, cfg, remember, base, { from, to: cfg.contract, data }), from, fromBlock };
}

/** Count consecutive polls on which the account's mined nonce has moved past our transaction's. */
function replacementTracker(eth, hash, from) {
  let nonce = null, seen = 0;
  return async () => {
    if (nonce === null) {
      const tx = await eth('eth_getTransactionByHash', [hash]);
      if (tx && tx.nonce != null) nonce = BigInt(tx.nonce);
    }
    if (nonce === null) return 0;
    const count = BigInt(await eth('eth_getTransactionCount', [from, 'latest']));
    seen = count > nonce ? seen + 1 : 0;
    return seen;
  };
}

/**
 * Watch a submitted pre-order until it lands. Resolves {device, hash} from the receipt, or from a Preordered log for
 * this buyer since `fromBlock` (which also catches a wallet "speed up" that replaced the hash). Rejects only when it
 * is certain no purchase happened: `failed` (reverted), `replaced` (nonce used by another tx, no log, twice in a
 * row), or `unconfirmed` after `maxPolls` when there was no hash to follow. RPC hiccups keep it watching.
 */
export async function watchPurchase({ eth, cfg, hash, from, fromBlock, sleep = defaultSleep, maxPolls = Infinity, onPoll = () => {}, interval = 2500 }) {
  const replaced = hash ? replacementTracker(eth, hash, from) : null;
  for (let i = 1; ; i++) {
    try {
      if (hash) {
        const r = await eth('eth_getTransactionReceipt', [hash]);
        if (r) {
          const d = r.status === '0x1' ? w.deviceFromReceipt(r, cfg.contract, cfg.preorderedTopic, from) : null;
          if (d === null) throw new CheckoutError('failed', { hash });
          return { device: d, hash };
        }
      }
      const logs = await eth('eth_getLogs', [{ address: cfg.contract, fromBlock, toBlock: 'latest',
        topics: [cfg.preorderedTopic, null, '0x' + w.addrWord(from)] }]);
      const mine = (Array.isArray(logs) ? logs : []).filter((l) => !l.removed && w.isPreordered(l, cfg.contract, cfg.preorderedTopic, from));
      if (mine.length) {
        const l = mine[mine.length - 1];
        return { device: BigInt(l.topics[1]), hash: HASH_RE.test(String(l.transactionHash)) ? l.transactionHash : hash };
      }
      if (replaced && (await replaced()) >= 2) throw new CheckoutError('replaced', { hash });
    } catch (e) {
      if (e instanceof CheckoutError) throw e;           // anything else is the RPC: keep watching
    }
    if (i >= maxPolls) throw new CheckoutError('unconfirmed');
    onPoll(i);
    await sleep(interval);
  }
}

/**
 * The wallet errored on the send without giving a hash, so it may still have gone out. Look for this buyer's
 * Preordered log for `maxPolls`; then, if the account's pending nonce has moved past `nonce` (recorded right before
 * the wallet prompt), a transaction did go out: call `onStill` and keep watching. Rejects `unconfirmed` only when
 * the nonce is known not to have moved. An unreadable nonce counts as "may have moved".
 */
export async function watchUnknown({ eth, cfg, from, fromBlock, nonce, sleep = defaultSleep, maxPolls = 48, onStill = () => {}, interval = 2500 }) {
  const recorded = typeof nonce === 'string' && /^\d+$/.test(nonce) ? BigInt(nonce) : null;
  for (;;) {
    try {
      return await watchPurchase({ eth, cfg, hash: null, from, fromBlock, sleep, maxPolls, interval });
    } catch (e) {
      if (!(e instanceof CheckoutError) || e.kind !== 'unconfirmed' || recorded === null) throw e;
      const now = await pendingNonce(eth, from);
      if (now !== null && now <= recorded) throw e;          // nothing left this account: safe to re-enable
      onStill();
      await sleep(interval);
    }
  }
}

/** Wait for an approve: done once the allowance covers `price` (also covers a sped-up approve). */
export async function waitApproval({ eth, cfg, hash, from, price, sleep = defaultSleep, interval = 2500 }) {
  const replaced = replacementTracker(eth, hash, from);
  const data = w.callData(cfg.selectors.allowance, w.addrWord(from), w.addrWord(cfg.contract));
  for (;;) {
    try {
      const r = await eth('eth_getTransactionReceipt', [hash]);
      if (r && r.status !== '0x1') throw new CheckoutError('approve_failed', { hash });
      if (w.uintResult(await eth('eth_call', [{ to: cfg.usdc, data }, 'latest'])) >= price) return;
      if (!r && (await replaced()) >= 2) throw new CheckoutError('approve_failed', { hash });
    } catch (e) {
      if (e instanceof CheckoutError) throw e;
    }
    await sleep(interval);
  }
}
