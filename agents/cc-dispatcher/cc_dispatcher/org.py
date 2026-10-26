"""The agent company: wake named agents from the message board (org_wakeups) and from kanban tasks assigned to them.

Design: Areas/argo/second-brain/agent-company/agent-company-design.md §6 in the vault. The board is reached only
through `cli-org` (the Second Brain app's CLI, on hermes's PATH), the same way kanban is reached through `hermes
kanban`: no database driver here, stdlib only.

A `runtime: hermes` agent is a Hermes profile of the same name (its model on the home model host): a wake is one
`hermes -p <name> chat -Q -q` run, its kanban cards are Hermes's own dispatcher's (never claimed here), and it waits
while the local gate says the host is down. Those wakes don't count toward the daily cap (no Claude quota).

One wake = one `claude -p` run as that agent: identity from .claude/agents/<name>.md plus the org-member skill as the
appended system prompt, ORG_AGENT in the environment. The agent's last session is resumed only when the wake
continues earlier work: a reply in a message thread, a message about a ticket the session handled, a ticket it already
worked, an answer to a ship request. A new task, a new top-level message or a routine starts fresh. All open wakeups of an agent
are claimed together (coalescing); an agent runs at most once per cooldown and `wakes_per_day` times a day; the
dispatcher's global max_in_progress and daily cap still apply.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from . import worker
from .board import Board, BoardError
from .config import Lane, OrgSettings, Settings

log = logging.getLogger("cc-dispatcher.org")
AUTHOR = "cc-dispatcher"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIMEOUT_ESCALATE_AFTER = 2   # consecutive runtime-cap hits on the same card before we stop re-waking it
CLAUDE_SESSION = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)   # `claude --resume` takes only this
HERMES_SESSION = re.compile(r"^\d{8}_\d{6}_[0-9a-f]+$")   # Hermes ids; a Claude session id (a uuid) is not resumable there


@dataclass(frozen=True)
class OrgAgent:
    name: str
    role: str
    team: str
    team_name: str
    model: str
    lane: str | None
    is_lead: bool
    prompt_file: str
    tools: tuple[str, ...]
    wakes_per_day: int
    cooldown_minutes: int
    max_runtime_minutes: int
    routines: tuple[dict, ...]
    permission_mode: str = "acceptEdits"
    work: str | None = None               # research | coding | design | media | writing | sales | education | ops
    runtime: str = "claude-code"          # claude-code | hermes
    capabilities: tuple[str, ...] = ()    # code | review | merge | deploy | m1 | web (tools come with them from the compiler)

    @property
    def is_hermes(self) -> bool:
        return self.runtime == "hermes"


def load_agents(path: Path) -> dict[str, OrgAgent]:
    raw = json.loads(path.read_text())
    agents = {}
    for name, a in raw.get("agents", {}).items():
        runtime = a.get("runtime", "claude-code")
        if runtime not in ("claude-code", "hermes"):
            continue   # codex, human: not ours
        agents[name] = OrgAgent(
            name=name, role=a["role"], team=a["team"], team_name=a.get("team_name", a["team"]), model=a.get("model", "sonnet"),
            lane=a.get("lane"), is_lead=bool(a.get("is_lead")), prompt_file=a["prompt_file"], tools=tuple(a.get("tools", [])),
            wakes_per_day=int(a.get("wakes_per_day", 6)), cooldown_minutes=int(a.get("cooldown_minutes", 10)),
            max_runtime_minutes=int(a.get("max_runtime_minutes", 30)), routines=tuple(a.get("routines", [])),
            permission_mode=a.get("permission_mode", "acceptEdits"), work=a.get("work"), runtime=runtime,
            capabilities=tuple(a.get("capabilities", [])),
        )
    return agents


def run_header(agent: OrgAgent, run_id: str, claimed: list[dict], task: tuple[str, str] | None, resumed: bool, at: str) -> dict:
    """The first line of a wake's log: who ran, why, on which card. The Second Brain usage ledger reads it."""
    return {
        "type": "cc_dispatch", "run_id": run_id, "agent": agent.name, "team": agent.team, "work": agent.work,
        "reasons": [{"reason": w.get("reason"), "ref": w.get("ref")} for w in claimed],
        "wakeup_ids": [w["id"] for w in claimed if "id" in w],
        "task_id": task[1] if task else None, "board": task[0] if task else None,
        "model": "hermes" if agent.is_hermes else agent.model, "runtime": agent.runtime, "resumed": resumed, "at": at,
    }


