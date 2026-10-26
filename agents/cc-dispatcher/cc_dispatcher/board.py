"""The engine board (the Second Brain app's Postgres board, `/api/board`) through the `cli-board` CLI.

This is the only board. The Hermes kanban (SQLite) and its `hermes kanban` CLI are no longer used: the engine board
replaced it on 2026-10-26 (vault: Areas/argo/second-brain/kanban-board/).
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable


class BoardError(RuntimeError):
    pass


def _arg(text: str) -> str:
    """cli-board's argument parser has no `--` separator and reads any argument starting with `--` as a flag, so a
    comment that starts with it gets a leading space."""
    return " " + text if text.startswith("--") else text


class Board:
    """The engine's own board (the Second Brain app's `/api/board`, door 3: direct Postgres as `sb_org`), through
    the `cli-board` CLI wrapper already installed at ~/.local/bin/cli-board (it sources /etc/secondbrain/env itself,
    so this process's own environment stays clean). Every write here needs a caller identity (`ORG_AGENT`), because the
    engine's board_card_* functions check "only its own cards" instead of SQLite's anyone-can-claim model. Callers
    pass `as_agent` on claim/comment/block/create; a read (tasks/show/task/status) doesn't need one. docs/board-
    design.md section 5.2 is the CLI reference; board-design.md section 7 names this the `claude-code` lane.
    """

    def __init__(self, cli_board_bin: str, board: str, run: Callable = subprocess.run):
        self.bin, self.board, self.run = cli_board_bin, board, run

    def _env(self, as_agent: str | None) -> dict | None:
        if as_agent is None:
            return None
        import os
        return {**os.environ, "ORG_AGENT": as_agent}

    def _raw(self, as_agent: str | None, *args: str):
        try:
            return self.run([self.bin, *args], capture_output=True, text=True, timeout=30, env=self._env(as_agent))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BoardError(f"{args[0]}: {exc}") from exc

    def _call(self, as_agent: str | None, *args: str) -> str:
        p = self._raw(as_agent, *args)
        if p.returncode != 0:
            raise BoardError(f"{args[0]}: {(p.stderr or p.stdout).strip()[-300:]}")
        return p.stdout

    def _json(self, as_agent: str | None, *args: str):
        out = self._call(as_agent, *args)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise BoardError(f"{args[0]}: not JSON: {out[:120]!r}") from exc

    def tasks(self, assignee: str, status: str) -> list[dict]:
        rows = self._json(None, "list", "--assignee", assignee, "--status", status, "--board", self.board, "--json")
        return rows if isinstance(rows, list) else []

    def ready_tasks(self) -> list[dict]:
        """Every ready card on the board in one call."""
        rows = self._json(None, "list", "--status", "ready", "--board", self.board, "--json")
        return rows if isinstance(rows, list) else []

    def claim(self, task_id: str, ttl_seconds: int, as_agent: str | None = None) -> bool:
        lease = max(1, min(1440, -(-int(ttl_seconds) // 60)))   # ceil to minutes, clamped to the engine's max lease
        agent = as_agent or task_id   # a caller must always be given for a real claim; task_id here would just fail cleanly
        try:
            p = self._raw(agent, "claim", "--agent", agent, "--task", task_id, "--lease", str(lease), "--board", self.board, "--json")
        except BoardError:
            return False
        if p.returncode == 3 or p.stdout.strip() in ("null", ""):
            return False          # already taken, or not ready: not an error, the caller moves on
        if p.returncode != 0:
            return False          # any other claim failure: someone else got it, or it left `ready`
        return True

    def status(self, task_id: str) -> str:
        data = self._json(None, "show", task_id, "--json")
        return (data.get("card") or data).get("status", "")

    def comment(self, task_id: str, text: str, author: str) -> None:
        # "--": a comment can start with "-". No --author: the engine's author is always ORG_AGENT, never a flag.
        if author == "cc-dispatcher":
            # The dispatcher has no roster row on the engine board (a comment as it fails: "agent is retired"), so its
            # notes go in as the owner's database door, labelled.
            self._call(None, "comment", task_id, _arg(f"[cc-dispatcher] {text}"))
            return
        self._call(author, "comment", task_id, _arg(text))

    def block(self, task_id: str, reason: str, kind: str = "transient", as_agent: str | None = None) -> None:
        # The engine's block_kind vocabulary is fixed: "transient" (a worker crash or restart,
        # not the worker's own doing) and "capability" (couldn't even start) both map to "tool-error"; anything
        # else that reaches here is dispatcher-internal, never a card-body choice, so this mapping is exhaustive.
        engine_kind = {"transient": "tool-error", "capability": "tool-error"}.get(kind, "other")
        self._call(as_agent, "finish", task_id, "--blocked", engine_kind, reason)

    def show(self, task_id: str) -> dict:
        return self._json(None, "show", task_id, "--json")

    def task(self, task_id: str) -> dict:
        data = self._json(None, "show", task_id, "--json")
        t = data.get("card") or data
        return {"title": t.get("title", ""), "body": t.get("body") or "", "status": t.get("status", "")}

    def create(self, title: str, body: str, assignee: str, created_by: str,
               parents: tuple[str, ...] = (), initial_status: str | None = None) -> dict:
        # created_by is the caller identity (ORG_AGENT), not a --created-by flag: a lane filing its own retry or
        # escalation creates "for itself", which the engine always allows (no dispatch capability needed).
        args = ["create", title, "--to", assignee, "--board", self.board, "--json"]
        if body:
            args += ["--body", body]
        if parents:
            args += ["--after", ",".join(parents)]
        doc = self._json(created_by, *args)
        card = doc.get("card") or doc
        if initial_status == "blocked" and card.get("id"):
            # The engine has no "create already blocked"; move it there right after, as the same caller.
            self._call(created_by, "move", card["id"], "blocked")
        return card

    def assign(self, task_id: str, assignee: str, as_agent: str | None = None) -> None:
        self._call(as_agent, "assign", task_id, assignee)      # "none" unassigns

    def attach(self, task_id: str, path: str, author: str) -> None:
        # No attachments table yet (board-design.md section 9): the log path rides as a comment instead.
        self._call(author, "comment", task_id, f"Live log: {path}")




def created_ts(task: dict) -> float:
    """A card's creation time as seconds: the engine board gives an ISO 8601 string (epoch seconds are still accepted)."""
    v = task.get("created_at")
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        from datetime import datetime
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
