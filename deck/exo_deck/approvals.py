"""The approval queue the NOWNodes key works through, shared by deck-buttons and the kiosk.

Auto mode approves only Guardian-marked low-risk, auto-eligible, unexpired items. Everything else
waits for a key press in both modes. Turning auto ON needs a key press within 5 s of the request.
An unreadable or malformed item is never auto-approved."""
from __future__ import annotations

import json
from pathlib import Path

from .state import _read, _write

PAGE = 7
AUTO_WINDOW_S = 5.0


def mode(state: Path) -> str:
    return "auto" if _read(state / "approve-mode").lower() == "auto" else "manual"


def set_mode(state: Path, m: str) -> None:
    _write(state / "approve-mode", ("auto" if m == "auto" else "manual") + "\n")
    if m != "auto":
        (state / "auto-request").unlink(missing_ok=True)   # "auto approve off" cancels a pending ask


def request_auto(state: Path, now: float) -> None:
    _write(state / "auto-request", str(now))


def _asked_at(state: Path) -> float | None:
    try:
        return float(_read(state / "auto-request"))
    except ValueError:
        return None


def confirm_auto(state: Path, now: float, window_s: float = AUTO_WINDOW_S) -> bool:
    """A key press: turns auto on only if a request is fresh. One request, one confirmation."""
    asked = _asked_at(state)
    (state / "auto-request").unlink(missing_ok=True)
    if asked is not None and 0 <= now - asked <= window_s:
        set_mode(state, "auto")
        return True
    return False


def _load(path: Path) -> dict:
    try:
        item = json.loads(path.read_text())
        if not isinstance(item, dict):
            raise ValueError
    except (OSError, ValueError):
        item = {"summary": "(unreadable)", "risk": "high", "auto_eligible": False}
    item["file"] = path.name
    item.setdefault("id", path.stem)
    return item


def _expiry(item: dict) -> float | None:
    exp = item.get("expires_at")
    return float(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None


def pending(state: Path, now: float) -> list[dict]:
    """Queue, oldest first. Expired items are moved to tx-expired/ first and never returned."""
    queue, expired = state / "tx-queue", state / "tx-expired"
    out = []
    for p in sorted(queue.glob("*.json")) if queue.exists() else []:
        item = _load(p)
        exp = _expiry(item)
        if exp is not None and exp < now:
            try:
                expired.mkdir(parents=True, exist_ok=True)
                p.replace(expired / p.name)
            except OSError:
                pass            # already approved or moved by another process
            continue
        out.append(item)
    return out


def next_auto(items: list[dict], now: float) -> dict | None:
    """First item the Guardian marked low-risk and auto-eligible, with a real unexpired expiry."""
    for i in items:
        exp = _expiry(i)
        if i.get("auto_eligible") is True and i.get("risk") == "low" and exp is not None and exp >= now:
            return i
    return None


def next_manual(items: list[dict]) -> dict | None:
    return items[0] if items else None


def recent_approved(state: Path, n: int = PAGE) -> list[dict]:
    d = state / "tx-approved"
    return [_load(p) for p in sorted(d.glob("*.json"), reverse=True)[:n]] if d.exists() else []


def panel(state: Path, now: float, page: int) -> dict:
    items = pending(state, now)
    asked = _asked_at(state)
    return {"mode": mode(state), "pending_total": len(items),
            "pending": items[page * PAGE:(page + 1) * PAGE],
            "approved": recent_approved(state),
            "auto_request": asked is not None and 0 <= now - asked <= AUTO_WINDOW_S}
