"""agent-hub: the privileged half of the Second Brain app's Agents page and its Claude/Hermes Telegram toggle.

The app runs sandboxed (NoNewPrivileges, read-only home), so it can't start Claude or run systemctl. This service
runs as hermes on 127.0.0.1 only, behind a bearer token the app holds, and exposes exactly the actions the page needs.
The board (cards, assignment, comments) is the engine's own and is not served here. Stdlib only. Plan: Areas/argo/argo-exo/plans/10-claude-code-agents.md §2.3 in the vault.
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

from cc_dispatcher.config import Settings as LaneSettings

from .chats import ChatError, ChatManager, read_from

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
    _profiles: tuple[float, list[str]] = (0.0, [])

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
        bots = [{"name": n, "description": a.get("role") or "", "team": a.get("team"), "model": a.get("model"), "family": "org"}
                for n, a in self.bots().items()]
        return {"lanes": lanes, "profiles": [{"name": p, "family": "hermes"} for p in self.profiles()], "bots": bots}

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

    def bots(self) -> dict[str, dict]:
        """The org roster from the compiled org file (name → caps); empty when the org is off or the file is missing."""
        org = self.lanes.org
        if org is None:
            return {}
        try:
            doc = json.loads(Path(org.agents_file).read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return {n: a for n, a in (doc.get("agents") or {}).items() if a.get("runtime", "claude-code") != "human"}
