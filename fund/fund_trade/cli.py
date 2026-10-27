"""fund-trade CLI. Every command prints one JSON object on stdout (errors: {"error": ...} and exit 1).

    fund-trade init-agent                       create the agent key if missing; print its address
    fund-trade create-safe [--salt N]           deploy the 2-of-3 Safe (agent, deck, phone); agent pays gas
    fund-trade status                           Safe, owners, threshold, gas, pending proposals, rules
    fund-trade portfolio                        holdings and NAV (the committee's brief)
    fund-trade propose --meeting D --motion T --sell S --buy B --amount A --votes JSON
    fund-trade proposals [--all]                open proposals (or all)
    fund-trade show ID                          one proposal, with votes and views
    fund-trade prepare ID                       fresh quote → Safe tx → hash for the deck to sign
    fund-trade execute ID SIGNATURE             add the agent's signature and submit
    fund-trade withdraw --token T --amount A|all [--reason R]   move funds from the Safe to the savings vault
    fund-trade refuse ID [--reason R]
    fund-trade log [--n N]                      newest trade-log entries + the constitution's current thesis
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from . import service, store
from .chain import ChainError
from .config import ConfigError, Fund


def _thesis(f: Fund) -> str:
    c = f.dir / "constitution.md"
    if not c.exists():
        return ""
    m = re.search(r"^## .*Current thesis.*?\n(.*?)(?=^## |\Z)", c.read_text(), re.S | re.M)
    return m.group(1).strip()[:1500] if m else ""


def _log_entries(f: Fund, n: int) -> list[str]:
    p = f.dir / "trade-log.md"
    if not p.exists() or store.ENTRIES_MARK not in p.read_text():
        return []
    body = p.read_text().split(store.ENTRIES_MARK, 1)[1]
    return [("### " + e).strip() for e in body.split("\n### ") if e.strip()][:n] if body.strip() else []


def _nav_line(f: Fund) -> str:
    p = f.dir / "trade-log.md"
    for ln in (p.read_text().splitlines() if p.exists() else []):
        if ln.rstrip().endswith(store.NAV_MARK):
            return ln.replace(store.NAV_MARK, "").strip()
    return ""


def run(argv: list[str], env=None) -> dict:
    ap = argparse.ArgumentParser(prog="fund-trade")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-agent")
    c = sub.add_parser("create-safe")
    c.add_argument("--salt", type=int)
    sub.add_parser("status")
    sub.add_parser("portfolio")
    p = sub.add_parser("propose")
    for a in ("--meeting", "--motion", "--sell", "--buy", "--amount", "--votes"):
        p.add_argument(a, required=True)
    w = sub.add_parser("withdraw")
    w.add_argument("--token", required=True)
    w.add_argument("--amount", required=True)
    w.add_argument("--reason", default="Move to savings")
    w.add_argument("--meeting", default="")
    w.add_argument("--votes", default="")
    ls = sub.add_parser("proposals")
    ls.add_argument("--all", action="store_true")
    for name in ("show", "prepare", "refuse"):
        s = sub.add_parser(name)
        s.add_argument("id")
        if name == "refuse":
            s.add_argument("--reason", default="refused by Konrad")
    e = sub.add_parser("execute")
    e.add_argument("id")
    e.add_argument("signature")
    lg = sub.add_parser("log")
    lg.add_argument("--n", type=int, default=10)
    a = ap.parse_args(argv)

    import os
    f = Fund.from_env(env if env is not None else os.environ)
    if a.cmd == "init-agent":
        return {"agent": f.init_agent_key()}
    if a.cmd == "create-safe":
        return {"safe": service.create_safe(f, service.chain_for(f), a.salt), "owners": f.cfg["owners"]}
    if a.cmd == "status":
        return service.status(f, service.chain_for(f))
    if a.cmd == "portfolio":
        return service.portfolio(f, service.chain_for(f))
    if a.cmd == "propose":
        try:
            votes = json.loads(a.votes)
        except json.JSONDecodeError:
            raise store.StoreError("--votes must be JSON") from None
        prop = store.create(f.proposals, meeting=a.meeting, motion=a.motion,
                            intent={"sell": a.sell, "buy": a.buy, "amount": a.amount}, votes=votes)
        service.notice(f, f"proposal {prop['id']} waiting for your approval: sell {a.amount} {a.sell} for {a.buy} "
                          f"(committee {prop['tally']}). Run m4-fund-approve on the M4.")
        return {"id": prop["id"], "tally": prop["tally"], "status": prop["status"]}
    if a.cmd == "withdraw":
        f.savings                                            # fail now, not at approve time, if it's unset
        votes = json.loads(a.votes) if a.votes else None
        prop = store.create_withdrawal(f.proposals, token=a.token, amount=a.amount, reason=a.reason,
                                       meeting=a.meeting, votes=votes)
        service.notice(f, f"withdrawal {prop['id']} waiting for your approval: {a.amount} {a.token} to savings. Run m4-fund-approve on the M4.")
        return {"id": prop["id"], "status": prop["status"], "to": f.savings}
    if a.cmd == "proposals":
        store.expire(f.proposals)
        return {"proposals": store.listing(f.proposals, None if a.all else store.OPEN)[::-1]}
    if a.cmd == "show":
        return store.load(f.proposals, a.id)
    if a.cmd == "prepare":
        return service.prepare(f, service.chain_for(f), a.id)
    if a.cmd == "execute":
        return service.execute(f, service.chain_for(f), a.id, a.signature)
    if a.cmd == "refuse":
        return service.refuse(f, a.id, a.reason)
    if a.cmd == "log":
        return {"nav": _nav_line(f), "entries": _log_entries(f, a.n), "thesis": _thesis(f)}
    raise AssertionError(a.cmd)


def main(argv=None) -> int:
    try:
        out = run(sys.argv[1:] if argv is None else argv)
    except (ConfigError, store.StoreError, service.TradeError, ChainError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
