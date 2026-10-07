#!/usr/bin/env python3
"""Hermes skill: build a transaction and propose it to the Exo Transaction Guardian. Never signs anything.

The skill holds no ledger credentials and does not import the Guardian store. It reaches the Guardian only through
exo-bridge, by reusing chain/runner/exo_guardian/cli.py (POST $EXO_BRIDGE_URL/guard with $EXO_BRIDGE_TOKEN).

Environment:
  EXO_BRIDGE_URL, EXO_BRIDGE_TOKEN   consumed by cli.py (never printed here)
  EXO_SAFE                           the agent Safe the transaction is proposed from
  EXO_ADDRESS_BOOK                   path to a JSON file {address: label}; default ~/.config/exo/address-book.json
  EXO_GUARDIAN_CLI                   override path to exo_guardian/cli.py (default: <repo>/chain/runner/exo_guardian/cli.py)

Exit 0: the Guardian answered (approve or refuse). 1: outcome unknown or Guardian unavailable (do NOT retry).
2: bad input; nothing was sent.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHAIN_ID = 1
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
PLAIN_DECIMAL = re.compile(r"^[0-9]+(\.[0-9]+)?$")
UINT = re.compile(r"^[0-9]+$")
HEXBYTES = re.compile(r"^0x([0-9a-fA-F]{2})*$")
SOURCE = re.compile(r"^(voice|agent:[a-z0-9][a-z0-9_-]{0,40})$")
TRANSFER = "0xa9059cbb"

UNKNOWN = ("Outcome unknown: the Guardian did not answer in time, and the proposal may still be queued. "
           "Check the approvals panel before doing anything. Do not retry yet.")


def _units(amount: str, decimals: int) -> int:
    if not isinstance(amount, str) or not PLAIN_DECIMAL.match(amount):
        raise ValueError(f"bad amount {amount!r}: use a plain decimal like 20 or 0.01")
    whole, _, frac = amount.partition(".")
    if len(frac) > decimals:
        raise ValueError(f"{amount} has more than {decimals} decimal places")
    v = int(whole) * 10**decimals + int(frac.ljust(decimals, "0") or "0")  # pure integer math, no rounding
    if v <= 0:
        raise ValueError(f"bad amount {amount}")
    return v


def _check_checksum(addr: str) -> None:
    body = addr[2:]
    if body == body.lower() or body == body.upper():
        return  # lowercase / all-caps carry no checksum
    try:
        from eth_utils import is_checksum_address
    except ImportError:
        raise ValueError("mixed-case address cannot be checksum-verified here; pass it lowercase")
    if not is_checksum_address(addr):
        raise ValueError(f"{addr} fails its checksum; check it for typos")


def _address(a: str) -> str:
    if not isinstance(a, str) or not ADDRESS.match(a):
        raise ValueError(f"{a!r} is not a 0x address")
    _check_checksum(a)
    return a.lower()


def _resolve(to: str, book: dict) -> str:
    if isinstance(to, str) and to.startswith("0x"):
        return _address(to)
    for addr, label in book.items():
        if isinstance(label, str) and isinstance(to, str) and label.lower() == to.lower():
            return _address(addr)
    raise ValueError(f"{to} is not in your address book")


def _source(source: str) -> str:
    if not isinstance(source, str) or not SOURCE.match(source):
        raise ValueError("source must be 'voice' or 'agent:<profile>'")
    return source


def send_request(token, amount, to, summary, source, tokens, book, safe):
    dest = _resolve(to, book)
    sender = _address(safe)
    if token.upper() == "ETH":
        tx = {"chain_id": CHAIN_ID, "to": dest, "value": str(_units(amount, 18)), "data": "0x"}
        tok = "ETH"
    else:
        t = tokens.get(token.upper())
        if t is None:
            raise ValueError(f"unknown token {token}; known: ETH, {', '.join(sorted(tokens))}")
        units = _units(amount, t["decimals"])
        data = TRANSFER + dest[2:].rjust(64, "0") + hex(units)[2:].rjust(64, "0")
        tx = {"chain_id": CHAIN_ID, "to": t["address"].lower(), "value": "0", "data": data}
        tok = t["address"].lower()
    return {"source": _source(source), "from": sender, "tx": tx,
            "intent": {"kind": "send", "summary": summary, "token": tok, "amount": amount, "to": dest}}


def raw_request(to, value, data, summary, source, safe):
    if not isinstance(value, str) or not UINT.match(value):
        raise ValueError("value must be a decimal wei amount")
    if not isinstance(data, str) or not HEXBYTES.match(data):
        raise ValueError("data must be 0x-prefixed hex bytes")
    return {"source": _source(source), "from": _address(safe),
            "intent": {"kind": "raw", "summary": summary},
            "tx": {"chain_id": CHAIN_ID, "to": _address(to), "value": value, "data": data.lower()}}


def spoken(result: dict) -> str:
    expl = result.get("explanation") or "Refused."
    if result.get("verdict") == "approve":
        if result.get("queued") is False:
            return f"{expl} This was a dry run: nothing was queued for the approve key."
        return f"{expl} Press the approve key to send it."
    return expl


def _load_cli():
    path = Path(os.environ.get("EXO_GUARDIAN_CLI") or HERE.parents[2] / "chain" / "runner" / "exo_guardian" / "cli.py")
    spec = importlib.util.spec_from_file_location("exo_guardian_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def propose(req: dict, env=os.environ, post=None, cli=None):
    """Send req through cli.py. Returns (exit_code, message). No retry, ever."""
    cli = cli or _load_cli()
    kwargs = {"env": env}
    if post is not None:
        kwargs["post"] = post
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["guard", json.dumps(req)], **kwargs)
    try:
        body = json.loads(out.getvalue()) if out.getvalue().strip() else None
    except ValueError:
        body = None
    if code == 0 and isinstance(body, dict) and body.get("verdict") in ("approve", "refuse"):
        return 0, spoken(body)
    if code == 2:
        return 2, "The Guardian could not take that request (bad setup or invalid request). Nothing was queued."
    if isinstance(body, dict) and body.get("proposal_id") and body.get("error"):
        return 1, ("Refused: the Guardian was unavailable, so nothing was queued. "
                   "Tell the wearer; do not retry on your own.")
    return 1, UNKNOWN


def main(argv, env=os.environ, post=None) -> int:
    ap = argparse.ArgumentParser(description="Propose a transaction through the Exo Guardian")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    for a in ("--token", "--amount", "--to", "--summary"):
        s.add_argument(a, required=True)
    s.add_argument("--source", default="agent:default")
    r = sub.add_parser("raw")
    for a in ("--to", "--value", "--data", "--summary"):
        r.add_argument(a, required=True)
    r.add_argument("--source", default="agent:default")
    a = ap.parse_args(argv)
    try:
        safe = env.get("EXO_SAFE", "")
        if not safe:
            raise ValueError("EXO_SAFE is not set")
        if a.cmd == "send":
            tokens = json.loads((HERE / "tokens.json").read_text())
            book_path = Path(env.get("EXO_ADDRESS_BOOK") or Path.home() / ".config" / "exo" / "address-book.json")
            try:
                book = json.loads(book_path.read_text())
            except OSError:
                book = {}  # raw 0x recipients still work; labels then fail with "not in your address book"
            if not isinstance(book, dict):
                raise ValueError("address book must be a JSON object of address to label")
            req = send_request(a.token, a.amount, a.to, a.summary, a.source, tokens, book, safe)
        else:
            req = raw_request(a.to, a.value, a.data, a.summary, a.source, safe)
    except ValueError as exc:
        print(f"Not sent: {exc}")
        return 2
    code, msg = propose(req, env=env, post=post)
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
