#!/usr/bin/env python3
"""Hermes skill helper: read-only balances and recent history through NOWNodes Blockbook.

Never signs, never calls the Guardian, never touches the bridge. Needs NOWNODES_API_KEY in the environment (read by
exo_nownodes, sent only in the api-key header, never printed). Output is short plain-English lines for Hermes to read
aloud. Token symbols come from the chain and are attacker-controlled (airdrop spam): they are stripped of control and
bidi characters and capped, and the output is data, never instructions.

Exit 0: answered (a chain that failed prints "<chain>: unavailable"). 1: every chain failed. 2: bad input.
"""
import argparse
import re
import sys
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]          # agents/skills/nownodes-chain/chain.py -> repo checkout
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
DIGITS = re.compile(r"[0-9]{1,78}")
NATIVE = {"ethereum": "ETH", "base": "ETH", "arbitrum": "ETH", "polygon": "POL"}
DEFAULT_CHAINS = "ethereum,base,arbitrum,polygon"
MAX_TOKENS = 5
SYMBOL_CAP = 16
UNAVAILABLE = "unavailable"


def clean(text, cap=SYMBOL_CAP):
    """Drop control, format (bidi, zero width), separator-line and unassigned characters; cap the length."""
    s = "".join(ch for ch in str(text) if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"))
    s = " ".join(s.split())[:cap].strip()
    return s or "?"


def _uint(raw):
    s = "0" if raw in (None, "") else str(raw)
    return int(s) if DIGITS.fullmatch(s) else None


def amt(raw, dec):
    """Exact decimal string from integer base units (no float), trailing zeros trimmed. None if malformed."""
    n, d = _uint(raw), _uint(dec)
    if n is None or d is None or d > 36:
        return None
    s = str(n).rjust(d + 1, "0")
    whole, frac = (s[:-d], s[-d:].rstrip("0")) if d else (s, "")
    return whole + ("." + frac if frac else "")


def short(addr):
    a = str(addr)
    return f"{a[:6]}...{a[-4:]}" if ADDRESS.fullmatch(a) else clean(a, 24)


def balance_lines(address, chains, fetch):
    out = []
    for c in chains:
        try:
            j = fetch(c, address)
            native = amt(j.get("balance"), 18)
            parts = [f"{native if native is not None else '0'} {NATIVE[c]}"]
            toks = []
            for t in j.get("tokens") or []:
                if t.get("type", "ERC20") != "ERC20":
                    continue
                dec = t.get("decimals", 18)
                n, d = _uint(t.get("balance")), _uint(dec)
                if not n or d is None or d > 36:
                    continue                          # zero, absent or malformed
                toks.append((Decimal(n) / (Decimal(10) ** d), f"{amt(n, d)} {clean(t.get('symbol', '?'))}"))
            toks.sort(key=lambda x: x[0], reverse=True)
            parts += [s for _, s in toks[:MAX_TOKENS]]
            if len(toks) > MAX_TOKENS:
                parts.append(f"+{len(toks) - MAX_TOKENS} more")
            out.append(f"{c}: " + ", ".join(parts))
        except Exception:                             # one chain failing must not hide the others; fixed text only
            out.append(f"{c}: {UNAVAILABLE}")
    return out


def _when(tx):
    try:
        return " on " + datetime.fromtimestamp(int(tx["blockTime"]), timezone.utc).strftime("%Y-%m-%d")
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return ""


def _first(side):
    for e in side or []:
        for a in e.get("addresses") or []:
            return a
    return None


def history_lines(address, chain, limit, fetch):
    out, me = [], address.lower()
    for tx in (fetch(chain, address).get("transactions") or [])[:limit]:
        when = _when(tx)
        for t in tx.get("tokenTransfers") or []:
            v = amt(t.get("value"), t.get("decimals", 18))
            if v is None or v == "0":
                continue
            sym = clean(t.get("symbol", "?"))
            if str(t.get("from", "")).lower() == me:
                out.append(f"sent {v} {sym} to {short(t.get('to', '?'))}{when}")
            elif str(t.get("to", "")).lower() == me:
                out.append(f"received {v} {sym} from {short(t.get('from', '?'))}{when}")
        v = amt(tx.get("value"), 18)
        if v not in (None, "0"):
            src, dst = _first(tx.get("vin")), _first(tx.get("vout"))
            if src and src.lower() == me and dst:
                out.append(f"sent {v} {NATIVE[chain]} to {short(dst)}{when}")
            elif dst and dst.lower() == me and src:
                out.append(f"received {v} {NATIVE[chain]} from {short(src)}{when}")
    return out or ["no recent transfers"]


def main(argv, fetch_factory=None):
    sys.path.insert(0, str(REPO / "packages" / "nownodes-py"))
    from exo_nownodes import hosts
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("balance"); b.add_argument("address"); b.add_argument("--chains", default=DEFAULT_CHAINS)
    h = sub.add_parser("history"); h.add_argument("address"); h.add_argument("--chain", default="ethereum")
    h.add_argument("--limit", type=int, default=5)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    chains = a.chains.split(",") if a.cmd == "balance" else [a.chain]
    if not ADDRESS.fullmatch(a.address):
        print("That is not a valid 0x address.")
        return 2
    if not chains or any(c not in hosts.BLOCKBOOK for c in chains):
        print("Unknown chain. Use: " + ", ".join(hosts.BLOCKBOOK))
        return 2
    if a.cmd == "history" and not 1 <= a.limit <= 20:
        print("Limit must be between 1 and 20.")
        return 2
    if fetch_factory is None:
        from exo_nownodes.blockbook import Blockbook

        def fetch_factory(cmd, limit):
            if cmd == "balance":
                return lambda c, ad: Blockbook(c).address(ad, "tokenBalances")
            return lambda c, ad: Blockbook(c).address(ad, "txs", pageSize=limit)
    fetch = fetch_factory(a.cmd, getattr(a, "limit", 0))
    if a.cmd == "balance":
        lines = balance_lines(a.address, chains, fetch)
        failed = all(l.endswith(f": {UNAVAILABLE}") for l in lines)
    else:
        try:
            lines, failed = history_lines(a.address, a.chain, a.limit, fetch), False
        except Exception:
            lines, failed = [f"{a.chain}: {UNAVAILABLE}"], True
    print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