class OrgBoard:
    """`cli-org` as a subprocess. Every call is as the dispatcher (no ORG_AGENT), except what the agent itself posts."""

    def __init__(self, cli_org: str, run: Callable = subprocess.run):
        self.bin, self.run = cli_org, run

    def _json(self, *args: str):
        cmd = [self.bin, *args, "--json"]
        try:
            p = self.run(cmd, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BoardError(f"cli-org {args[0]}: {exc}") from exc
        if p.returncode != 0:
            raise BoardError(f"cli-org {args[0]}: {(p.stderr or p.stdout).strip()[-300:]}")
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError as exc:
            raise BoardError(f"cli-org {args[0]}: not JSON: {p.stdout[:120]!r}") from exc

    def open_wakeups(self) -> list[dict]:
        rows = self._json("wakeups", "--all")
        return [w for w in rows if not w.get("claimed_at")] if isinstance(rows, list) else []

    def claim(self, agent: str, run_id: str) -> list[dict]:
        rows = self._json("wake-claim", agent, "--by", run_id)
        return rows if isinstance(rows, list) else []

    def done(self, ids: list[int], run_id: str, exit_code: int, result: str) -> None:
        self._json("wake-done", ",".join(str(i) for i in ids), "--run", run_id, "--exit", str(exit_code), "--result", result[:1500] or "(no output)")

    def add(self, agent: str, reason: str, ref: str) -> int | None:
        return (self._json("wake-add", agent, "--reason", reason, "--ref", ref) or {}).get("id")

    def session(self, agent: str) -> dict:
        return self._json("session", agent) or {}

    def message(self, agent: str, ref: str) -> dict | None:
        """{"thread_id", "task_id"} of one message, read as the agent (it must be a member of the channel); None if unknown."""
        try:
            p = self.run([self.bin, "thread", str(ref), "--json"], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "ORG_AGENT": agent})
            if p.returncode != 0:
                return None
            for m in json.loads(p.stdout):
                if str(m.get("id")) == str(ref):
                    return {"thread_id": m.get("thread_id"), "task_id": m.get("task_id")}
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
            pass
        return None

    def release(self) -> int:
        return int((self._json("wake-release") or {}).get("released", 0))

    def set_session(self, agent: str, session_id: str | None) -> None:
        self._json("session", agent, *(["--set", session_id] if session_id else ["--clear"]))


@dataclass
class OrgRun:
    agent: OrgAgent
    run_id: str
    wakeup_ids: list[int]
    task: tuple[str, str] | None          # (board, task id) when the wake is a kanban task
    proc: object
    log_path: Path
    started: float
    resumed: bool
    task_ids: tuple = ()                   # kanban task ids this wake worked on (kept with the session)
    interrupted_at: float | None = None
    timed_out: bool = False


@dataclass
class OrgState:
    """runs_dir/org-state.json: per-agent wake counts and last wake, and which routines fired today."""
    wakes: dict = field(default_factory=dict)       # agent -> {"day": "YYYY-MM-DD", "n": int, "last": float}
    routines: dict = field(default_factory=dict)    # "agent/routine" -> "YYYY-MM-DD"
    session_tasks: dict = field(default_factory=dict)  # agent -> kanban task ids its saved session has worked
    task_timeouts: dict = field(default_factory=dict)   # task_id -> consecutive runtime-cap hits in a row
    session_tasks: dict = field(default_factory=dict)  # agent -> kanban task ids its saved session has worked

    @classmethod
    def load(cls, path: Path) -> "OrgState":
        try:
            return cls(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__))
        tmp.replace(path)


