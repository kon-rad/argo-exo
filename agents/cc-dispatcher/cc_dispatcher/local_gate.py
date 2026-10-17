"""The local model gate: Hermes profiles whose model runs on the home model host (the M1 Max) wait while it is off.

Hermes's own dispatcher (in the default profile's gateway) spawns these profiles for their cards. With the host off a
worker fails three times, exits without `complete`, and Hermes blocks the card (`gave_up`): safe, since the profiles
have no cloud fallback, but it needs a human `unblock`. This gate makes the wait automatic:

- host down: every `ready` card assigned to a local profile is parked with `schedule` ("waiting on time, not human
  input"), so Hermes's dispatcher leaves it alone;
- host back: the parked cards, plus any card of a local profile that Hermes gave up on during the outage (it was
  claimed in the seconds before the gate noticed), go back to `ready` with `unblock`.

The org runner asks `up` before it wakes a `runtime: hermes` bot, so its DMs and routines wait too. State in
runs_dir/local-gate.json survives a restart. Design: Areas/argo/second-brain/macbook-home-lab/dev-log/ in the vault.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .board import Board, BoardError

log = logging.getLogger("cc-dispatcher.local")
AUTHOR = "cc-dispatcher"
GAVE_UP_WINDOW = 600     # a gave_up this many seconds before the gate saw the host go down still counts as the outage


def http_probe(url: str, timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:      # noqa: S310 (fixed URL from lanes.toml)
            return 200 <= r.status < 300
    except Exception:
        return False


@dataclass
class GateState:
    down_since: float | None = None
    parked: dict = field(default_factory=dict)       # task id -> board name

    @classmethod
    def load(cls, path: Path) -> "GateState":
        try:
            return cls(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__))
        tmp.replace(path)


class LocalGate:
    def __init__(self, health_url: str, profiles: tuple[str, ...], boards: dict[str, Board], state_path: Path,
                 probe: Callable[[str], bool] = http_probe, down_after: int = 2):
        self.health_url, self.profiles, self.boards = health_url, profiles, boards
        self.state_path, self.probe, self.down_after = state_path, probe, down_after
        self.state = GateState.load(state_path)
        self._fails = 0
        self.up = self.state.down_since is None

    def tick(self, now: float) -> None:
        ok = self.probe(self.health_url)
        self._fails = 0 if ok else self._fails + 1
        if ok:
            self.up = True
            if self.state.down_since is not None or self.state.parked:
                self._release(now)
            return
        if self._fails < self.down_after:
            return                       # one failed probe is a blip, not an outage
        if self.up or self.state.down_since is None:
            log.warning("local model host unreachable (%s); parking cards for %s", self.health_url, ", ".join(self.profiles))
            self.state.down_since = self.state.down_since or now
            self.state.save(self.state_path)
        self.up = False
        self._park()

    def _park(self) -> None:
        for board_name, board in self.boards.items():
            for profile in self.profiles:
                for t in self._safe(board.tasks, profile, "ready") or []:
                    if self._safe(board.schedule, t["id"], "waiting for the local model host (it is unreachable); "
                                  "cc-dispatcher returns this card to ready when it answers again") is not None:
                        self.state.parked[t["id"]] = board_name
                        self.state.save(self.state_path)
                        log.info("parked %s (%s) until the local model host is back", t["id"], profile)

    def _release(self, now: float) -> None:
        since = self.state.down_since
        released = []
        for task_id, board_name in list(self.state.parked.items()):
            board = self.boards.get(board_name)
            if board is not None and self._safe(board.unblock, task_id, "the local model host is back") is not None:
                released.append(task_id)
            self.state.parked.pop(task_id, None)
        if since is not None:
            for board in self.boards.values():
                for profile in self.profiles:
                    for t in self._safe(board.tasks, profile, "blocked") or []:
                        if self._gave_up_during(board, t["id"], since, now) and self._safe(board.unblock, t["id"], "the local model host is back") is not None:
                            released.append(t["id"])
        log.info("local model host is back after %.0f s; released %s", now - since if since else 0, ", ".join(released) or "nothing")
        self.state.down_since = None
        self.state.save(self.state_path)

    def _gave_up_during(self, board: Board, task_id: str, since: float, now: float) -> bool:
        data = self._safe(board.show, task_id) or {}
        events = data.get("events") or []
        if not events:
            return False
        last = events[-1]
        return last.get("kind") == "gave_up" and since - GAVE_UP_WINDOW <= float(last.get("created_at") or 0) <= now

    @staticmethod
    def _safe(fn, *args):
        try:
            r = fn(*args)
            return True if r is None else r
        except BoardError as exc:
            log.warning("board call failed: %s", exc)
            return None
