"""The kiosk's Fund panel: the proposal the approve key would sign, how the five partners voted, and the trade log.

Proposal text is written by LLM partners, so every field is cleaned and capped here and esc()'d in the kiosk."""
from __future__ import annotations

from .bridge_client import BridgeError
from .chain_view import clean

VOTES = ("yes", "no", "abstain", "block")


def _t(v, cap: int) -> str:
    return clean(v, cap) if isinstance(v, str) and v else ""


def _proposal(p: dict) -> dict:
    i = p.get("intent") if isinstance(p.get("intent"), dict) else {}
    votes = [{"partner": _t(v.get("partner"), 24), "vote": v.get("vote") if v.get("vote") in VOTES else "?",
              "reason": _t(v.get("reason"), 140)} for v in (p.get("votes") or []) if isinstance(v, dict)][:5]
    return {"id": _t(p.get("id"), 16), "motion": _t(p.get("motion"), 220), "tally": _t(p.get("tally"), 60),
            "trade": (f"Move {_t(i.get('amount'), 20)} {_t(i.get('token'), 12)} to savings" if p.get("kind") == "withdraw"
                      else f"Sell {_t(i.get('amount'), 20)} {_t(i.get('sell'), 12)} for {_t(i.get('buy'), 12)}"),
            "status": _t(p.get("status"), 12), "votes": votes}


def panel(bridge, page: int) -> dict:
    try:
        props = [p for p in bridge.fund_proposals() if isinstance(p, dict)]
    except BridgeError:
        return {"error": "Fund unreachable"}
    try:
        lg = bridge.fund_log()
    except BridgeError:
        lg = {"nav": "", "entries": [], "thesis": ""}
    sel = props[page % len(props)] if props else None
    entries = [_t(e.split("\n", 1)[0].removeprefix("### "), 110) for e in (lg.get("entries") or []) if isinstance(e, str)][:4]
    return {"pending_total": len(props), "index": (page % len(props)) if props else 0,
            "proposal": _proposal(sel) if sel else None, "nav": _t(lg.get("nav"), 80),
            "thesis": _t(lg.get("thesis"), 240), "log": entries}
