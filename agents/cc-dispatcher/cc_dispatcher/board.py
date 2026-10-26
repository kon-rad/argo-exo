"""The `hermes kanban` CLI, the board's stable interface (same approach as exo_bridge.kanban).

Claims behave like this (tested live on the droplet, 2026-10-11): `claim` locks the task to the short-lived CLI's own
host:pid and leaves worker_pid empty, so Hermes's crash detector and max-runtime enforcer never touch it, and a
heartbeat from another process can't extend it. Only TTL expiry returns it to `ready`. So we claim with a TTL longer
than the lane's runtime and enforce the runtime ourselves.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable


class BoardError(RuntimeError):
    pass


class Board:
    def __init__(self, hermes_bin: str, board: str, run: Callable = subprocess.run):
        self.bin, self.board, self.run = hermes_bin, board, run

    def _call(self, *args: str) -> str:
        cmd = [self.bin, "kanban", "--board", self.board, *args]
        try:
            p = self.run(cmd, capture_output=True, text=True, timeout=30)
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

    def tasks(self, assignee: str, status: str) -> list[dict]:
        rows = self._json("list", "--assignee", assignee, "--status", status, "--json")
        return rows if isinstance(rows, list) else []

    def claim(self, task_id: str, ttl_seconds: int, as_agent: str | None = None) -> bool:
        try:
            self._call("claim", task_id, "--ttl", str(int(ttl_seconds)))
            return True
        except BoardError:
            return False          # someone else got it, or it left `ready`

    def status(self, task_id: str) -> str:
        data = self._json("show", task_id, "--json")
        return (data.get("task") or data).get("status", "")

    def comment(self, task_id: str, text: str, author: str) -> None:
        self._call("comment", "--author", author, "--", task_id, text)   # "--": a comment can start with "-"

    def block(self, task_id: str, reason: str, kind: str = "transient", as_agent: str | None = None) -> None:
        self._call("block", "--kind", kind, "--", task_id, reason)

    def schedule(self, task_id: str, reason: str) -> None:
        """Park a card in Scheduled (waiting on time, not a human); `unblock` returns it to ready."""
        self._call("schedule", "--", task_id, reason)

    def unblock(self, task_id: str, reason: str) -> None:
        self._call("unblock", "--reason", reason, "--", task_id)

    # -- used by agent-hub (the Second Brain app's Agents page) -------------------------------------------------
    def ready_tasks(self) -> list[dict]:
        """Every ready card on the board in one call; the org filters by assignee itself (was one call per bot)."""
        rows = self._json("list", "--status", "ready", "--json")
        return rows if isinstance(rows, list) else []

    def all_tasks(self) -> list[dict]:
        rows = self._json("list", "--json")
        return rows if isinstance(rows, list) else []

    def show(self, task_id: str) -> dict:
        return self._json("show", task_id, "--json")

    def task(self, task_id: str) -> dict:
        data = self._json("show", task_id, "--json")
        t = data.get("task") or data
        return {"title": t.get("title", ""), "body": t.get("body") or "", "status": t.get("status", "")}

    def create(self, title: str, body: str, assignee: str, created_by: str,
               parents: tuple[str, ...] = (), initial_status: str | None = None) -> dict:
        # Options first, then "--", then the title, and the body as ONE argv item: text never becomes a flag.
        args = ["create", "--assignee", assignee, "--created-by", created_by, "--json"]
        if body:
            args.insert(1, f"--body={body}")
        for p in parents:
            args += ["--parent", p]
        if initial_status:
            args += ["--initial-status", initial_status]
        return self._json(*args, "--", title)

    def assign(self, task_id: str, assignee: str) -> None:
        self._call("assign", task_id, assignee)      # "none" unassigns

    def attach(self, task_id: str, path: str, author: str) -> None:
        self._call("attach", "--author", author, "--content-type", "application/x-ndjson", task_id, path)


def _arg(text: str) -> str:
    """cli-board's argument parser has no `--` separator and reads any argument starting with `--` as a flag, so a
    comment that starts with it gets a leading space."""
    return " " + text if text.startswith("--") else text


class EngineBoard:
    """The engine's own board (the Second Brain app's `/api/board`, door 3: direct Postgres as `sb_org`), through
    the `cli-board` CLI wrapper already installed at ~/.local/bin/cli-board (it sources /etc/secondbrain/env itself,
    so this process's own environment stays clean). Same public surface as `Board`, so `Dispatcher` doesn't branch on
    which backend it holds; the difference is that every write here needs a caller identity (`ORG_AGENT`), because the
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
        """Every ready card on the board in one call (same surface as `Board.ready_tasks`)."""
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
            return False          # any other claim failure: someone else got it, or it left `ready` (same as Board.claim)
        return True

    def status(self, task_id: str) -> str:
        data = self._json(None, "show", task_id, "--json")
        return (data.get("card") or data).get("status", "")

    def comment(self, task_id: str, text: str, author: str) -> None:
        # "--": a comment can start with "-". No --author: the engine's author is always ORG_AGENT, never a flag.
        self._call(author, "comment", task_id, _arg(text))

    def block(self, task_id: str, reason: str, kind: str = "transient", as_agent: str | None = None) -> None:
        # The engine's block_kind vocabulary isn't Hermes's free-text kind: "transient" (a worker crash or restart,
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


BoardLike = Board | EngineBoard


def created_ts(task: dict) -> float:
    """A card's creation time as seconds: the Hermes board gives epoch seconds, the engine board an ISO 8601 string."""
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
