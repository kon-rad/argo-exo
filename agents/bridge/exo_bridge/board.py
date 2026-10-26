"""Thin wrapper over the `cli-board` CLI: the Second Brain engine's Postgres board, the only board.

Facts about the CLI that shape this file (docs/board-design.md section 5.2 in the engine):
- its argument parser has no `--` separator and reads any argument starting with `--` as a flag, so free text gets a
  leading space when it starts with `--`, and `--body` takes its text as the NEXT argument (no `--body=...`);
- identity is never a flag: it comes from the process environment (BOARD_URL + BOARD_TOKEN for the HTTPS door, which is
  how the bridge, running as its own isolated user, reaches the board without any database credential);
- `list --json` output is cut off at about 64 KB on a big board, so every listing is filtered by assignee.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Callable


class BoardError(RuntimeError):
    pass


def _arg(text: str) -> str:
    return " " + text if text.startswith("--") else text


class Board:
    def __init__(self, cli_board_bin: str, board: str, run: Callable = subprocess.run, env: dict | None = None):
        self.bin, self.board, self.run, self.env = cli_board_bin, board, run, env
        self._seen: dict[str, tuple[float, dict]] = {}     # idempotency key -> (when, card)
        self._lock = threading.Lock()

    def _call(self, *args: str) -> str:
        cmd = [self.bin, *args]
        try:
            p = self.run(cmd, capture_output=True, text=True, timeout=30, env=self.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BoardError(f"{args[0]}: {exc}") from exc
        if p.returncode != 0:
            raise BoardError(f"{args[0]}: {(p.stderr or p.stdout).strip()[-300:]}")
        return p.stdout

    def _json(self, *args: str):
        out = self._call(*args)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise BoardError(f"{args[0]}: not JSON: {out[:120]!r}") from exc

    def create(self, title: str, body: str, assignee: str, idempotency_key: str) -> dict:
        """One card for a roster agent. The engine has no idempotency key, so a double-fired hook is caught here: the
        same key inside five minutes returns the card already made (this process only; one bridge runs)."""
        now = time.time()
        with self._lock:
            self._seen = {k: v for k, v in self._seen.items() if now - v[0] < 300}
            if idempotency_key in self._seen:
                return self._seen[idempotency_key][1]
            doc = self._json("create", _arg(title), "--to", assignee, "--board", self.board, "--body", _arg(body), "--json")
            card = doc.get("card") or doc
            self._seen[idempotency_key] = (now, card)
            return card

    def show(self, card_id: str) -> dict:
        doc = self._json("show", card_id, "--json")
        return doc.get("card") or doc

    def list(self, assignees: tuple[str, ...]) -> list[dict]:
        """The wearer's cards: open ones plus those finished in the last 7 days, for the given assignees only."""
        rows: dict[str, dict] = {}
        for a in assignees:
            got = self._json("list", "--board", self.board, "--assignee", a, "--limit", "50", "--json")
            for t in got if isinstance(got, list) else []:
                rows[t["id"]] = t
        return sorted(rows.values(), key=lambda t: str(t.get("created_at") or ""), reverse=True)
