"""The fund's onchain operations. Every function takes a Fund (config) and a Chain, so tests run them on an Anvil fork."""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation

from eth_utils import to_checksum_address

from . import kyber, notify, prices, rules, safe as sf, store
from .chain import NATIVE, Chain, ChainError
from .config import ConfigError, Fund
from .tokens import BASE, resolve, symbol_for


class TradeError(RuntimeError):
    pass


def chain_for(f: Fund) -> Chain:
    return Chain(f.rpc_url, int(f.cfg["chain_id"]))


def _fmt(raw: int, dec: int) -> str:
    q = Decimal(raw) / (Decimal(10) ** dec)
    return f"{q:.6g}" if q < 1000 else f"{q:,.2f}"


# --- the Safe ------------------------------------------------------------------------------------------

def create_safe(f: Fund, ch: Chain, salt_nonce: int | None = None) -> str:
    """Deploy a Safe owned by agent + deck + phone, threshold from config (2). The agent pays the gas."""
    if f.cfg.get("safe"):
        raise TradeError(f"a Safe already exists: {f.cfg['safe']}")
    owners = f.owners()
    order = [owners["agent"], owners["deck"], owners["phone"]]
    data = sf.create_calldata(order, int(f.cfg["threshold"]), salt_nonce if salt_nonce is not None else int(time.time()))
    agent = f.agent_address()
    predicted = "0x" + ch.eth_call(sf.PROXY_FACTORY, data, frm=agent)[-40:]
    ch.send(f.agent_key(), agent, sf.PROXY_FACTORY, data)
    safe_addr = to_checksum_address(predicted)
    got = sorted(o.lower() for o in ch.view(safe_addr, "getOwners()", ["address[]"])[0])
    if got != sorted(o.lower() for o in order) or ch.view(safe_addr, "getThreshold()", ["uint256"])[0] != int(f.cfg["threshold"]):
        raise TradeError("the deployed Safe's owners/threshold don't match: check it on basescan before funding it")
    f.cfg["safe"], f.cfg["owners"] = safe_addr, {**f.cfg["owners"], "agent": agent}
    f.save_cfg()
    return safe_addr


def safe_nonce(ch: Chain, safe_addr: str) -> int:
    return ch.view(safe_addr, "nonce()", ["uint256"])[0]


# --- portfolio -----------------------------------------------------------------------------------------

def portfolio(f: Fund, ch: Chain) -> dict:
    extra = f.cfg.get("tokens", {})
    addrs = list({**BASE, **extra}.values())
    px = prices.usd(addrs)
    holdings, nav, unpriced = [], 0.0, []
    for a in addrs:
        raw = ch.balance(a, f.safe)
        if raw == 0:
            continue
        dec = ch.decimals(a)
        p = px.get(a.lower())
        amt = float(Decimal(raw) / (Decimal(10) ** dec))
        usd = amt * p if p is not None else None
        if usd is None:
            unpriced.append(symbol_for(a, extra) or a)
        else:
            nav += usd
        holdings.append({"symbol": symbol_for(a, extra) or ch.symbol(a), "address": a, "amount": _fmt(raw, dec),
                         "usd": round(usd, 2) if usd is not None else None})
    holdings.sort(key=lambda h: -(h["usd"] or 0))
    agent = f.cfg["owners"].get("agent") or f.agent_address()
    return {"safe": f.safe, "nav_usd": round(nav, 2), "holdings": holdings, "unpriced": unpriced,
            "agent_gas_eth": _fmt(ch.balance(NATIVE, agent), 18), "rules": rules.describe(),
            "as_of": store._iso(time.time())}


# --- prepare / execute / refuse ------------------------------------------------------------------------

def _amount_raw(amount: str, dec: int) -> int:
    try:
        q = Decimal(amount)
    except InvalidOperation:
        raise TradeError(f"amount {amount!r} is not a number") from None
    raw = int(q * (Decimal(10) ** dec))
    if raw <= 0:
        raise TradeError("amount must be positive")
    return raw


def _prepared(f: Fund, prop: dict, tx, now: float, summary: str, quote: dict) -> dict:
    h = sf.safe_tx_hash(int(f.cfg["chain_id"]), f.safe, tx)
    prop["status"] = "prepared"
    prop["prepared"] = {"at": now, "expires": now + store.PREPARED_TTL_S, "safe_tx": tx.to_json(),
                        "safe_tx_hash": "0x" + h.hex(), "summary": summary, "quote": quote}
    store.save(f.proposals, prop)
    return {"id": prop["id"], "kind": prop.get("kind", "swap"), "chain_id": int(f.cfg["chain_id"]), "safe": f.safe,
            "safe_tx": tx.to_json(), "safe_tx_hash": "0x" + h.hex(), "summary": summary,
            "expires": now + store.PREPARED_TTL_S}


