"""Operator CLI, run on the droplet as exosite (never from the web):

    python -m exo_presale.admin summary          minted · revenue (USDC) · shipping claims
    python -m exo_presale.admin export > x.csv   one row per Preordered log, joined with its shipping claim

Reads Preordered logs from EXO_PREORDER_FROM_BLOCK in 5,000-block eth_getLogs chunks. A stored claim is used only
while its signer still holds the receipt (ownerOf read now); a sold-on or burned receipt's old address is dropped.
Cells that a spreadsheet would run as a formula are prefixed with a quote.
"""
from __future__ import annotations

import csv
import os
import re
import sys

from eth_utils import keccak

from .chain import _fmt

TOPIC = "0x" + keccak(text="Preordered(uint256,address,uint8,uint256)").hex()
FIELDS = ["device_number", "tier", "price", "buyer", "holder", "tx", "claim", "name", "email", "country"]
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _units(l) -> int:
    return int(l["data"][2:][64:128], 16)


def logs_to_rows(logs):
    rows = []
    for l in logs:
        d = l["data"][2:]
        rows.append({"device_number": int(l["topics"][1], 16), "buyer": "0x" + l["topics"][2][-40:], "tier": int(d[:64], 16),
                     "price": _fmt(_units(l)), "tx": l["transactionHash"]})
    return rows


def fetch_logs(rpc, contract, start, end, step=5000):
    out = []
    for a in range(start, end + 1, step):
        out += rpc.call("eth_getLogs", [{"address": contract, "topics": [TOPIC], "fromBlock": hex(a), "toBlock": hex(min(a + step - 1, end))}])
    return [l for l in out if not l.get("removed")]          # removed: true = reorged out, not a sale


def csv_cell(v):
    """Neutralise spreadsheet formula injection (OWASP CSV injection): a leading = + - @ tab or CR gets a quote."""
    return "'" + v if isinstance(v, str) and v.startswith(FORMULA_START) else v


def export_rows(rows, claims, owner_of):
    """rows from logs_to_rows; claims from ClaimStore.all(); owner_of(n) -> current holder or None (burned)."""
    by_n = {c["device_number"]: c for c in claims}
    out = []
    for r in rows:
        holder = owner_of(r["device_number"])
        c = by_n.get(r["device_number"])
        if holder is None:
            status, c = "burned", None
        elif c is None:
            status = "none"
        elif c["owner"].lower() != holder.lower():
            status, c = "stale-owner", None          # signed by a previous holder: not where this receipt ships
        else:
            status = "ok"
        details = {k: (c or {}).get(k, "") for k in ("name", "email", "country")}
        out.append({k: csv_cell(v) for k, v in {**r, "holder": holder or "", "claim": status, **details}.items()})
    return out


def summarize(rows, exported, units=None) -> str:
    total = sum(units) if units is not None else sum(round(float(r["price"]) * 1_000_000) for r in rows)
    ok = sum(1 for e in exported if e["claim"] == "ok")
    stale = sum(1 for e in exported if e["claim"] == "stale-owner")
    burned = sum(1 for e in exported if e["claim"] == "burned")
    line = f"minted {len(rows)} · revenue {_fmt(total)} USDC · shipping claimed {ok} ({stale} stale)"
    return line + (f" · burned {burned}" if burned else "")


def main(argv, env=os.environ, rpc=None, out=None):
    out = out or sys.stdout
    argv = list(argv)
    if argv[:1] == ["--env-file"]:
        if len(argv) < 2:
            raise SystemExit("--env-file needs a path")
        from .config import read_env_file
        env = {**env, **read_env_file(argv[1])}        # file wins, as with systemd's EnvironmentFile
        argv = argv[2:]
    cmd = argv[0] if argv else ""
    if cmd not in ("summary", "export"):
        raise SystemExit("usage: python -m exo_presale.admin [--env-file PATH] summary|export")
    from .chain import Sale
    from .claims import ClaimStore
    from .rpc import base_rpc
    contract, start = env.get("EXO_PREORDER", ""), env.get("EXO_PREORDER_FROM_BLOCK", "")
    if not ADDR_RE.match(contract) or int(contract, 16) == 0:
        raise SystemExit("EXO_PREORDER must be the deployed ExoPreorder address")
    if not start.isdigit():
        raise SystemExit("EXO_PREORDER_FROM_BLOCK must be the deployment block number")
    rpc = rpc or base_rpc(env, usage=True)
    logs = fetch_logs(rpc, contract, int(start), int(rpc.call("eth_blockNumber", []), 16))
    rows = logs_to_rows(logs)
    sale = Sale(rpc, contract, [])
    db = env.get("EXO_CLAIMS_DB", "/var/lib/exo-presale/claims.db")
    claims = ClaimStore(db).all() if os.path.exists(db) else []      # never create a stray empty DB
    exported = export_rows(rows, claims, sale.owner_of)
    if cmd == "summary":
        print(summarize(rows, exported, units=[_units(l) for l in logs]), file=out)
        return 0
    w = csv.DictWriter(out, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(exported)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