# -- prompt ------------------------------------------------------------------------------------------------------
def describe_wakeups(wakeups: list[dict]) -> list[str]:
    lines = []
    for w in wakeups:
        reason, ref = w.get("reason"), w.get("ref")
        # "owner" is the reason for a message the human wrote (the engine's name since its
        # owner-names migration); "konrad" is what older wakeups carried.
        if reason == "owner" and str(ref).startswith("ship:"):
            sid = str(ref)[5:]
            lines.append(f"the owner answered ship request #{sid} (`cli-org ship list` shows it; approved: merge or deploy now, "
                         "denied: the note says what to fix)")
        elif reason in ("dm", "mention", "handoff", "owner", "konrad"):
            who = {"dm": "a direct message", "mention": "a mention", "handoff": "a handoff",
                   "owner": "a message from konrad", "konrad": "a message from konrad"}[reason]
            lines.append(f"{who} (message {ref}; `cli-org thread {ref}` shows it)")
        elif reason == "routine":
            lines.append(f"your routine `{ref}`" + (": post today's stand-up with `cli-org standup`" if ref == "daily-standup" else
                                                   ": write what you learned this week into your memory file" if ref == "weekly-reflection" else ""))
        elif reason == "task":
            lines.append(f"kanban task {ref}")
        else:
            lines.append(f"{reason} {ref}")
    return lines


def wake_prompt(agent: OrgAgent, wakeups: list[dict], task: tuple[str, str] | None) -> str:
    reasons = describe_wakeups(wakeups)
    if task and not any(w.get("reason") == "task" for w in wakeups):
        reasons.append(f"kanban task {task[1]} on board {task[0]}")
    head = f"You are {agent.name}, {agent.role} on the {agent.team_name} team. You were woken because of:\n" + "\n".join(f"- {r}" for r in reasons)
    # Only what woke the bot: reading every channel on every wake was most of a short wake's tokens.
    steps = ["Read only what woke you: `cli-org thread <id>` for each message above. Read a channel (`cli-org read <channel>`) "
             "only when the work needs its history."]
    if task:
        steps.append("Before you start, `cli-org recall \"<the card's subject>\" --project <its project>` for what the company "
                     "already knows; when you learn something others will need, `cli-org remember` it with its source.")
    if task:
        steps.append(f"Work kanban task {task[1]} on board {task[0]}: start with `hermes kanban --board {task[0]} show {task[1]} --json`, "
                     "do the work, and end it with `complete` or `block` (the kanban worker protocol below).")
    steps += ["Answer what was addressed to you in its thread (`--thread <id>`); do the work that is yours; hand off what isn't.",
              f"Finish with exactly one `cli-org post {agent.team} \"<what changed, with a path, command or task id>\" --kind status`.",
              "Nobody is watching this terminal: no questions, no permission prompts. Stop when you are done; the run ends when you stop."]
    return head + "\n\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) + "\n"


def system_prompt(settings: Settings, agent: OrgAgent, with_protocol: bool) -> str:
    org = settings.org
    assert org is not None
    identity = (org.vault / agent.prompt_file).read_text()
    if identity.startswith("---"):
        identity = identity.split("---", 2)[2].strip()   # the frontmatter is for Claude's subagent loader, not the prompt
    parts = [identity, org.skill_file.read_text().strip()]
    if with_protocol:
        parts.append((Path(settings.lanes_path).parent / "worker-protocol.md").read_text().strip())
    return "\n\n".join(parts) + "\n"


