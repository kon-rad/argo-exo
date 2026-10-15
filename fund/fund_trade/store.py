"""Proposal files (state/proposals/<id>.json) and the plain-English trade log (trade-log.md).

Proposal status: proposed → prepared → executed | failed | refused | expired. A prepared proposal that isn't
executed within PREPARED_TTL_S simply gets prepared again (fresh quote, fresh Safe hash)."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

PROPOSAL_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{1,3}$")
EXPIRE_S = 48 * 3600
PREPARED_TTL_S = 120
OPEN = ("proposed", "prepared")
VOTES = ("yes", "no", "abstain", "block")
ENTRIES_MARK = "<!-- entries: newest first, written by fund-trade -->"
NAV_MARK = "<!-- nav -->"


class StoreError(RuntimeError):
    pass


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def path_for(proposals: Path, pid: str) -> Path:
    if not PROPOSAL_ID.match(pid or ""):
        raise StoreError(f"bad proposal id {pid!r}")
    return proposals / f"{pid}.json"


def load(proposals: Path, pid: str) -> dict:
    p = path_for(proposals, pid)
    if not p.exists():
        raise StoreError(f"no proposal {pid}")
    return json.loads(p.read_text())


def save(proposals: Path, prop: dict) -> None:
    prop["updated"] = _iso(_now())
    _write_json(path_for(proposals, prop["id"]), prop)


def validate_votes(votes) -> list[dict]:
    if not isinstance(votes, list) or not votes:
        raise StoreError("votes must be a non-empty list of {partner, vote, reason}")
    out = []
    for v in votes:
        if not isinstance(v, dict) or not isinstance(v.get("partner"), str) or v.get("vote") not in VOTES:
            raise StoreError(f"bad vote {v!r}: vote must be one of {VOTES}")
        out.append({"partner": v["partner"][:40], "vote": v["vote"], "reason": str(v.get("reason", ""))[:400],
                    "view": str(v.get("view", ""))[:600]})
    return out


def tally(votes: list[dict]) -> str:
    yes = sum(v["vote"] == "yes" for v in votes)
    no = sum(v["vote"] in ("no", "block") for v in votes)
    against = [v["partner"].split()[0] for v in votes if v["vote"] in ("no", "block")]
    return f"{yes}–{no}" + (f" ({', '.join(against)} against)" if against else "")


def _next_id(proposals: Path, now: float) -> str:
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    proposals.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(p.stem.rsplit("-", 1)[1]) for p in proposals.glob(f"{day}-*.json")] or [0])
    return f"{day}-{n}"


def _new(proposals: Path, now: float, **fields) -> dict:
    prop = {"id": _next_id(proposals, now), "created": _iso(now), "created_ts": now, **fields,
            "status": "proposed", "prepared": None, "tx_hash": None, "result": None}
    save(proposals, prop)
    return prop


def create(proposals: Path, *, meeting: str, motion: str, intent: dict, votes: list, now: float | None = None) -> dict:
    """A swap the committee passed."""
    now = _now() if now is None else now
    for k in ("sell", "buy", "amount"):
        if not isinstance(intent.get(k), str) or not intent[k].strip():
            raise StoreError(f"intent.{k} is required (strings: sell/buy are a symbol or 0x address, amount is in tokens)")
    votes = validate_votes(votes)
    if sum(v["vote"] == "yes" for v in votes) < 3 or any(v["vote"] == "block" for v in votes):
        raise StoreError("a trade needs at least 3 yes votes and no constitutional block")
    return _new(proposals, now, kind="swap", meeting=meeting[:40], motion=motion[:600],
                intent={"kind": "swap", **{k: intent[k].strip() for k in ("sell", "buy", "amount")}},
                votes=votes, tally=tally(votes))


def create_withdrawal(proposals: Path, *, token: str, amount: str, reason: str, requested_by: str = "Konrad",
                      meeting: str = "", votes: list | None = None, now: float | None = None) -> dict:
    """Move funds from the Safe to the savings vault (the Pi Zero's address). Requested by Konrad, or passed by the
    committee as a profit-taking motion (then with its votes). The destination is never a parameter: it's the
    configured savings address, and the deck checks it again before signing."""
    now = _now() if now is None else now
    if not isinstance(token, str) or not token.strip() or not isinstance(amount, str) or not amount.strip():
        raise StoreError("withdraw needs a token and an amount (a number, or 'all')")
    v = validate_votes(votes) if votes else []
    return _new(proposals, now, kind="withdraw", meeting=meeting[:40], motion=(reason or "Move to savings")[:600],
                intent={"kind": "withdraw", "token": token.strip(), "amount": amount.strip()},
                votes=v, tally=tally(v) if v else f"requested by {requested_by[:40]}")


def expire(proposals: Path, now: float | None = None) -> list[str]:
    now = _now() if now is None else now
    gone = []
    for p in sorted(proposals.glob("*.json")) if proposals.exists() else []:
        prop = json.loads(p.read_text())
        if prop.get("status") in OPEN and now - float(prop.get("created_ts", now)) > EXPIRE_S:
            prop["status"] = "expired"
            save(proposals, prop)
            gone.append(prop["id"])
    return gone


def listing(proposals: Path, statuses=OPEN) -> list[dict]:
    out = []
    for p in sorted(proposals.glob("*.json")) if proposals.exists() else []:
        prop = json.loads(p.read_text())
        if statuses is None or prop.get("status") in statuses:
            out.append(prop)
    return out


# --- trade-log.md ----------------------------------------------------------------------------------------

def log_entry(log_path: Path, block: str) -> None:
    text = log_path.read_text() if log_path.exists() else f"# Trade Log\n\n{NAV_MARK}\n\n{ENTRIES_MARK}\n"
    if ENTRIES_MARK not in text:
        text = text.rstrip() + f"\n\n{ENTRIES_MARK}\n"
    head, tail = text.split(ENTRIES_MARK, 1)
    log_path.write_text(head + ENTRIES_MARK + "\n\n" + block.strip() + "\n" + tail)


def set_nav(log_path: Path, line: str) -> None:
    if not log_path.exists():
        return
    lines = log_path.read_text().split("\n")
    for i, ln in enumerate(lines):
        if ln.rstrip().endswith(NAV_MARK):
            lines[i] = f"{line} {NAV_MARK}"
            log_path.write_text("\n".join(lines))
            return
