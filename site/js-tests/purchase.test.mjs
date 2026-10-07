// The money rulings for the checkout, against a scripted EIP-1193 wallet: which path, which amount, which chain,
// and that nothing is ever sent twice or sent after the buyer said no.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as w from '../static/wallet.mjs';
import * as p from '../static/purchase.mjs';

const CFG = { ...JSON.parse(readFileSync(new URL('../static/preorder.json', import.meta.url), 'utf8')), contract: '0x' + 'c0'.repeat(20) };
const S = CFG.selectors;
const BUYER = '0x' + 'b0'.repeat(20);
const PRICE = 499000000n;
const SIG = '0x' + '11'.repeat(32) + '22'.repeat(32) + '1c';
const hexw = (n) => '0x' + w.word(n);
const noSleep = async () => {};

function wallet(over = {}) {
  const s = { chainId: '0x2105', code: '0x', price: PRICE, paused: false, minted: 3n, max: 100n, balance: 10n ** 12n,
    allowance: 0n, nonce: 7n, simulate: 'ok', sign: SIG, sent: [], receipts: {}, logs: [], txCount: 0n, ...over };
  const calls = [];
  const eth = async (method, params = []) => {
    calls.push({ method, params });
    if (s.on?.[method]) { const r = await s.on[method](params, s); if (r !== undefined) return r; }
    switch (method) {
      case 'eth_chainId': return s.chainId;
      case 'eth_getCode': return s.code;
      case 'eth_blockNumber': return '0x100';
      case 'eth_signTypedData_v4': if (s.sign instanceof Error) throw s.sign; return s.sign;
      case 'eth_call': {
        const { to, data } = params[0];
        const sel = data.slice(0, 10);
        if (to === CFG.contract) {
          if (sel === S.price) return hexw(s.price);
          if (sel === S.paused) return hexw(s.paused ? 1n : 0n);
          if (sel === S.totalMinted) return hexw(s.minted);
          if (sel === S.maxSupply) return hexw(s.max);
          if (sel === S.preorderWithPermit || sel === S.preorder) {
            const sim = typeof s.simulate === 'function' ? s.simulate(sel, s) : s.simulate;
            if (sim === 'ok') return hexw(s.minted + 1n);
            throw sim;
          }
        }
        if (to === CFG.usdc) {
          if (sel === S.balanceOf) return hexw(s.balance);
          if (sel === S.allowance) return hexw(s.allowance);
          if (sel === S.nonces) return hexw(s.nonce);
        }
        throw new Error('unexpected eth_call ' + to + ' ' + sel);
      }
      case 'eth_sendTransaction': {
        if (s.send instanceof Error) throw s.send;
        s.sent.push(params[0]);
        const hash = '0x' + w.word(BigInt(s.sent.length));
        if (params[0].to === CFG.usdc) { s.allowance = BigInt('0x' + params[0].data.slice(74)); s.receipts[hash] = { status: '0x1', logs: [] }; }
        return hash;
      }
      case 'eth_getTransactionReceipt': return s.receipts[params[0]] ?? null;
      case 'eth_getLogs': return s.logs;
      case 'eth_getTransactionByHash': return { nonce: '0x5' };
      case 'eth_getTransactionCount': return '0x' + s.txCount.toString(16);
      default: throw new Error('unexpected ' + method);
    }
  };
  return { s, calls, eth, methods: () => calls.map((c) => c.method) };
}

const buy = (wl, extra = {}) => p.purchase({ eth: wl.eth, cfg: CFG, tier: 1, shownPrice: PRICE, from: BUYER, now: () => 1_700_000_000_000, ...extra });
const rejects = async (promise, kind) => { await assert.rejects(promise, (e) => { assert.equal(e.kind, kind, e.message); return true; }); };