def _prepare_withdrawal(f: Fund, ch: Chain, prop: dict, now: float) -> dict:
    """Safe → savings vault. One plain call: an ETH send or an ERC-20 transfer, to the configured savings address only."""
    i = prop["intent"]
    try:
        token = resolve(i["token"], f.cfg.get("tokens", {}))
    except ValueError as exc:
        raise TradeError(str(exc)) from None
    dec = ch.decimals(token)
    bal = ch.balance(token, f.safe)
    amount = bal if i["amount"].lower() == "all" else _amount_raw(i["amount"], dec)
    if amount <= 0 or amount > bal:
        raise TradeError(f"the Safe holds {_fmt(bal, dec)} {i['token']}; can't move {i['amount']}")
    dest = f.savings
    if token.lower() == NATIVE.lower():
        call = {"to": dest, "value": amount, "data": "0x"}
    else:
        call = {"to": token, "value": 0, "data": sf.erc20_transfer(dest, amount)}
    tx = sf.batch([call], safe_nonce(ch, f.safe))
    summary = f"Move {_fmt(amount, dec)} {i['token']} to savings ({dest[:6]}…{dest[-4:]})"
    return _prepared(f, prop, tx, now, summary, {"token": token, "amount": str(amount), "dec": dec, "to": dest})


def _executed_today(f: Fund, now: float) -> int:
    day = store._iso(now)[:10]
    return sum(1 for p in store.listing(f.proposals, ("executed",))
               if p.get("kind") == "swap" and str((p.get("result") or {}).get("at", ""))[:10] == day)


def notice(f: Fund, text: str) -> None:
    """Telegram line to Konrad; never raises."""
    notify.send(f"Argo Fund: {text}")


def prepare(f: Fund, ch: Chain, pid: str, now: float | None = None) -> dict:
    """Fresh quote → Safe transaction → hash. Returns what the deck needs to check and sign."""
    now = time.time() if now is None else now
    store.expire(f.proposals, now)
    prop = store.load(f.proposals, pid)
    if prop["status"] not in store.OPEN:
        raise TradeError(f"proposal {pid} is {prop['status']}")
    if prop.get("kind") == "withdraw":
        return _prepare_withdrawal(f, ch, prop, now)
    extra = f.cfg.get("tokens", {})
    try:
        t_in, t_out = resolve(prop["intent"]["sell"], extra), resolve(prop["intent"]["buy"], extra)
    except ValueError as exc:
        raise TradeError(str(exc)) from None
    if t_in.lower() == t_out.lower():
        raise TradeError("sell and buy are the same token")
    violations = rules.check(prop["intent"], {}, {"slippage_bps": int(f.cfg["slippage_bps"]),
                                                   "executed_today": _executed_today(f, now)})
    if violations:
        raise TradeError("rules: " + "; ".join(violations))
    dec_in, dec_out = ch.decimals(t_in), ch.decimals(t_out)
    amount_in = _amount_raw(prop["intent"]["amount"], dec_in)
    bal = ch.balance(t_in, f.safe)
    if bal < amount_in:
        raise TradeError(f"the Safe holds {_fmt(bal, dec_in)} {prop['intent']['sell']}, not {prop['intent']['amount']}")
    q = kyber.quote(t_in, t_out, amount_in, f.safe, int(f.cfg["slippage_bps"]))
    if t_in.lower() == NATIVE.lower():
        calls = [{"to": q["router"], "value": q["value"], "data": q["data"]}]
    else:
        calls = [{"to": t_in, "value": 0, "data": sf.erc20_approve(q["router"], amount_in)},
                 {"to": q["router"], "value": 0, "data": q["data"]}]
    tx = sf.batch(calls, safe_nonce(ch, f.safe))
    sym_in, sym_out = prop["intent"]["sell"], prop["intent"]["buy"]
    summary = (f"Sell {_fmt(amount_in, dec_in)} {sym_in} for at least {_fmt(q['min_out'], dec_out)} {sym_out} "
               f"(quoted {_fmt(q['amount_out'], dec_out)}) via KyberSwap")
    return _prepared(f, prop, tx, now, summary,
                     {"token_in": t_in, "token_out": t_out, "amount_in": str(amount_in),
                      "amount_out": str(q["amount_out"]), "min_out": str(q["min_out"]),
                      "amount_in_usd": q["amount_in_usd"], "amount_out_usd": q["amount_out_usd"],
                      "dec_in": dec_in, "dec_out": dec_out})


