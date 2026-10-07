"""Wallet and asset balances from NOWNodes Blockbook, cached so the kiosk's 1.5 s polling stays cheap.

The wallets file is the same one the nownodes-chain skill reads: {"<label>": {"<chain>": "0x<address>"}}, public
addresses only. Token symbols are attacker-controlled (anyone can airdrop a token named anything), so they are
cleaned here and the kiosk still renders them through esc()."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable

log = logging.getLogger("exo-chain-view")

NATIVE = {"ethereum": "ETH", "base": "ETH", "arbitrum": "ETH", "polygon": "POL"}   # all four have 18 decimals
PAGE = 5            # two-line rows: five fit the safe area (the AR rule allows at most seven)
MAX_TOKENS = 3      # per wallet row; the rest collapse into "+n"
SYMBOL_CAP = 16
LABEL_CAP = 24
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
DIGITS = re.compile(r"[0-9]{1,80}")


def clean(text, cap: int = SYMBOL_CAP) -> str:
    """Drop control, format (bidi, zero width), separator-line and unassigned characters; cap the length."""
    s = "".join(ch for ch in str(text) if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"))
    return " ".join(s.split())[:cap].strip() or "?"


def fmt_units(raw, decimals) -> str | None:
    """Exact decimal text from integer base units, at most 4 decimals, truncated (never shows more than is owned).
    Pure int arithmetic: no float, no Decimal context rounding. None if malformed."""
    raw = "0" if raw in (None, "") else str(raw)
    dec = str(decimals)
    if not DIGITS.fullmatch(raw) or not DIGITS.fullmatch(dec) or int(dec) > 36:
        return None
    n, d = int(raw), int(dec)
    if n == 0:
        return "0"
    whole, frac = divmod(n, 10 ** d)
    frac4 = str(frac).rjust(d, "0")[:4].rstrip("0") if d else ""
    if whole == 0 and not frac4:
        return "<0.0001"
    return f"{whole}.{frac4}" if frac4 else str(whole)


def load_wallets(path=None) -> list[dict]:
    """Flatten the wallets file into [{label, chain, address}]. Missing or unreadable file: []. A bad entry is skipped."""
    p = Path(path or os.environ.get("EXO_WALLETS_FILE", "/srv/deck/wallets.json"))
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    out = []
    if isinstance(data, dict):
        for label, chains in data.items():
            if not isinstance(chains, dict):
                continue
            for chain, addr in chains.items():
                if isinstance(addr, str) and ADDRESS.fullmatch(addr) and isinstance(chain, str):
                    out.append({"label": clean(label, LABEL_CAP), "chain": chain, "address": addr})
    return out


def _tokens(j: dict) -> list[dict]:
    out = []
    for t in j.get("tokens") or []:
        if not isinstance(t, dict) or t.get("type", "ERC20") != "ERC20":
            continue
        if str(t.get("balance", "0")) in ("0", ""):
            continue                                    # zero-balance airdrop spam never shows
        amount = fmt_units(t.get("balance"), t.get("decimals", 18))
        if amount is not None:
            out.append({"symbol": clean(t.get("symbol", "?")), "amount": amount})
    return out


def _spawn(fn: Callable) -> None:
    threading.Thread(target=fn, daemon=True, name="exo-balances").start()


class Balances:
    """`client_for(chain)` returns an exo_nownodes Blockbook (anything with .address(addr, details=...)).

    panel() never blocks on the network: it answers from the cache at once (or a loading state the first time) and
    starts at most one background refresh when the cache is older than the TTL. The cache age is taken when a
    refresh FINISHES, so a slow refresh cannot trigger another one back to back. Rows older than the TTL are
    marked stale. `spawn` runs the refresh (a daemon thread by default; tests pass a synchronous one)."""

    def __init__(self, client_for: Callable, wallets: list[dict], ttl_s: int = 60, clock: Callable = time.time,
                 spawn: Callable = _spawn):
        self.client_for, self.wallets, self.ttl, self.clock, self.spawn = client_for, wallets, ttl_s, clock, spawn
        self._cache: dict | None = None
        self._at = 0.0
        self._good: dict = {}
        self._running = threading.Lock()      # held for the duration of one refresh

    def _refresh(self) -> dict:
        rows, errors = [], []
        for w in self.wallets:
            key = (w["label"], w["chain"], w["address"])
            try:
                if w["chain"] not in NATIVE:
                    raise ValueError("unknown chain")
                j = self.client_for(w["chain"]).address(w["address"], details="tokenBalances")
                native = fmt_units(j.get("balance"), 18)
                if native is None:
                    raise ValueError("bad balance")
                tokens = _tokens(j)
                row = {"label": w["label"], "chain": w["chain"], "address_short": w["address"][:6] + "\u2026" + w["address"][-4:],
                       "native": f"{native} {NATIVE[w['chain']]}", "tokens": tokens[:MAX_TOKENS],
                       "tokens_more": max(0, len(tokens) - MAX_TOKENS), "stale": False}
                self._good[key] = row
                rows.append(row)
            except Exception as exc:    # one chain down must not blank the rest; the text may hold a URL, so only log it
                log.warning("balance %s on %s failed: %s", w["label"], w["chain"], type(exc).__name__)
                errors.append(f"{w['label']} on {w['chain']}: unavailable")
                if key in self._good:
                    rows.append(dict(self._good[key], stale=True))
        return {"wallets": rows, "errors": errors}

    def _run(self) -> None:
        try:
            result = self._refresh()
            self._cache, self._at = result, self.clock()     # age starts when the refresh finished
        except Exception as exc:  # noqa: BLE001
            log.warning("balance refresh failed: %s", type(exc).__name__)
        finally:
            self._running.release()

    def _kick(self) -> None:
        if self._running.acquire(blocking=False):
            try:
                self.spawn(self._run)
            except Exception:  # noqa: BLE001  (could not start a thread: free the slot, serve the cache)
                self._running.release()

    def panel(self, page: int) -> dict:
        now = self.clock()
        cache, at = self._cache, self._at
        if cache is None or now - at > self.ttl:
            self._kick()
        if cache is None:
            return {"wallets": [], "more": 0, "errors": [], "age_s": 0, "loading": True}
        old = now - at > self.ttl
        rows = [dict(r, stale=True) if old else r for r in cache["wallets"]]
        page = max(0, min(page, (len(rows) - 1) // PAGE)) if rows else 0
        return {"wallets": rows[page * PAGE:(page + 1) * PAGE], "more": max(0, len(rows) - (page + 1) * PAGE),
                "errors": cache["errors"][:3], "age_s": int(max(0, now - at)), "loading": False}
