"""The Agents panel: the exo-bridge kanban board as four columns, newest cards first."""
from __future__ import annotations

from collections import Counter

from .bridge_client import BridgeError

COLS = {"triage": "queued", "todo": "queued", "blocked": "queued", "ready": "ready", "running": "running", "done": "done"}


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _slim(t: dict) -> dict:
    """Only what the panel draws; titles are capped so one long card cannot wreck a column."""
    return {"id": str(t.get("id") or "")[:40], "title": str(t.get("title") or "")[:120],
            "assignee": str(t.get("assignee") or "")[:24], "status": t["status"],
            "created_at": t.get("created_at") if _num(t.get("created_at")) else None}


def columns(tasks: list[dict], per_col: int = 5) -> dict:
    live = [t for t in tasks if isinstance(t, dict) and t.get("status") in COLS]
    cols = {c: [] for c in ("queued", "ready", "running", "done")}
    for t in sorted(live, key=lambda t: _num(t.get("created_at")), reverse=True):
        col = cols[COLS[t["status"]]]
        if len(col) < per_col:
            col.append(_slim(t))
    return {"online": True, "cols": cols, "counts": dict(Counter(t["status"] for t in live))}


def panel(bridge) -> dict:
    try:
        return columns(bridge.board())
    except BridgeError:
        return {"online": False, "cols": {}, "counts": {}}
