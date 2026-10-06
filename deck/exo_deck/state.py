"""Shared deck state on disk: which panel the kiosk shows, the conversation log, status chips.
Files, not a server, so the voice hooks, deck-buttons and the kiosk can all touch it."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

PANELS: tuple[str, ...] = ("talk", "agents", "approvals", "transactions", "wallets", "cre", "body", "sensors", "media")


def _read(path: Path, default: str = "") -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return default


def _write(path: Path, text: str) -> None:
    """Atomic write via a unique temp file in the same dir (several processes write state)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def get_panel(state: Path) -> str:
    p = _read(state / "panel", "talk")
    return p if p in PANELS else "talk"


def set_panel(state: Path, target: str) -> str | None:
    target = target.strip().lower()
    cur = PANELS.index(get_panel(state))
    if target == "next":
        new = PANELS[(cur + 1) % len(PANELS)]
    elif target == "previous":
        new = PANELS[(cur - 1) % len(PANELS)]
    elif target.isdigit() and 1 <= int(target) <= len(PANELS):
        new = PANELS[int(target) - 1]
    elif target in PANELS:
        new = target
    else:
        return None
    _write(state / "panel", new)
    _write(state / "page", "0")
    return new


def page(state: Path, delta: int | None) -> int:
    try:
        cur = int(_read(state / "page", "0"))
    except ValueError:
        cur = 0
    new = 0 if delta is None else max(0, cur + delta)
    _write(state / "page", str(new))
    return new


def append_turn(state: Path, role: str, text: str, now: float | None = None, cap: int = 500) -> None:
    log = state / "conversation.jsonl"
    with _locked(state / ".conversation.lock"):
        lines = log.read_text().splitlines() if log.exists() else []
        lines.append(json.dumps({"ts": time.time() if now is None else now, "role": role, "text": text}))
        _write(log, "\n".join(lines[-cap:]) + "\n")


def recent_turns(state: Path, n: int = 7) -> list[dict]:
    log = state / "conversation.jsonl"
    out = []
    for line in (log.read_text().splitlines() if log.exists() else [])[-n:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def snapshot(state: Path) -> dict:
    queue = state / "tx-queue"
    try:
        pg = int(_read(state / "page", "0"))
    except ValueError:
        pg = 0
    return {
        "panel": get_panel(state), "page": pg,
        "listening": (state / "listening").exists(),
        "mode": _read(state / "approve-mode", "manual").lower() or "manual",
        "pending": len(list(queue.glob("*.json"))) if queue.exists() else 0,
        "frozen": (state / "frozen").exists(),
        "heard": _read(state / "last-heard.txt")[:300],
        "reply": _read(state / "last-reply.txt")[:300],
    }
