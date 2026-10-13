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

    def claim(self, task_id: str, ttl_seconds: int) -> bool:
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

    def block(self, task_id: str, reason: str, kind: str = "transient") -> None:
        self._call("block", "--kind", kind, "--", task_id, reason)

    # -- used by agent-hub (the Second Brain app's Agents page) -------------------------------------------------
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

    def attach(self, task_id: str, path: str, author: str) -> None:
        self._call("attach", "--author", author, "--content-type", "application/x-ndjson", task_id, path)
