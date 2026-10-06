"""Serialise the talk hooks (talk-start, talk-stop, talk-cancel, repeat).

Each hook is its own process. If a release arrives before talk-start has written rec.pid, talk-stop
finds nothing to stop and arecord keeps recording. One worker runs the hooks in order and waits for
each to exit; talk-start returns once arecord is spawned, so the next hook always sees rec.pid.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

log = logging.getLogger("hookq")


class HookQueue:
    def __init__(self, runner: Callable[..., object]):
        """runner(name, *args) blocks until the hook process exits."""
        self._runner = runner
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending_starts = 0
        self._idle = threading.Event()
        self._idle.set()
        self._unfinished = 0
        threading.Thread(target=self._work, daemon=True).start()

    def put(self, name: str, *args: str) -> bool:
        """Queue a hook. A talk-start while another talk-start is still queued is dropped."""
        with self._lock:
            if name == "talk-start":
                if self._pending_starts:
                    log.info("talk-start already queued, dropped")
                    return False
                self._pending_starts += 1
            self._unfinished += 1
            self._idle.clear()
        self._q.put((name, args))
        return True

    def join(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def _work(self) -> None:
        while True:
            name, args = self._q.get()
            with self._lock:
                if name == "talk-start":
                    self._pending_starts -= 1
            try:
                self._runner(name, *args)
            except Exception:
                log.exception("hook %s failed", name)
            finally:
                with self._lock:
                    self._unfinished -= 1
                    if not self._unfinished:
                        self._idle.set()