# -- spawn -------------------------------------------------------------------------------------------------------
def org_command(settings: Settings, agent: OrgAgent, lane: Lane | None, task: tuple[str, str] | None,
                prompt: str, session_id: str | None) -> list[str]:
    org = settings.org
    assert org is not None
    allowed = list(org.allowed_tools) + list(agent.tools)
    denied = list(org.denied_tools)
    if lane:
        allowed += list(lane.allowed_tools)
        denied += list(lane.denied_tools)
    if task:
        allowed += worker.kanban_tools(settings.hermes_bin, task[0])
    cmd = [
        settings.claude_bin, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--model", agent.model,
        "--append-system-prompt", system_prompt(settings, agent, with_protocol=task is not None),
        "--permission-mode", agent.permission_mode,
        "--permission-prompts", "none",
        "--max-turns", str(org.max_turns),
        "--allowedTools", ",".join(dict.fromkeys(allowed)),
        "--disallowedTools", ",".join(dict.fromkeys(denied)),
    ]
    if "code" in agent.capabilities and org.bots_work_dir:
        cmd += ["--add-dir", org.bots_work_dir]     # cli-git worktrees: Edit and Write work there without a prompt
    if session_id:
        cmd += ["--resume", session_id]
    return cmd


def org_env(base: dict, settings: Settings, agent: OrgAgent, run_id: str, task: tuple[str, str] | None) -> dict:
    env = {k: base[k] for k in worker.PASS_THROUGH if k in base}
    bindir = str(Path(settings.claude_bin).parent)
    env["PATH"] = f"{bindir}:{Path(settings.hermes_bin).parent}:/usr/local/bin:/usr/bin:/bin"
    env.update({"ORG_AGENT": agent.name, "ORG_RUN": run_id, "HERMES_PROFILE": agent.name, "CC_LANE": agent.lane or ""})
    if task:
        env.update({"HERMES_KANBAN_BOARD": task[0], "HERMES_KANBAN_TASK": task[1]})
    return env


def spawn(settings: Settings, agent: OrgAgent, lane: Lane | None, task: tuple[str, str] | None, prompt: str,
          session_id: str | None, run_id: str, cwd: str, log_path: Path, base_env: dict | None = None) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = open(log_path, "ab")
    try:
        return subprocess.Popen(
            org_command(settings, agent, lane, task, prompt, session_id), cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=out, stderr=subprocess.STDOUT,
            env=org_env(base_env if base_env is not None else dict(os.environ), settings, agent, run_id, task),
            start_new_session=True,
        )
    finally:
        out.close()


def hermes_command(settings: Settings, agent: OrgAgent, prompt: str, session_id: str | None) -> list[str]:
    """The Hermes profile named like the agent; identity and the org skill ride in the first prompt of a session."""
    org = settings.org
    assert org is not None
    if not session_id:
        prompt = system_prompt(settings, agent, with_protocol=False) + "\n---\n\n" + prompt
    cmd = [settings.hermes_bin, "-p", agent.name, "chat", "-Q", "-q", prompt, "--max-turns", str(org.max_turns), "--source", "org"]
    if session_id:
        cmd += ["--resume", session_id]
    return cmd