test('EOA: one-click permit for the live price, no approve', async () => {
  const wl = wallet();
  const res = await buy(wl);
  assert.equal(wl.s.sent.length, 1);
  const tx = wl.s.sent[0];
  assert.equal(tx.to, CFG.contract);
  assert.equal(tx.from, BUYER);
  assert.equal(tx.chainId, '0x2105');                                      // the wallet itself refuses another chain
  const deadline = BigInt(1_700_000_000 + 1800);
  assert.equal(tx.data, w.preorderWithPermitData(S.preorderWithPermit, 1, PRICE, deadline, w.splitSig(SIG)));
  const td = JSON.parse(wl.calls.find((c) => c.method === 'eth_signTypedData_v4').params[1]);
  assert.deepEqual(td.domain, { name: 'USD Coin', version: '2', chainId: 8453, verifyingContract: CFG.usdc });
  assert.deepEqual(td.message, { owner: BUYER, spender: CFG.contract, value: String(PRICE), nonce: '7', deadline: String(deadline) });
  // simulated the exact calldata it then sent
  const sim = wl.calls.filter((c) => c.method === 'eth_call' && c.params[0].data.startsWith(S.preorderWithPermit));
  assert.equal(sim.length, 1);
  assert.equal(sim[0].params[0].data, tx.data);
  assert.equal(sim[0].params[0].from, BUYER);
  assert.equal(res.hash, '0x' + w.word(1n));
  assert.equal(res.fromBlock, '0x101');   // head + 1: the purchase cannot be in a block already mined
});

test('wallet with code (EIP-7702 delegation) goes straight to approve exact price + preorder', async () => {
  const wl = wallet({ code: '0xef0100' + 'de'.repeat(20) });
  await buy(wl);
  assert.ok(!wl.methods().includes('eth_signTypedData_v4'));
  assert.equal(wl.s.sent.length, 2);
  assert.equal(wl.s.sent[0].to, CFG.usdc);
  assert.equal(wl.s.sent[0].data, w.approveData(S.approve, CFG.contract, PRICE));        // exact, never unlimited
  assert.equal(wl.s.sent[1].to, CFG.contract);
  assert.equal(wl.s.sent[1].data, w.preorderData(S.preorder, 1, PRICE));
  assert.deepEqual(wl.s.sent.map((t) => t.chainId), ['0x2105', '0x2105']);   // approve and preorder both carry it
});

test('any code at all, or an unreadable answer, counts as a contract wallet', async () => {
  for (const code of ['0x00', '0x6080', null, undefined]) {
    const wl = wallet({ code });
    await buy(wl);
    assert.ok(!wl.methods().includes('eth_signTypedData_v4'), String(code));
  }
});

test('permit simulation reverts for a non-sale reason: approve exact price, then preorder', async () => {
  const wl = wallet({ simulate: (sel) => (sel === S.preorderWithPermit ? Object.assign(new Error('execution reverted'), { code: 3, data: '0x08c379a0' }) : 'ok') });
  await buy(wl);
  assert.equal(wl.s.sent.length, 2);
  assert.equal(wl.s.sent[0].data, w.approveData(S.approve, CFG.contract, PRICE));
  assert.equal(wl.s.sent[1].data, w.preorderData(S.preorder, 1, PRICE));
});

test('a wallet that cannot sign typed data falls back to approve', async () => {
  const wl = wallet({ sign: Object.assign(new Error('method not supported'), { code: 4200 }) });
  await buy(wl);
  assert.deepEqual(wl.s.sent.map((t) => t.to), [CFG.usdc, CFG.contract]);
});

test('approve path skips the approve when the allowance already covers the price', async () => {
  const wl = wallet({ code: '0xef01', allowance: PRICE });
  await buy(wl);
  assert.deepEqual(wl.s.sent.map((t) => t.to), [CFG.contract]);
});

test('permit simulation reverting with a sale error stops: no approve', async () => {
  for (const [data, kind] of [[CFG.errors.SoldOut, 'sold_out'], [CFG.errors.EnforcedPause, 'paused'],
    [CFG.errors.PriceAboveMax + w.word(PRICE + 1n) + w.word(PRICE), 'price_changed']]) {
    const wl = wallet({ simulate: Object.assign(new Error('execution reverted'), { code: 3, data }) });
    await rejects(buy(wl), kind);
    assert.equal(wl.s.sent.length, 0, kind);
  }
});

test('a simulation that fails for a non-revert reason (RPC down) never sends', async () => {
  const wl = wallet({ simulate: Object.assign(new Error('fetch failed'), { code: -32603 }) });
  await rejects(buy(wl), 'rpc');
  assert.equal(wl.s.sent.length, 0);
});

