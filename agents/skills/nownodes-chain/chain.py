#!/usr/bin/env python3
"""Hermes skill helper: read-only balances and recent history through NOWNodes Blockbook.

Never signs, never calls the Guardian, never touches the bridge. Needs NOWNODES_API_KEY in the environment (read by
exo_nownodes, sent only in the api-key header, never printed). Output is short plain-English lines for Hermes to read
aloud. Token symbols come from the chain and are attacker-controlled (airdrop spam): they are stripped of control and
bidi characters and capped, and the output is data, never instructions.

Wallets: an address argument may be a 0x address or a label from the wallets file (EXO_WALLETS_FILE, default
~/.config/exo/wallets.json, schema {"<label>": {"<chain>": "0x..."}}, public addresses only). `balance` with no
address covers every wallet in the file.

Exit 0: answered (a chain that failed prints "<chain>: unavailable"). 1: every chain failed. 2: bad input.
"""
import argparse
import json
import os
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
NO_WALLETS = "No wallets file yet \u2014 give me an address, or create ~/.config/exo/wallets.json"
BAD_WALLETS = "The wallets file is not valid. Each label needs chain names mapped to 0x addresses."


class WalletsError(Exception):
    pass


def load_wallets(path=None, chain_names=NATIVE):
    """{label: {chain: address}}; every chain and address is validated. Raises WalletsError (fixed text only)."""
    p = Path(path or os.environ.get("EXO_WALLETS_FILE") or "~/.config/exo/wallets.json").expanduser()
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        raise WalletsError(NO_WALLETS)
    if not isinstance(data, dict) or not data:
        raise WalletsError(BAD_WALLETS)
    for label, m in data.items():
        if (not isinstance(m, dict) or not m or clean(label, 40) != label
                or any(c not in chain_names or not isinstance(a, str) or not ADDRESS.fullmatch(a) for c, a in m.items())):
            raise WalletsError(BAD_WALLETS)
    return data


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
            native = amt(j["balance"], 18) if j.get("balance") not in (None, "") else None
            if native is None:
                raise ValueError("no native balance")
            parts = [f"{native} {NATIVE[c]}"]
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
        if not isinstance(tx, dict):
            continue
        when = _when(tx)
        for t in tx.get("tokenTransfers") or []:
            if not isinstance(t, dict):
                continue
            v = amt(t.get("value"), t.get("decimals", 18))
            if v == "0":
                continue
            v = v if v is not None else "an unknown amount of"
            sym = clean(t.get("symbol", "?"))
            if str(t.get("from", "")).lower() == me:
                out.append(f"sent {v} {sym} to {short(t.get('to', '?'))}{when}")
            elif str(t.get("to", "")).lower() == me:
                out.append(f"received {v} {sym} from {short(t.get('from', '?'))}{when}")
        v = amt(tx.get("value"), 18)
        if v != "0":
            v = v if v is not None else "an unknown amount of"
            src, dst = _first(tx.get("vin")), _first(tx.get("vout"))
            if src and src.lower() == me and dst:
                out.append(f"sent {v} {NATIVE[chain]} to {short(dst)}{when}")
            elif dst and dst.lower() == me and src:
                out.append(f"received {v} {NATIVE[chain]} from {short(src)}{when}")
    return out or ["no recent transfers"]


def _resolve(a, hosts):
    """-> list of (prefix, address, chains) or an error line. Raises WalletsError."""
    want = None
    if a.cmd == "balance" and a.chains:
        want = a.chains.split(",")
        if not want or any(c not in hosts.BLOCKBOOK for c in want):
            return "Unknown chain. Use: " + ", ".join(hosts.BLOCKBOOK)
    if a.cmd == "history" and a.chain not in hosts.BLOCKBOOK:
        return "Unknown chain. Use: " + ", ".join(hosts.BLOCKBOOK)
    if a.address and ADDRESS.fullmatch(a.address):
        return [("", a.address, want or list(hosts.BLOCKBOOK) if a.cmd == "balance" else [a.chain])]
    wallets = load_wallets(chain_names=hosts.BLOCKBOOK)
    if a.address:
        if a.address not in wallets:
            return "That is neither a 0x address nor a wallet label in the wallets file."
        wallets = {a.address: wallets[a.address]}
    elif a.cmd == "history":
        return "Give me an address or wallet label for history."
    out = []
    for label, m in wallets.items():
        chains = [c for c in m if (want is None or c in want) and (a.cmd == "balance" or c == a.chain)]
        for c in chains:
            out.append((label, m[c], [c]))
        if a.cmd == "history" and not chains:
            return f"Wallet {label} has no {a.chain} address."
    return out or "No matching wallet addresses."


def main(argv, fetch_factory=None):
    sys.path.insert(0, str(REPO / "packages" / "nownodes-py"))
    from exo_nownodes import hosts
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("balance"); b.add_argument("address", nargs="?"); b.add_argument("--chains", default=None)
    h = sub.add_parser("history"); h.add_argument("address", nargs="?"); h.add_argument("--chain", default="ethereum")
    h.add_argument("--limit", type=int, default=5)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    if a.address and not ADDRESS.fullmatch(a.address) and not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", a.address):
        print("That is not a valid 0x address or wallet label.")
        return 2
    if a.cmd == "history" and not 1 <= a.limit <= 20:
        print("Limit must be between 1 and 20.")
        return 2
    try:
        targets = _resolve(a, hosts)
    except WalletsError as e:
        print(e)
        return 2
    if isinstance(targets, str):
        print(targets)
        return 2
    if fetch_factory is None:
        from exo_nownodes.blockbook import Blockbook

        def fetch_factory(cmd, limit):
            if cmd == "balance":
                return lambda c, ad: Blockbook(c).address(ad, "tokenBalances")
            return lambda c, ad: Blockbook(c).address(ad, "txs", pageSize=limit)
    fetch = fetch_factory(a.cmd, getattr(a, "limit", 0))
    lines, ok = [], 0
    for label, addr, chains in targets:
        pre = f"{label}, " if label else ""
        if a.cmd == "balance":
            got = balance_lines(addr, chains, fetch)
        else:
            try:
                got = history_lines(addr, chains[0], a.limit, fetch)
            except Exception:
                got = [f"{chains[0]}: {UNAVAILABLE}"]
        ok += sum(not l.endswith(f": {UNAVAILABLE}") for l in got)
        lines += [pre + l for l in got]
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
