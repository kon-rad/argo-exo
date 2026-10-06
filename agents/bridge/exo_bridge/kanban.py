"""Thin wrapper over the `hermes kanban` CLI, the stable interface to the board."""
from __future__ import annotations

import json
import subprocess
from typing import Callable


class KanbanError(RuntimeError):
    pass


class Kanban:
    def __init__(self, hermes_bin: str, board: str, run: Callable = subprocess.run):
        self.bin, self.board, self.run = hermes_bin, board, run

    def _call(self, *args: str) -> str:
        cmd = [self.bin, "kanban", "--board", self.board, *args]
        try:
            p = self.run(cmd, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KanbanError(f"{args[0]}: {exc}") from exc
        if p.returncode != 0:
            raise KanbanError(f"{args[0]}: {(p.stderr or p.stdout).strip()[-300:]}")
        return p.stdout

    def _json(self, *args: str):
        out = self._call(*args)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise KanbanError(f"{args[0]}: not JSON: {out[:120]!r}") from exc

    def create(self, title: str, body: str, assignee: str, max_runtime: str, idempotency_key: str) -> dict:
        # Options first, then "--", then the title: a transcript can never become a flag.
        return self._json("create", "--body", body, "--assignee", assignee,
                          "--max-runtime", max_runtime, "--idempotency-key", idempotency_key,
                          "--created-by", "exo-deck", "--json", "--", title)

    def subscribe(self, task_id: str, chat_id: str) -> None:
        self._call("notify-subscribe", "--platform", "telegram", "--chat-id", chat_id, task_id)

    def list(self) -> list[dict]:
        return self._json("list", "--json")
