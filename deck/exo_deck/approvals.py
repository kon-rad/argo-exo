"""The approval queue the NOWNodes key works through, shared by deck-buttons and the kiosk.

Auto mode approves only Guardian-marked low-risk, auto-eligible, unexpired items. Everything else
waits for a key press in both modes. Turning auto ON needs a key press within 5 s of the request.
An unreadable or malformed item is never auto-approved."""
from __future__ import annotations

import json
from pathlib import Path

from .state import _read, _write

PAGE = 7              # rows for plain lists
PENDING_PAGE = 5      # two-line pending rows that fit the 900 px safe area
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


def _read_item(path: Path) -> dict | None:
    """The queue item with `file` and `id` filled in, or None if unreadable / not a JSON object."""
    try:
        item = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(item, dict):
        return None
    item["file"] = path.name
    item.setdefault("id", path.stem)
    return item


def _load(path: Path) -> dict:
    return _read_item(path) or {"summary": "(unreadable)", "risk": "high", "auto_eligible": False,
                                "file": path.name, "id": path.stem}


def _move(state: Path, path: Path, folder: str) -> None:
    try:
        (state / folder).mkdir(parents=True, exist_ok=True)
        path.replace(state / folder / path.name)
    except OSError:
        pass            # already approved or moved by another process


def _expiry(item: dict) -> float | None:
    exp = item.get("expires_at")
    return float(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None


def pending(state: Path, now: float) -> list[dict]:
    """Queue, oldest first. NOTE: this mutates the queue. Expired items move to tx-expired/ and
    unreadable ones to tx-rejected/ before it returns, so neither can ever be approved."""
    queue = state / "tx-queue"
    out = []
    for p in sorted(queue.glob("*.json")) if queue.exists() else []:
        item = _read_item(p)
        if item is None:
            _move(state, p, "tx-rejected")
            continue
        exp = _expiry(item)
        if exp is not None and exp < now:
            _move(state, p, "tx-expired")
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


def release(state: Path, item: dict, now: float, auto: bool = False) -> Path | None:
    """Approve one item: re-read it from the queue and re-check expiry (and, for auto, eligibility)
    right before the atomic rename into tx-approved/. Returns the new path, or None if not approved."""
    src = state / "tx-queue" / item["file"]
    fresh = _read_item(src)
    if fresh is None:
        if src.exists():
            _move(state, src, "tx-rejected")
        return None
    exp = _expiry(fresh)
    if exp is not None and exp < now:
        _move(state, src, "tx-expired")
        return None
    if auto and next_auto([fresh], now) is None:
        return None
    dest = state / "tx-approved" / item["file"]
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dest)           # atomic: approved at most once
    except OSError:
        return None
    return dest


def on_key(state: Path, now: float, last_press: float, gap_s: float = 1.0) -> tuple[str, Path | None]:
    """What one press of the approve key does: ("ignored", None) inside the bounce gap or with an
    empty queue, ("confirmed-auto", None) if it answered a fresh auto request (and approves nothing),
    else ("approved", dest) for the oldest pending item. The caller sets last_press = now unless ignored."""
    if now - last_press < gap_s:
        return "ignored", None
    if confirm_auto(state, now):
        return "confirmed-auto", None
    item = next_manual(pending(state, now))
    dest = release(state, item, now) if item else None
    return ("approved", dest) if dest else ("ignored", None)


def on_tick_auto(state: Path, now: float, last_press: float, gap_s: float = 1.0) -> Path | None:
    """Auto mode: approve one low-risk, auto-eligible, unexpired item (at most one per gap)."""
    if mode(state) != "auto" or now - last_press < gap_s:
        return None
    item = next_auto(pending(state, now), now)
    return release(state, item, now, auto=True) if item else None


def recent_approved(state: Path, n: int = PAGE) -> list[dict]:
    d = state / "tx-approved"
    return [_load(p) for p in sorted(d.glob("*.json"), reverse=True)[:n]] if d.exists() else []


def panel(state: Path, now: float, page: int) -> dict:
    items = pending(state, now)
    asked = _asked_at(state)
    return {"mode": mode(state), "pending_total": len(items),
            "pending_more": max(0, len(items) - (page + 1) * PENDING_PAGE),
            "pending": [dict(i, explanation=str(i.get("explanation") or "")[:120])
                        for i in items[page * PENDING_PAGE:(page + 1) * PENDING_PAGE]],
            "approved": recent_approved(state),
            "auto_request": asked is not None and 0 <= now - asked <= AUTO_WINDOW_S}