def spawn_hermes(settings: Settings, agent: OrgAgent, prompt: str, session_id: str | None, run_id: str, cwd: str,
                 log_path: Path, base_env: dict | None = None) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = open(log_path, "ab")
    env = org_env(base_env if base_env is not None else dict(os.environ), settings, agent, run_id, None)
    try:
        return subprocess.Popen(hermes_command(settings, agent, prompt, session_id), cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    finally:
        out.close()


def hermes_result_of(log_path: Path) -> tuple[str | None, str, bool]:
    """(session_id, result text, saw_reply) from a `hermes chat -Q` log: the reply, then `session_id: <id>` on stderr."""
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return None, "", False
    session = None
    body = []
    for line in lines:
        if line.startswith("session_id: "):
            session = line.split(": ", 1)[1].strip() or None
        elif not line.startswith('{"type": "cc_dispatch"') and "tirith security scanner" not in line:
            body.append(line)        # (Hermes's start-up warning about its optional command scanner is noise here)
    text = "\n".join(body).strip()
    return session, text[-1500:], bool(text)


def result_of(log_path: Path) -> tuple[str | None, str, bool]:
    """(session_id, result text, saw_assistant) from a stream-json log."""
    session, text, saw = None, "", False
    for ev in worker._events(log_path):
        if ev.get("type") == "assistant":
            saw = True
        if ev.get("session_id"):
            session = ev["session_id"]
        if ev.get("type") == "result":
            text = str(ev.get("result") or ev.get("error") or ev.get("subtype") or "")
    return session, text or worker.tail(log_path, 6), saw


# -- the runner ---------------------------------------------------------------------------------------------------
@dataclass
class OrgRunner:
    settings: Settings
    boards: dict[str, Board]
    org_board: OrgBoard
    spawn: Callable = spawn
    prepare_cwd: Callable = worker.prepare_cwd
    clock: Callable[[], float] = time.time
    running: dict[str, OrgRun] = field(default_factory=dict)   # agent name -> run (one run per agent at a time)
    spawn_hermes: Callable = spawn_hermes
    gate: object | None = None            # local_gate.LocalGate: `up` False holds runtime: hermes wakes

    def __post_init__(self):
        org = self.settings.org
        assert org is not None
        self.org = org
        self.agents = load_agents(org.agents_file)
        self._agents_mtime = self._agents_file_mtime()
        self.state_path = self.settings.runs_dir / "org-state.json"
        self.state = OrgState.load(self.state_path)
        self.tz = ZoneInfo(org.timezone)

    # -- roster reload -------------------------------------------------------------------------------------------
    def _agents_file_mtime(self) -> float:
        try:
            return self.org.agents_file.stat().st_mtime
        except OSError:
            return 0.0

    def _reload_agents_if_changed(self) -> None:
        """A roster edit (e.g. an agent's `runtime` flipped to zen-agent) only takes effect for a dispatcher that is
        already running if it re-reads org-agents.json: self.agents was otherwise frozen at startup, so a flipped
        agent kept getting spawned as claude-code and failing with FileNotFoundError on its old prompt_file every
        tick (rune, 2026-10-24) until the service was restarted. Check the file's mtime once per tick instead."""
        mtime = self._agents_file_mtime()
        if mtime == self._agents_mtime:
            return
        try:
            self.agents = load_agents(self.org.agents_file)
            self._agents_mtime = mtime
            log.info("reloaded %s (roster changed on disk)", self.org.agents_file)
        except (OSError, ValueError, KeyError) as exc:
            log.warning("could not reload %s: %s; keeping the previous roster", self.org.agents_file, exc)

    # -- startup -----------------------------------------------------------------------------------------------------
    def recover(self) -> int:
        """Wakeups still claimed at startup belong to runs that died with the previous dispatcher; release them."""
        n = self._safe(self.org_board.release) or 0
        if n:
            log.warning("released %d wakeup(s) claimed by a previous dispatcher", n)
        return n

    # -- one pass --------------------------------------------------------------------------------------------------
    def tick(self, now: float, may_spawn: Callable[[], bool], may_spawn_local: Callable[[], bool] | None = None) -> int:
        """Fires due routines, then starts one run per agent that has work. Returns the number of Claude runs started
        (the daily cap counts those; a Hermes wake on the local model is checked with may_spawn_local instead)."""
        may_spawn_local = may_spawn_local or may_spawn
        self._reload_agents_if_changed()
        self._fire_routines(now)
        started = 0
        pending: dict[str, list[dict]] = {}
        try:
            for w in self.org_board.open_wakeups():
                pending.setdefault(w["agent_name"], []).append(w)
        except BoardError as exc:
            log.warning("cli-org wakeups failed: %s", exc)
            return 0
        tasks: dict[str, tuple[str, str, dict]] = {}
        order = lambda t: (-int(t.get("priority") or 0), int(t.get("created_at") or 0))   # noqa: E731
        for board_name, board in self.boards.items():
            if hasattr(board, "ready_tasks"):
                # One listing per board per tick, filtered here (was one `hermes kanban list` per bot, 22 every 30 s).
                for t in sorted(self._safe(board.ready_tasks) or [], key=order):
                    name = t.get("assignee")
                    if name in self.agents and not self.agents[name].is_hermes:
                        tasks.setdefault(name, (board_name, t["id"], t))
                continue
            for name in self.agents:
                if self.agents[name].is_hermes:
                    continue      # a Hermes profile's cards belong to Hermes's own dispatcher
                for t in sorted(self._safe(board.tasks, name, "ready") or [], key=order):
                    tasks.setdefault(name, (board_name, t["id"], t))
        for name in sorted(set(pending) | set(tasks), key=lambda n: (not self.agents[n].is_lead if n in self.agents else True, n)):
            agent = self.agents.get(name)
            if agent is None or name in self.running or not self._allowed(agent, now):
                continue
            if agent.is_hermes:
                if self.gate is not None and agent.name in getattr(self.gate, "profiles", ()) and not getattr(self.gate, "up", True):
                    continue      # its model host is down: the wakeups stay open until it is back
                if may_spawn_local() and pending.get(name):
                    self._start(agent, pending[name], None, now)
                continue
            if not may_spawn():
                break
            if self._start(agent, pending.get(name, []), tasks.get(name), now):
                started += 1
        return started

    def _allowed(self, agent: OrgAgent, now: float) -> bool:
        s = self.state.wakes.get(agent.name) or {}
        day = self._day(now)
        n = s.get("n", 0) if s.get("day") == day else 0
        if n >= agent.wakes_per_day:
            return False
        return now - float(s.get("last", 0)) >= agent.cooldown_minutes * 60

    def _start(self, agent: OrgAgent, wakeups: list[dict], task: tuple[str, str, dict] | None, now: float) -> bool:
        run_id = f"org-{agent.name}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now))}"
        task_ref = None
        if task:
            board_name, task_id, _ = task
            board = self.boards[board_name]
            if board.claim(task_id, agent.max_runtime_minutes * 60 + 900, agent.name):
                task_ref = (board_name, task_id)
            elif not wakeups:
                return False
        try:
            claimed = self.org_board.claim(agent.name, run_id)
        except BoardError as exc:
            log.warning("wake-claim failed for %s: %s", agent.name, exc)
            claimed = []
        if not claimed and not task_ref:
            return False
        lane = self.settings.lanes.get(agent.lane) if agent.lane else None
        session = self._session_to_resume(agent)
        if session and not self._continues_work(agent, claimed, task_ref):
            session = None                      # new work: a fresh context; the saved session is replaced when this run ends
        log_path = self.settings.runs_dir / f"{run_id}.{'log' if agent.is_hermes else 'jsonl'}"
        try:
            cwd = self.prepare_cwd(self.settings, lane, task_ref[1]) if (lane and lane.worktree and task_ref) else str(self.org.vault)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:      # frames this wake in the log; the Second Brain ledger reads it
                at = datetime.fromtimestamp(now, tz=ZoneInfo("UTC")).isoformat()
                f.write(json.dumps(run_header(agent, run_id, claimed, task_ref, bool(session), at)) + "\n")
            if agent.is_hermes:
                proc = self.spawn_hermes(self.settings, agent, wake_prompt(agent, claimed, None), session, run_id, cwd, log_path)
            else:
                proc = self.spawn(self.settings, agent, lane, task_ref, wake_prompt(agent, claimed, task_ref), session, run_id, cwd, log_path)
        except Exception as exc:
            log.exception("spawn failed for %s", agent.name)
            if task_ref:
                self._safe(self.boards[task_ref[0]].block, task_ref[1], f"cc-dispatcher could not start {agent.name}: {exc}"[:500], "capability")
            if claimed:
                self._safe(self.org_board.done, [w["id"] for w in claimed], run_id, 127, f"could not start the worker: {exc}")
            return False
        self.running[agent.name] = OrgRun(agent, run_id, [w["id"] for w in claimed], task_ref, proc, log_path, now, bool(session),
                                        task_ids=self._task_ids(claimed, task_ref))
        day = self._day(now)
        s = self.state.wakes.get(agent.name) or {}
        self.state.wakes[agent.name] = {"day": day, "n": (s.get("n", 0) if s.get("day") == day else 0) + 1, "last": now}
        self.state.save(self.state_path)
        if task_ref:
            self._safe(self.boards[task_ref[0]].comment, task_ref[1], f"{agent.name} ({agent.model}) started on this task. Live log: {log_path}", AUTHOR)
        log.info("woke %s for %s (pid %s)", agent.name, ", ".join(describe_wakeups(claimed)) or f"task {task_ref[1] if task_ref else '?'}",
                 getattr(proc, "pid", "?"))
        return True

    @staticmethod
    def _task_ids(claimed: list[dict], task_ref: tuple[str, str] | None) -> tuple:
        ids = {str(w["ref"]) for w in claimed if w.get("reason") == "task" and w.get("ref")}
        if task_ref:
            ids.add(task_ref[1])
        return tuple(sorted(ids))

    def _continues_work(self, agent: OrgAgent, claimed: list[dict], task_ref: tuple[str, str] | None) -> bool:
        """True when this wake picks up what the saved session was doing, so its context is needed: a ticket that session
        worked, a reply inside a thread, a message about such a ticket, or the owner's answer to a ship request.
        A new task, a top-level message and a routine start fresh."""
        worked = set(self.state.session_tasks.get(agent.name, []))
        if task_ref and task_ref[1] in worked:
            return True
        for w in claimed:
            reason, ref = w.get("reason"), str(w.get("ref") or "")
            if reason == "task":
                if ref in worked:
                    return True
            elif reason == "owner" and ref.startswith("ship:"):
                return True
            elif reason in ("dm", "mention", "handoff", "owner", "konrad"):
                ctx = self.org_board.message(agent.name, ref)
                if ctx and (ctx.get("thread_id") or (ctx.get("task_id") and str(ctx["task_id"]) in worked)):
                    return True
        return False

    def _session_to_resume(self, agent: OrgAgent) -> str | None:
        try:
            s = self.org_board.session(agent.name)
        except BoardError:
            return None
        sid, started = s.get("session_id"), s.get("session_started")
        if not sid:
            return None
        if agent.is_hermes != bool(HERMES_SESSION.match(str(sid))):
            # The bot changed runtime (lena: Claude Code -> Hermes, 2026-10-17): the old session is the other runtime's.
            self._safe(self.org_board.set_session, agent.name, None)
            return None
        if not agent.is_hermes and not CLAUDE_SESSION.match(str(sid)):
            # A bridge-era id (12 hex): the real CLI refuses `--resume` on it, and the failed run would eat the wakeup.
            self._safe(self.org_board.set_session, agent.name, None)
            return None
        try:
            age_days = (datetime.now(self.tz).date() - datetime.fromisoformat(str(started)[:10]).date()).days
        except ValueError:
            age_days = 0
        if age_days >= self.org.session_reset_days:
            self._safe(self.org_board.set_session, agent.name, None)
            return None
        return sid

    # -- after a run -----------------------------------------------------------------------------------------------
    def reap(self, now: float) -> None:
        for name, r in list(self.running.items()):
            code = r.proc.poll()
            if code is None:
                continue
            del self.running[name]
            session, text, saw = (hermes_result_of if r.agent.is_hermes else result_of)(r.log_path)
            if r.timed_out:
                text = f"hit its max runtime ({r.agent.max_runtime_minutes} min). {text}"
            if session and (code == 0 or saw):
                self._safe(self.org_board.set_session, name, session)
                kept = set(self.state.session_tasks.get(name, [])) if r.resumed else set()
                self.state.session_tasks[name] = sorted(kept | set(r.task_ids))[-50:]
                self.state.save(self.state_path)
            elif r.resumed and not saw:
                # The resume itself failed (session gone or unreadable): start fresh next time.
                self._safe(self.org_board.set_session, name, None)
                log.warning("%s: resume failed, session cleared", name)
            if r.wakeup_ids:
                self._safe(self.org_board.done, r.wakeup_ids, r.run_id, code, text)
            if r.task:
                board = self.boards[r.task[0]]
                task_id = r.task[1]
                self._safe(board.attach, task_id, str(r.log_path), AUTHOR)
                if self._safe(board.status, task_id) == "running":
                    if r.timed_out:
                        streak = self.state.task_timeouts.get(task_id, 0) + 1
                        self.state.task_timeouts[task_id] = streak
                    else:
                        streak = 0
                        self.state.task_timeouts.pop(task_id, None)
                    self.state.save(self.state_path)
                    if r.timed_out and streak >= TIMEOUT_ESCALATE_AFTER:
                        # A card that hits the runtime cap repeatedly in a row, re-waking the same agent every time
                        # it is returned to ready, is not "transient" (tau, t_cf79b6dc, 2026-10-24: 6 runs over 3h,
                        # each producing zero output). Stop the loop and put it in front of Konrad instead. Forget
                        # the streak now: a human unblocking it for another try starts the count fresh, not mid-way.
                        self.state.task_timeouts.pop(task_id, None)
                        self.state.save(self.state_path)
                        why = (f"{name} hit its max runtime {streak} times in a row on this card "
                               f"({r.agent.max_runtime_minutes} min each) without completing or blocking it. Stopping "
                               f"automatic retries: check {name}'s session/auth, split the card, or raise its runtime.")
                        self._safe(board.block, task_id, f"{why} Last output:\n{worker.tail(r.log_path)}"[:3500], "needs_input")
                    else:
                        why = "hit its max runtime" if r.timed_out else f"exited (code {code}) without complete or block"
                        self._safe(board.block, task_id, f"{name} {why}. Last output:\n{worker.tail(r.log_path)}"[:3500], "transient")
                elif self.state.task_timeouts.pop(task_id, None) is not None:
                    self.state.save(self.state_path)       # the card moved on (done/blocked itself): forget the streak
            log.info("%s finished (exit %s): %s", name, code, text[:200].replace("\n", " "))

    def enforce_runtime(self, now: float, grace: float) -> None:
        import signal
        for r in self.running.values():
            limit = r.started + r.agent.max_runtime_minutes * 60
            if r.interrupted_at is None and now > limit:
                r.timed_out, r.interrupted_at = True, now
                _signal(r, signal.SIGINT)
            elif r.interrupted_at is not None and now > r.interrupted_at + grace:
                _signal(r, signal.SIGTERM)

    # -- routines ---------------------------------------------------------------------------------------------------
    def _fire_routines(self, now: float) -> None:
        local = datetime.fromtimestamp(now, self.tz)
        day = local.strftime("%Y-%m-%d")
        for agent in self.agents.values():
            for routine in agent.routines:
                name = routine.get("name")
                if not name:
                    continue
                key = f"{agent.name}/{name}"
                if self.state.routines.get(key) == day or not routine_due(routine, local):
                    continue
                try:
                    self.org_board.add(agent.name, "routine", name)   # None = one is already open; fine
                except BoardError as exc:
                    log.warning("routine %s for %s not added (%s); retrying next tick", name, agent.name, exc)
                    continue
                self.state.routines[key] = day
                self.state.save(self.state_path)
                log.info("routine %s fired for %s", name, agent.name)

    def _day(self, now: float) -> str:
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except BoardError as exc:
            log.warning("board call failed: %s", exc)
            return None


def routine_due(routine: dict, local: datetime) -> bool:
    """`at: "05:30"` every day; `on: "sun 20:00"` once a week. Due from that time until midnight."""
    spec = routine.get("at") or routine.get("on") or ""
    parts = spec.split()
    if len(parts) == 2:
        if parts[0].lower()[:3] != WEEKDAYS[local.weekday()]:
            return False
        hhmm = parts[1]
    elif len(parts) == 1:
        hhmm = parts[0]
    else:
        return False
    try:
        hh, mm = (int(x) for x in hhmm.split(":"))
    except ValueError:
        return False
    return (local.hour, local.minute) >= (hh, mm)


def _signal(r: OrgRun, sig: int) -> None:
    try:
        r.proc.send_signal(sig)
    except (ProcessLookupError, OSError):
        pass
