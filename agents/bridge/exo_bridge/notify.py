"""Tell the wearer when a card they filed finishes or blocks.

The Hermes board did this itself (`notify-subscribe`). The engine board has no per-card Telegram subscription, so the
bridge watches the cards it filed: one thread per card polls `cli-board show` and, on `done` or `blocked`, runs the
notifier command from EXO_NOTIFY_CMD with the text as its last argument. No EXO_NOTIFY_CMD: nothing is watched.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import threading
import time
from typing import Callable

log = logging.getLogger("exo-bridge")
TERMINAL = ("done", "blocked")


def summary(card: dict) -> str:
    what = "finished" if card.get("status") == "done" else "is blocked"
    why = card.get("result") if card.get("status") == "done" else card.get("block_reason")
    text = f"Exo card {card.get('id')} {what}: {card.get('title')}"
    return (text + f" - {why}")[:600] if why else text


class Watcher:
    def __init__(self, board, notify_cmd: str, run: Callable = subprocess.run, sleep: Callable = time.sleep,
                 poll_seconds: float = 20, give_up_seconds: float = 4 * 3600, start: Callable | None = None):
        self.board, self.cmd, self.run, self.sleep = board, shlex.split(notify_cmd) if notify_cmd else [], run, sleep
        self.poll, self.give_up = poll_seconds, give_up_seconds
        self.start = start or (lambda fn: threading.Thread(target=fn, daemon=True).start())

    @property
    def enabled(self) -> bool:
        return bool(self.cmd)

    def watch(self, card_id: str) -> None:
        if self.enabled:
            self.start(lambda: self._loop(card_id))

    def _loop(self, card_id: str) -> None:
        waited = 0.0
        while waited < self.give_up:
            self.sleep(self.poll)
            waited += self.poll
            try:
                card = self.board.show(card_id)
            except Exception as exc:    # a board hiccup must not end the watch
                log.warning("watch %s: %s", card_id, exc)
                continue
            if card.get("status") in TERMINAL:
                try:
                    self.run([*self.cmd, summary(card)], capture_output=True, text=True, timeout=60)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    log.warning("notify %s failed: %s", card_id, exc)
                return
        log.info("watch %s: gave up after %.0f s", card_id, self.give_up)