test('price changed since the page showed it: show the new one, sign nothing, send nothing', async () => {
  const wl = wallet({ price: PRICE + 1_000_000n });
  await assert.rejects(buy(wl), (e) => e.kind === 'price_changed' && e.price === PRICE + 1_000_000n);
  assert.ok(!wl.methods().includes('eth_signTypedData_v4'));
  assert.equal(wl.s.sent.length, 0);
  // a lower price also needs a new click: the buyer agreed to the number they saw
  await rejects(buy(wallet({ price: PRICE - 1n })), 'price_changed');
});

test('price changed while the approve was confirming: no preorder sent', async () => {
  const wl = wallet({ code: '0xef01' });
  wl.s.on = { eth_sendTransaction: (params, s) => { if (params[0].to === CFG.usdc) s.price = PRICE + 5n; } };
  await rejects(buy(wl), 'price_changed');
  assert.deepEqual(wl.s.sent.map((t) => t.to), [CFG.usdc]);
});

test('closed states refuse before anything is signed', async () => {
  for (const [over, kind] of [[{ paused: true }, 'paused'], [{ minted: 100n }, 'sold_out'], [{ price: 0n }, 'not_open'],
    [{ balance: PRICE - 1n }, 'low_balance']]) {
    const wl = wallet(over);
    await rejects(buy(wl), kind);
    assert.ok(!wl.methods().includes('eth_signTypedData_v4'), kind);
    assert.equal(wl.s.sent.length, 0, kind);
  }
});

test('buyer rejects the permit signature: stop, no fallback, nothing sent', async () => {
  const wl = wallet({ sign: Object.assign(new Error('User rejected the request.'), { code: 4001 }) });
  await rejects(buy(wl), 'cancelled');
  assert.equal(wl.s.sent.length, 0);
});

test('buyer rejects the send: cancelled, the pending record is cleared', async () => {
  const saved = [];
  const wl = wallet({ send: Object.assign(new Error('User denied transaction signature'), { code: 4001 }) });
  await rejects(buy(wl, { remember: (r) => saved.push(r) }), 'cancelled');
  assert.equal(saved.at(-1), null);
});

test('a send error that is not a rejection may have gone out: send_unknown keeps the pending record', async () => {
  const saved = [];
  const wl = wallet({ send: Object.assign(new Error('timeout'), { code: -32603 }) });
  wl.s.txCount = 12n;
  await assert.rejects(buy(wl, { remember: (r) => saved.push(r) }), (e) => e.kind === 'send_unknown' && e.fromBlock === '0x101' && e.from === BUYER && e.nonce === '12');
  assert.equal(saved.at(-1).nonce, '12');
  const q = wl.calls.find((c) => c.method === 'eth_getTransactionCount');
  assert.deepEqual(q.params, [BUYER, 'pending']);
  assert.equal(saved.at(-1).stage, 'sending');
});

test('never sends on another chain', async () => {
  const wl = wallet({ chainId: '0x1' });
  await rejects(buy(wl), 'wrong_chain');
  assert.equal(wl.s.sent.length, 0);
  // the wallet switches away between the approve and the preorder
  const wl2 = wallet({ code: '0xef01' });
  wl2.s.on = { eth_sendTransaction: (params, s) => { if (params[0].to === CFG.usdc) s.chainId = '0xa'; } };
  await rejects(buy(wl2), 'wrong_chain');
  assert.deepEqual(wl2.s.sent.map((t) => t.to), [CFG.usdc]);
});

test('the pending record is written before each wait, with the hash', async () => {
  const saved = [];
  const wl = wallet({ code: '0xef01' });
  await buy(wl, { remember: (r) => saved.push(r && { ...r }) });
  const stages = saved.map((r) => r && r.stage);
  assert.deepEqual(stages, ['approve', null, 'sending', 'preorder']);
  assert.equal(saved[0].hash, '0x' + w.word(1n));
  assert.equal(saved[3].hash, '0x' + w.word(2n));
  assert.equal(saved[3].from, BUYER);
  assert.equal(saved[3].contract, CFG.contract);
  assert.equal(saved[3].price, String(PRICE));
  assert.equal(saved[2].nonce, '0');                                        // pending nonce right before the prompt
  assert.equal(saved[3].nonce, '0');
});

