"""Claude Code chat sessions for the Second Brain app's Agents page.

One chat = one long-lived `claude -p --input-format stream-json --output-format stream-json` child while you're
talking, resumed with `--resume <session_id>` after it has been idle-closed. Every event the child prints, plus each
message you send, is appended to ~/cc-chats/<id>.jsonl; the page polls it by byte offset, so a reload or a second
device (laptop + Pi) replays the same feed. The unmodified CLI on the subscription login, never --bare, never the
Agent SDK, one user.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

CHAT_ID = re.compile(r"^[0-9a-f]{12}$")
MAX_READ = 512 * 1024


class ChatError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class ChatMeta:
    id: str
    title: str
    cwd: str
    model: str
    created_at: float
    updated_at: float
    session_id: str = ""
    status: str = "idle"       # idle | working | closed (no live process) | error
    last_result: str = ""


@dataclass
class ChatSettings:
    root: Path                 # ~/cc-chats
    claude_bin: str
    cwd: str                   # default working directory (the vault)
    model: str = "sonnet"
    max_live: int = 2          # live claude processes for chats
    idle_close_seconds: int = 900
    denied_tools: tuple[str, ...] = ()
    base_env: dict = field(default_factory=dict)


def chat_command(s: ChatSettings, meta: ChatMeta) -> list[str]:
    cmd = [s.claude_bin, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--include-partial-messages", "--model", meta.model,
           # auto: the classifier reviews each action; anything that would still need a person is denied, because
           # this page has no approval UI yet (plan 10, Phase 5).
           "--permission-mode", "auto", "--permission-prompts", "none"]
    if s.denied_tools:
        cmd += ["--disallowedTools", ",".join(s.denied_tools)]
    if meta.session_id:
        cmd += ["--resume", meta.session_id]
    return cmd


def chat_env(s: ChatSettings) -> dict:
    keep = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ")
    env = {k: s.base_env[k] for k in keep if k in s.base_env}
    env["PATH"] = f"{Path(s.claude_bin).parent}:/usr/local/bin:/usr/bin:/bin"
    return env   # never ANTHROPIC_API_KEY: it would outrank the subscription login


class Chat:
    def __init__(self, meta: ChatMeta, settings: ChatSettings, popen: Callable):
        self.meta, self.s, self.popen = meta, settings, popen
        self.proc = None
        self.lock = threading.Lock()
        self.last_activity = time.time()

    @property
    def log_path(self) -> Path:
        return self.s.root / f"{self.meta.id}.jsonl"

    @property
    def meta_path(self) -> Path:
        return self.s.root / f"{self.meta.id}.json"

    def save(self) -> None:
        tmp = self.meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.meta)))
        tmp.replace(self.meta_path)

    def append(self, event: dict) -> None:
        with open(self.log_path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def live(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _start(self) -> None:
        self.proc = self.popen(chat_command(self.s, self.meta), cwd=self.meta.cwd, env=chat_env(self.s),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1, start_new_session=True)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc) -> None:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                ev = {"type": "stderr", "text": line[:2000]}
            self.on_event(ev)
        code = proc.wait()
        with self.lock:
            if self.proc is proc:
                if self.meta.status == "working":
                    self.meta.status = "error"
                    self.append({"type": "hub", "text": f"claude exited (code {code}) mid-turn", "at": time.time()})
                elif self.meta.status != "error":
                    self.meta.status = "closed"
                self.meta.updated_at = time.time()
                self.save()

    def on_event(self, ev: dict) -> None:
        with self.lock:
            # Partial-message deltas are only for live typing; they make up most of the bytes, so they're kept out
            # of the replay file except text deltas, which the page needs to show the reply as it streams.
            if ev.get("type") == "stream_event" and ((ev.get("event") or {}).get("delta") or {}).get("type") != "text_delta":
                return
            self.append(ev)
            self.last_activity = time.time()
            if ev.get("type") == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
                self.meta.session_id = ev["session_id"]
            if ev.get("type") == "result":
                self.meta.status = "idle"
                self.meta.last_result = str(ev.get("result", ""))[:300]
                if "not logged in" in self.meta.last_result.lower():
                    self.meta.status = "error"
            self.meta.updated_at = time.time()
            self.save()

    def send(self, text: str) -> None:
        with self.lock:
            if self.meta.status == "working" and self.live():
                raise ChatError(409, "the agent is still working on the last message; stop it or wait")
            if not self.live():
                self._start()
            msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
            self.append({"type": "you", "text": text, "at": time.time()})
            self.meta.status = "working"
            if self.meta.title == "New chat":
                self.meta.title = " ".join(text.split())[:60]
            self.meta.updated_at = self.last_activity = time.time()
            self.save()
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def interrupt(self) -> None:
        """SIGINT ends the turn cleanly (SIGTERM would leave it unfinished). The next message resumes the session."""
        with self.lock:
            if self.live():
                try:
                    os.killpg(self.proc.pid, signal.SIGINT)
                except (ProcessLookupError, PermissionError, OSError):
                    self.proc.send_signal(signal.SIGINT)
                self.append({"type": "hub", "text": "stopped by you", "at": time.time()})

    def close(self) -> None:
        with self.lock:
            if self.live():
                try:
                    self.proc.stdin.close()   # end of input: claude finishes and exits
                except OSError:
                    pass


class ChatManager:
    def __init__(self, settings: ChatSettings, popen: Callable = subprocess.Popen, clock: Callable = time.time):
        self.s, self.popen, self.clock = settings, popen, clock
        self.s.root.mkdir(parents=True, exist_ok=True)
        self.chats: dict[str, Chat] = {}
        self.lock = threading.Lock()
        for meta_file in self.s.root.glob("*.json"):
            try:
                meta = ChatMeta(**json.loads(meta_file.read_text()))
            except (ValueError, TypeError):
                continue
            if meta.status == "working":
                meta.status = "closed"     # the hub restarted mid-turn; the session can be resumed
            self.chats[meta.id] = Chat(meta, self.s, self.popen)

    def get(self, chat_id: str) -> Chat:
        if not CHAT_ID.match(chat_id or "") or chat_id not in self.chats:
            raise ChatError(404, "no such chat")
        return self.chats[chat_id]

    def list(self) -> list[dict]:
        rows = [asdict(c.meta) | {"live": c.live()} for c in self.chats.values()]
        return sorted(rows, key=lambda r: r["updated_at"], reverse=True)

    def create(self, cwd: str | None = None, model: str | None = None) -> dict:
        cwd = cwd or self.s.cwd
        if not Path(cwd).is_dir():
            raise ChatError(400, f"no such directory: {cwd}")
        if model and model not in ("opus", "sonnet", "haiku"):
            raise ChatError(400, "model must be opus, sonnet or haiku")
        now = self.clock()
        meta = ChatMeta(id=uuid.uuid4().hex[:12], title="New chat", cwd=cwd, model=model or self.s.model,
                        created_at=now, updated_at=now)
        chat = Chat(meta, self.s, self.popen)
        chat.save()
        chat.log_path.touch()
        with self.lock:
            self.chats[meta.id] = chat
        return asdict(meta) | {"live": False}

    def send(self, chat_id: str, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise ChatError(400, "empty message")
        if len(text) > 20000:
            raise ChatError(400, "message too long (20,000 characters max)")
        chat = self.get(chat_id)
        if not chat.live():
            self._make_room(keep=chat_id)
        chat.send(text)
        return asdict(chat.meta) | {"live": chat.live()}

    def _make_room(self, keep: str) -> None:
        live = [c for c in self.chats.values() if c.live() and c.meta.id != keep]
        if len(live) < self.s.max_live:
            return
        idle = sorted((c for c in live if c.meta.status != "working"), key=lambda c: c.last_activity)
        if not idle:
            raise ChatError(429, f"{self.s.max_live} chats are working right now; wait for one to finish")
        idle[0].close()

    def events(self, chat_id: str, offset: int) -> dict:
        chat = self.get(chat_id)
        return read_from(chat.log_path, offset) | {"chat": asdict(chat.meta) | {"live": chat.live()}}

    def reap_idle(self) -> None:
        now = self.clock()
        for c in list(self.chats.values()):
            if c.live() and c.meta.status != "working" and now - c.last_activity > self.s.idle_close_seconds:
                c.close()


def read_from(path: Path, offset: int) -> dict:
    """Complete lines from byte `offset`, at most MAX_READ bytes. Returns the events and the next offset."""
    try:
        size = path.stat().st_size
    except OSError:
        return {"events": [], "offset": 0, "size": 0}
    offset = max(0, min(int(offset), size))
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read(MAX_READ)
    end = chunk.rfind(b"\n")
    if end < 0:
        return {"events": [], "offset": offset, "size": size}
    events = []
    for raw in chunk[: end + 1].splitlines():
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            events.append({"type": "raw", "text": raw.decode(errors="replace")[:2000]})
    return {"events": events, "offset": offset + end + 1, "size": size}
