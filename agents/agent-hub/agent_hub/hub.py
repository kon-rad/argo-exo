"""agent-hub: the privileged half of the Second Brain app's Agents page and its Claude/Hermes Telegram toggle.

The app runs sandboxed (NoNewPrivileges, read-only home), so it can't write the kanban db, start Claude or run
systemctl. This service runs as hermes on 127.0.0.1 only, behind a bearer token the app holds, and exposes exactly
the actions the page needs. Stdlib only. Plan: Areas/argo/argo-exo/plans/10-claude-code-agents.md §2.3 in the vault.
"""
from __future__ import annotations

import hmac
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cc_dispatcher.board import Board, BoardError
from cc_dispatcher.config import Settings as LaneSettings

from .chats import ChatError, ChatManager, read_from

TASK_ID = re.compile(r"^t_[0-9a-f]{6,32}$")
BOARD = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
PROFILE_ROW = re.compile(r"^\s*[◆*]?\s*([a-z0-9][a-z0-9_-]*)\s")


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Hub:
    token: str
    lanes: LaneSettings
    chats: ChatManager
    telegram_script: str
    hermes_bin: str
    run: Callable = subprocess.run
    board_factory: Callable[[str], Board] | None = None
    _profiles: tuple[float, list[str]] = (0.0, [])

    def board(self, name: str) -> Board:
        if not BOARD.match(name or ""):
            raise HttpError(400, "bad board name")
        return (self.board_factory or (lambda b: Board(self.hermes_bin, b, self.run)))(name)

    # -- dispatch ------------------------------------------------------------------------------------------------
    def handle(self, method: str, path: str, query: dict, body: dict | None, auth: str) -> tuple[int, dict]:
        if not self.token or not hmac.compare_digest(auth or "", f"Bearer {self.token}"):
            return 401, {"error": "unauthorized"}
        try:
            return 200, self._route(method, path.rstrip("/") or "/", query, body or {})
        except HttpError as exc:
            return exc.status, {"error": str(exc)}
        except ChatError as exc:
            return exc.status, {"error": str(exc)}
        except BoardError as exc:
            return 502, {"error": f"kanban: {exc}"}

    def _route(self, method: str, path: str, q: dict, b: dict) -> dict:
        parts = path.strip("/").split("/")
        m = (method, *parts)
        if m == ("GET", "health"):
            return {"ok": True}
        if m == ("GET", "telegram"):
            return self.telegram("status")
        if m == ("POST", "telegram"):
            if b.get("default") in ("claude", "hermes"):
                return self.telegram("default", b["default"])
            if b.get("claude") in ("on", "off"):
                return self.telegram("claude", b["claude"])
            raise HttpError(400, 'send {"default": "claude"|"hermes"} or {"claude": "on"|"off"}')
        if m == ("GET", "agents"):
            return self.agents()
        if m == ("GET", "boards"):
            return {"boards": self._boards()}
        if m == ("GET", "board"):
            name = q.get("board", self.lanes.boards[0])
            return {"board": name, "tasks": self.board(name).all_tasks()}
        if m == ("POST", "tasks"):
            return self.create_task(b)
        if len(parts) >= 2 and parts[0] == "tasks":
            task_id = parts[1]
            if not TASK_ID.match(task_id):
                raise HttpError(400, "bad task id")
            if m == ("GET", "tasks", task_id):
                return self.board(q.get("board", self.lanes.boards[0])).show(task_id)
            if m == ("GET", "tasks", task_id, "log"):
                return read_from(self.lanes.runs_dir / f"{task_id}.jsonl", int(q.get("offset", 0) or 0))
            if m == ("POST", "tasks", task_id, "comment"):
                text = str(b.get("text", "")).strip()
                if not text or len(text) > 4000:
                    raise HttpError(400, "comment must be 1–4000 characters")
                self.board(b.get("board", self.lanes.boards[0])).comment(task_id, text, "konrad")
                return {"ok": True}
        if m == ("GET", "chats"):
            return {"chats": self.chats.list()}
        if m == ("POST", "chats"):
            return self.chats.create(b.get("cwd"), b.get("model"))
        if len(parts) >= 2 and parts[0] == "chats":
            chat_id = parts[1]
            if m == ("GET", "chats", chat_id, "events"):
                return self.chats.events(chat_id, int(q.get("offset", 0) or 0))
            if m == ("POST", "chats", chat_id, "messages"):
                return self.chats.send(chat_id, str(b.get("text", "")))
            if m == ("POST", "chats", chat_id, "interrupt"):
                self.chats.get(chat_id).interrupt()
                return {"ok": True}
        raise HttpError(404, f"no route {method} {path}")

    # -- actions -------------------------------------------------------------------------------------------------
    def telegram(self, *args: str) -> dict:
        args = [self.telegram_script, *args]
        try:
            p = self.run(args, capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HttpError(502, f"telegram-agent: {exc}") from exc
        if p.returncode != 0:
            raise HttpError(409 if "equals" in p.stderr or "no bot token" in p.stderr else 502,
                            (p.stderr or p.stdout).strip()[-400:] or "telegram-agent failed")
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise HttpError(502, "telegram-agent printed no status") from exc

    def agents(self) -> dict:
        lanes = [{"name": l.name, "description": l.description, "model": l.model, "family": "claude"}
                 for l in self.lanes.lanes.values()]
        return {"lanes": lanes, "profiles": [{"name": p, "family": "hermes"} for p in self.profiles()]}

    def profiles(self) -> list[str]:
        at, cached = self._profiles
        if time.time() - at < 60:
            return cached
        try:
            p = self.run([self.hermes_bin, "profile", "list"], capture_output=True, text=True, timeout=30)
            names = [m.group(1) for line in p.stdout.splitlines() if (m := PROFILE_ROW.match(line))]
        except (OSError, subprocess.TimeoutExpired):
            names = cached
        names = [n for n in names if n not in ("profile",)]
        self._profiles = (time.time(), names)
        return names

    def _boards(self) -> list[dict]:
        p = self.run([self.hermes_bin, "kanban", "boards", "list", "--json"], capture_output=True, text=True, timeout=30)
        try:
            rows = json.loads(p.stdout)
        except json.JSONDecodeError as exc:
            raise HttpError(502, "kanban boards: not JSON") from exc
        return [{"slug": r["slug"], "name": r.get("name") or r["slug"]} for r in rows if not r.get("archived")]

    def create_task(self, b: dict) -> dict:
        title = " ".join(str(b.get("title", "")).split())
        body = str(b.get("body", "")).strip()
        assignee = str(b.get("assignee", ""))
        if not 1 <= len(title) <= 200:
            raise HttpError(400, "title must be 1–200 characters")
        if len(body) > 8000:
            raise HttpError(400, "body too long (8,000 characters max)")
        known = set(self.lanes.lanes) | set(self.profiles())
        if assignee not in known:
            raise HttpError(400, f"unknown agent {assignee!r}")
        if assignee in ("trader", "portfolio", "wallet"):
            raise HttpError(400, "money agents take work from the wearer's approve key path, not from the app")
        return self.board(b.get("board", self.lanes.boards[0])).create(title, body, assignee, "secondbrain-app")