test('ensureBase switches, adds Base when unknown, and verifies', async () => {
  const wl = wallet({ chainId: '0x1' });
  wl.s.on = { wallet_switchEthereumChain: (params, s) => { s.chainId = params[0].chainId; return null; } };
  await p.ensureBase(wl.eth, CFG);
  assert.equal(wl.s.chainId, '0x2105');

  const wl2 = wallet({ chainId: '0x1' });
  wl2.s.on = {
    wallet_switchEthereumChain: (params, s) => { if (!s.added) throw Object.assign(new Error('Unrecognized chain'), { code: 4902 }); s.chainId = params[0].chainId; return null; },
    wallet_addEthereumChain: (params, s) => { assert.equal(params[0].chainId, '0x2105'); s.added = true; return null; },
  };
  await p.ensureBase(wl2.eth, CFG);
  assert.deepEqual(wl2.methods().filter((m) => m.startsWith('wallet_')), ['wallet_switchEthereumChain', 'wallet_addEthereumChain', 'wallet_switchEthereumChain']);
  assert.equal(wl2.s.chainId, '0x2105');

  const wl3 = wallet({ chainId: '0x1' });
  wl3.s.on = { wallet_switchEthereumChain: () => null };   // claims success but stays put
  await rejects(p.ensureBase(wl3.eth, CFG), 'wrong_chain');

  const wl4 = wallet({ chainId: '0x1' });
  wl4.s.on = { wallet_switchEthereumChain: () => { throw Object.assign(new Error('no'), { code: 4001 }); } };
  await rejects(p.ensureBase(wl4.eth, CFG), 'cancelled');
});

const preorderedLog = (n, buyer = BUYER, tx = '0x' + 'ee'.repeat(32)) => ({
  address: CFG.contract, transactionHash: tx, topics: [CFG.preorderedTopic, hexw(n), '0x' + w.addrWord(buyer)] });

test('watchPurchase: device number from the receipt', async () => {
  const wl = wallet();
  const hash = '0x' + 'aa'.repeat(32);
  let polls = 0;
  wl.s.on = { eth_getTransactionReceipt: () => (++polls < 3 ? null : { status: '0x1', logs: [preorderedLog(42n)] }) };
  const r = await p.watchPurchase({ eth: wl.eth, cfg: CFG, hash, from: BUYER, fromBlock: '0x100', sleep: noSleep });
  assert.deepEqual(r, { device: 42n, hash });
});

test('watchPurchase: a sped-up (replaced) transaction is found by the Preordered log for this buyer', async () => {
  const wl = wallet({ logs: [preorderedLog(9n)] });
  const r = await p.watchPurchase({ eth: wl.eth, cfg: CFG, hash: '0x' + 'aa'.repeat(32), from: BUYER, fromBlock: '0x100', sleep: noSleep });
  assert.deepEqual(r, { device: 9n, hash: '0x' + 'ee'.repeat(32) });
  const q = wl.calls.find((c) => c.method === 'eth_getLogs').params[0];
  assert.deepEqual(q, { address: CFG.contract, fromBlock: '0x100', toBlock: 'latest', topics: [CFG.preorderedTopic, null, '0x' + w.addrWord(BUYER)] });
});

test('watchPurchase: reverted receipt fails; replaced without a log fails after two looks; RPC hiccups keep watching', async () => {
  const wl = wallet({ receipts: { ['0x' + 'aa'.repeat(32)]: { status: '0x0', logs: [] } } });
  await rejects(p.watchPurchase({ eth: wl.eth, cfg: CFG, hash: '0x' + 'aa'.repeat(32), from: BUYER, fromBlock: '0x100', sleep: noSleep }), 'failed');

  const wl2 = wallet({ txCount: 6n });   // our tx had nonce 5; the account moved past it with no receipt and no log
  await rejects(p.watchPurchase({ eth: wl2.eth, cfg: CFG, hash: '0x' + 'aa'.repeat(32), from: BUYER, fromBlock: '0x100', sleep: noSleep }), 'replaced');
  assert.equal(wl2.methods().filter((m) => m === 'eth_getTransactionCount').length, 2);

  let n = 0;
  const wl3 = wallet();
  wl3.s.on = { eth_getTransactionReceipt: () => { n++; if (n < 4) throw new Error('rate limited'); return { status: '0x1', logs: [preorderedLog(5n)] }; } };
  const r = await p.watchPurchase({ eth: wl3.eth, cfg: CFG, hash: '0x' + 'aa'.repeat(32), from: BUYER, fromBlock: '0x100', sleep: noSleep });
  assert.equal(r.device, 5n);
});