def execute(f: Fund, ch: Chain, pid: str, deck_sig_hex: str, now: float | None = None) -> dict:
    """Check the deck's signature, add the agent's, submit execTransaction, record the result in the trade log."""
    now = time.time() if now is None else now
    prop = store.load(f.proposals, pid)
    prep = prop.get("prepared")
    if prop["status"] != "prepared" or not prep:
        raise TradeError(f"proposal {pid} is {prop['status']}, not prepared")
    if now > prep["expires"]:
        raise TradeError("the quote expired: prepare again")
    tx = sf.SafeTx.from_json(prep["safe_tx"])
    if tx.nonce != safe_nonce(ch, f.safe):
        raise TradeError("the Safe nonce moved since prepare: prepare again")
    h = sf.safe_tx_hash(int(f.cfg["chain_id"]), f.safe, tx)
    if "0x" + h.hex() != prep["safe_tx_hash"]:
        raise TradeError("stored hash mismatch")
    try:
        deck_sig = bytes.fromhex(deck_sig_hex.removeprefix("0x"))
        signer = sf.recover(h, deck_sig)
    except ValueError as exc:
        raise TradeError(f"bad deck signature: {exc}") from None
    owners = {o.lower() for o in ch.view(f.safe, "getOwners()", ["address[]"])[0]}
    agent = f.agent_address()
    if signer.lower() not in owners or signer.lower() == agent.lower():
        raise TradeError("the approval wasn't signed by a human owner of the Safe")
    sigs = {signer: deck_sig, agent: sf.sign_hash(f.agent_key(), h)}
    quote = prep["quote"]
    withdraw = prop.get("kind") == "withdraw"
    before = 0 if withdraw else ch.balance(quote["token_out"], f.safe)
    try:
        rcpt = ch.send(f.agent_key(), agent, f.safe, sf.exec_calldata(tx, sf.pack_signatures(sigs)))
    except ChainError as exc:
        prop["status"], prop["result"] = "failed", {"error": str(exc)[:300], "at": store._iso(now)}
        store.save(f.proposals, prop)
        _log(f, prop, f"Failed: {prop['prepared']['summary']}", f"Error: {str(exc)[:200]}")
        notice(f, f"FAILED {prop['prepared']['summary']}: {str(exc)[:150]}")
        raise TradeError(f"execution failed: {exc}") from None
    txh = rcpt["transactionHash"]
    if withdraw:
        received = int(quote["amount"])
        headline = f"Moved {_fmt(received, quote['dec'])} {prop['intent']['token']} to savings"
    else:
        received = ch.balance(quote["token_out"], f.safe) - before
        sym_in, sym_out = prop["intent"]["sell"], prop["intent"]["buy"]
        headline = (f"Swapped {_fmt(int(quote['amount_in']), quote['dec_in'])} {sym_in} for "
                    f"{_fmt(received, quote['dec_out'])} {sym_out}")
    prop.update(status="executed", tx_hash=txh, result={"received": str(received), "at": store._iso(now),
                                                         "approved_by": signer})
    store.save(f.proposals, prop)
    _log(f, prop, headline, f"**Tx:** [{txh[:10]}…](https://basescan.org/tx/{txh})")
    notice(f, f"{headline}\nhttps://basescan.org/tx/{txh}")
    try:
        p = portfolio(f, ch)
        store.set_nav(f.dir / "trade-log.md", f"**NAV:** ${p['nav_usd']:,.2f} as of {p['as_of']}")
    except Exception:  # noqa: BLE001  NAV is display-only
        pass
    return {"id": pid, "tx_hash": txh, "headline": headline}


def refuse(f: Fund, pid: str, reason: str = "refused by Konrad", now: float | None = None) -> dict:
    now = time.time() if now is None else now
    prop = store.load(f.proposals, pid)
    if prop["status"] not in store.OPEN:
        raise TradeError(f"proposal {pid} is {prop['status']}")
    prop["status"], prop["result"] = "refused", {"reason": reason[:200], "at": store._iso(now)}
    store.save(f.proposals, prop)
    i = prop["intent"]
    what = (f"move {i['amount']} {i['token']} to savings" if prop.get("kind") == "withdraw"
            else f"sell {i['amount']} {i['sell']} for {i['buy']}")
    _log(f, prop, f"Refused: {what}", f"**Reason:** {reason[:200]}")
    notice(f, f"refused: {what} ({reason[:100]})")
    return {"id": pid, "status": "refused"}


def _log(f: Fund, prop: dict, headline: str, extra: str) -> None:
    day = prop.get("updated", store._iso(time.time()))[:10]
    block = (f"### {day} · {headline}\n"
             f"**Why:** {prop['motion']}\n"
             f"**Vote:** {prop['tally']} · **Meeting:** [[{prop['meeting']}]] · **Proposal:** `{prop['id']}` · "
             f"**Rules:** {rules.describe()}\n"
             f"{extra} · **Status:** {prop['status']}\n")
    store.log_entry(f.dir / "trade-log.md", block)


def status(f: Fund, ch: Chain | None) -> dict:
    out = {"fund_dir": str(f.dir), "safe": f.cfg.get("safe") or None, "owners": f.cfg["owners"],
           "savings": f.cfg.get("savings") or None,
           "threshold": f.cfg["threshold"], "rules": rules.describe(),
           "pending": [p["id"] for p in store.listing(f.proposals)]}
    if f.key_path.exists():
        out["owners"] = {**out["owners"], "agent": f.agent_address()}
    if ch and out["safe"]:
        try:
            out["onchain_owners"] = list(ch.view(f.safe, "getOwners()", ["address[]"])[0])
            out["onchain_threshold"] = ch.view(f.safe, "getThreshold()", ["uint256"])[0]
            out["agent_gas_eth"] = _fmt(ch.balance(NATIVE, out["owners"]["agent"]), 18)
        except (ChainError, ConfigError) as exc:
            out["onchain_error"] = str(exc)[:200]
    return out