test('watchPurchase without a hash (send outcome unknown) gives up after maxPolls', async () => {
  const wl = wallet();
  await rejects(p.watchPurchase({ eth: wl.eth, cfg: CFG, hash: null, from: BUYER, fromBlock: '0x100', sleep: noSleep, maxPolls: 3 }), 'unconfirmed');
  assert.equal(wl.methods().filter((m) => m === 'eth_getLogs').length, 3);
  assert.ok(!wl.methods().includes('eth_sendTransaction'));
});

test('waitApproval: done once the allowance covers the price, fails on a reverted approve', async () => {
  const wl = wallet({ allowance: 0n });
  let polls = 0;
  wl.s.on = { eth_getTransactionReceipt: (params, s) => { if (++polls === 2) s.allowance = PRICE; return null; } };
  await p.waitApproval({ eth: wl.eth, cfg: CFG, hash: '0x' + 'aa'.repeat(32), from: BUYER, price: PRICE, sleep: noSleep });
  assert.equal(polls, 2);

  const wl2 = wallet({ receipts: { ['0x' + 'aa'.repeat(32)]: { status: '0x0', logs: [] } } });
  await rejects(p.waitApproval({ eth: wl2.eth, cfg: CFG, hash: '0x' + 'aa'.repeat(32), from: BUYER, price: PRICE, sleep: noSleep }), 'approve_failed');
});

test('isRejection recognises the usual wallet shapes', () => {
  assert.ok(p.isRejection({ code: 4001 }));
  assert.ok(p.isRejection({ code: 'ACTION_REJECTED' }));
  assert.ok(p.isRejection({ code: -32603, message: 'User rejected the request.' }));
  assert.ok(!p.isRejection({ code: -32603, message: 'internal error' }));
});

test('watchUnknown: nonce unchanged after the timeout -> unconfirmed, safe to re-enable', async () => {
  const wl = wallet({ txCount: 12n });
  let still = 0;
  await rejects(p.watchUnknown({ eth: wl.eth, cfg: CFG, from: BUYER, fromBlock: '0x101', nonce: '12', sleep: noSleep, maxPolls: 3, onStill: () => still++ }), 'unconfirmed');
  assert.equal(still, 0);
  assert.deepEqual(wl.calls.filter((c) => c.method === 'eth_getTransactionCount').map((c) => c.params), [[BUYER, 'pending']]);
  assert.ok(!wl.methods().includes('eth_sendTransaction'));
});

test('watchUnknown: nonce advanced -> a tx went out, keep watching until the Preordered log shows', async () => {
  const wl = wallet({ txCount: 13n });
  let logsCalls = 0, still = 0;
  wl.s.on = { eth_getLogs: () => (++logsCalls >= 8 ? [preorderedLog(77n)] : []) };
  const r = await p.watchUnknown({ eth: wl.eth, cfg: CFG, from: BUYER, fromBlock: '0x101', nonce: '12', sleep: noSleep, maxPolls: 3, onStill: () => still++ });
  assert.equal(r.device, 77n);
  assert.ok(still >= 2, 'showed "still confirming" instead of re-enabling');
});

test('watchUnknown: an unreadable nonce counts as "may have gone out"; no recorded nonce keeps the old timeout', async () => {
  const wl = wallet();
  let n = 0;
  wl.s.on = { eth_getTransactionCount: () => { throw new Error('rpc down'); }, eth_getLogs: () => (++n >= 5 ? [preorderedLog(3n)] : []) };
  assert.equal((await p.watchUnknown({ eth: wl.eth, cfg: CFG, from: BUYER, fromBlock: '0x101', nonce: '12', sleep: noSleep, maxPolls: 3 })).device, 3n);
  await rejects(p.watchUnknown({ eth: wallet().eth, cfg: CFG, from: BUYER, fromBlock: '0x101', nonce: null, sleep: noSleep, maxPolls: 2 }), 'unconfirmed');
});
