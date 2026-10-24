"""One dispatcher pass at a time: reap finished workers, enforce runtimes, then claim and spawn new work."""
from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from . import verify, worker
from .board import Board, BoardError, BoardLike
from .config import Lane, Settings
from .local_gate import LocalGate
from .org import OrgBoard, OrgRunner

log = logging.getLogger("cc-dispatcher")
KILL_GRACE_SECONDS = 30
AUTHOR = "cc-dispatcher"


@dataclass
class Running:
    board: str
    task_id: str
    lane: Lane
    proc: object                  # subprocess.Popen, or a fake in tests
    log_path: Path
    started: float
    interrupted_at: float | None = None
    timed_out: bool = False
    cwd: str = ""                 # where the worker ran; verifiers with cwd = "worker" run here


@dataclass
class State:
    """Persisted in runs_dir/state.json so a restart keeps the daily count and any rate-limit pause."""
    day: str = ""
    spawned_today: int = 0
    paused_until: float = 0.0

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            return cls(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__))
        tmp.replace(path)


@dataclass
class Dispatcher:
    settings: Settings
    boards: dict[str, BoardLike]
    spawn: Callable = worker.spawn
    prepare_cwd: Callable = worker.prepare_cwd
    clock: Callable[[], float] = time.time
    run_verifier: Callable = verify.run
    running: dict[str, Running] = field(default_factory=dict)
    _capped_day: str = ""
    _ticks: int = 0

    org: OrgRunner | None = None
    gate: LocalGate | None = None

    def __post_init__(self):
        self.state_path = self.settings.runs_dir / "state.json"
        self.state = State.load(self.state_path)
        if self.org is None and self.settings.org is not None:
            self.org = OrgRunner(self.settings, self.boards, OrgBoard(self.settings.org.cli_org), clock=self.clock)
        if self.gate is None and self.settings.local is not None:
            self.gate = LocalGate(self.settings.local.health_url, self.settings.local.profiles, self.boards,
                                  self.settings.runs_dir / "local-gate.json")
        if self.org is not None and self.org.gate is None:
            self.org.gate = self.gate

    # -- startup -------------------------------------------------------------------------------------------------
    def recover(self) -> list[str]:
        """Tasks left `running` on a cc lane belong to a dispatcher that died; their workers died with it (systemd
        kills the cgroup). Block them with a reason instead of waiting out a TTL of an hour or more."""
        blocked = []
        if self.org is not None:
            self.org.recover()
        for board_name, board in self.boards.items():
            for lane in self.settings.lanes.values():
                for task in self._safe(board.tasks, lane.name, "running") or []:
                    if task["id"] in self.running:
                        continue
                    self._safe(board.block, task["id"],
                               "cc-dispatcher restarted while this task ran; unblock to retry", "transient", lane.name)
                    blocked.append(task["id"])
        return blocked

    # -- one pass ------------------------------------------------------------------------------------------------
    def tick(self) -> None:
        now = self.clock()
        self._reap(now)
        self._enforce_runtime(now)
        self._roll_day(now)
        if self.gate is not None:
            self.gate.tick(now)
        if not self._may_spawn(now):
            if self.org is not None and self._may_spawn_local(now):
                self._tick_org(now)      # capped for Claude, but local-model bots still run
            return
        # Lanes and the org take turns going first: with two slots, a tick that always served the lanes first let two
        # research cards hold both slots while four bots' cards and their mentions waited (2026-10-15).
        self._ticks += 1
        if self.org is not None and self._ticks % 2 == 0:
            self._tick_org(now)
            self._tick_lanes(now)
        else:
            self._tick_lanes(now)
            if self.org is not None:
                self._tick_org(now)

    def _tick_lanes(self, now: float) -> None:
        for board_name, board in self.boards.items():
            for lane in self.settings.lanes.values():
                for task in sorted(self._safe(board.tasks, lane.name, "ready") or [],
                                   key=lambda t: (-int(t.get("priority") or 0), int(t.get("created_at") or 0))):
                    if not self._may_spawn(now):
                        return
                    self._start(board_name, board, lane, task["id"], now)

    def _tick_org(self, now: float) -> None:
        assert self.org is not None
        started = self.org.tick(now, lambda: self._may_spawn(now), lambda: self._may_spawn_local(now))
        if started:
            self.state.spawned_today += started
            self.state.save(self.state_path)

    def _may_spawn_local(self, now: float) -> bool:
        """A wake on the local model: the PAUSE file and the concurrency limit apply (droplet RAM); the daily cap and
        the Claude rate-limit pause don't."""
        if self.settings.pause_file.exists():
            return False
        return len(self.running) + (len(self.org.running) if self.org is not None else 0) < self.settings.max_in_progress

    def _may_spawn(self, now: float) -> bool:
        s = self.settings
        if s.pause_file.exists() or now < self.state.paused_until:
            return False
        in_progress = len(self.running) + (len(self.org.running) if self.org is not None else 0)
        if self.state.spawned_today >= s.daily_cap:
            # Once per capped day, so a capped dispatcher never looks like a dead one (2026-10-15: a bot's first
            # card sat `ready` for hours behind a silent cap).
            if self._capped_day != self.state.day:
                self._capped_day = self.state.day
                log.warning("daily cap reached (%d spawns on %s); nothing starts until the day rolls over at 00:00 %s",
                            s.daily_cap, self.state.day, s.day_timezone)
            return False
        return in_progress < s.max_in_progress

    def _start(self, board_name: str, board: BoardLike, lane: Lane, task_id: str, now: float) -> None:
        ttl = lane.max_runtime_minutes * 60 + 900   # outlives our own runtime limit, so only we end the run
        if not board.claim(task_id, ttl, lane.name):
            return
        log_path = self.settings.runs_dir / f"{task_id}.jsonl"
        try:
            base = None
            if lane.worktree:     # a retry builds on the failed attempt's branch, not on a clean HEAD
                parent = verify.retry_of((self._safe(board.task, task_id) or {}).get("body") or "")
                base = f"cc/{parent}" if parent else None
            cwd = self.prepare_cwd(self.settings, lane, task_id, base=base)
            self.settings.runs_dir.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:     # frames this run in the log; the Second Brain ledger reads it
                started = datetime.now(timezone.utc)
                f.write(json.dumps({"type": "cc_dispatch", "run_id": f"{task_id}-{started.strftime('%Y%m%d-%H%M%S')}",
                                    "task_id": task_id, "board": board_name, "lane": lane.name,
                                    "model": lane.model, "at": started.isoformat()}) + "\n")
            proc = self.spawn(self.settings, lane, board_name, task_id, cwd, log_path)
        except Exception as exc:   # worktree or exec failure: the task must not sit claimed until the TTL ends
            log.exception("spawn failed for %s", task_id)
            self._safe(board.block, task_id, f"cc-dispatcher could not start the worker: {exc}"[:500], "capability", lane.name)
            return
        self.running[task_id] = Running(board_name, task_id, lane, proc, log_path, now, cwd=cwd)
        self.state.spawned_today += 1
        self.state.save(self.state_path)
        self._safe(board.comment, task_id, f"Claude Code worker started ({lane.name}, {lane.model}). "
                                           f"Live log: {log_path}", AUTHOR)
        log.info("started %s on %s (pid %s)", task_id, lane.name, getattr(proc, "pid", "?"))

    def _reap(self, now: float) -> None:
        if self.org is not None:
            self.org.reap(now)
        for task_id, r in list(self.running.items()):
            code = r.proc.poll()
            if code is None:
                continue
            del self.running[task_id]
            board = self.boards[r.board]
            self._safe(board.attach, task_id, str(r.log_path), AUTHOR)
            no_login = worker.auth_failed(r.log_path)
            if no_login:
                # Every task would fail the same way: stop spawning until someone logs in and removes the pause.
                self.settings.pause_file.parent.mkdir(parents=True, exist_ok=True)
                self.settings.pause_file.write_text("claude is not logged in: run `claude` then /login as hermes\n")
                log.error("claude is not logged in; wrote %s", self.settings.pause_file)
            elif worker.hit_rate_limit(r.log_path):
                self.state.paused_until = now + self.settings.rate_limit_pause_minutes * 60
                self.state.save(self.state_path)
                log.warning("rate limit seen in %s; pausing spawns", task_id)
            status = self._safe(board.status, task_id)
            if status != "running":
                log.info("%s finished: %s (exit %s)", task_id, status, code)
                if status == "done":
                    self._verify(r, board, task_id)
                continue
            # The worker exited without complete/block: the same rule Hermes calls a protocol violation.
            if no_login:
                why = (f"could not run: Claude Code is not logged in on the droplet. Konrad: run `claude`, then /login, "
                       f"as hermes, delete {self.settings.pause_file}, and unblock this task")
            elif r.timed_out:
                why = "hit its max runtime"
            else:
                why = f"exited (code {code}) without complete or block"
            self._safe(board.block, task_id,
                       f"Claude worker {why}. Last output:\n{worker.tail(r.log_path)}"[:3500], "transient", r.lane.name)

    def _verify(self, r: Running, board: BoardLike, task_id: str) -> None:
        """Never raises: a crash in the check must not take the dispatcher loop down with it."""
        try:
            self._verify_inner(r, board, task_id)
        except Exception as exc:
            log.exception("verify crashed for %s", task_id)
            try:
                outcome = verify.Outcome("unknown", None, False, None, f"verify crashed: {exc}"[:500], 0.0)
                verify.record(r.log_path, outcome, task_id, r.lane.name)
                self._safe(board.comment, task_id, f"Verify crashed: {exc}"[:500], AUTHOR)
            except Exception:
                log.exception("could not report the verify crash for %s", task_id)

    def _verify_inner(self, r: Running, board: BoardLike, task_id: str) -> None:
        """A completed card that names a verifier is checked by code, never by the worker's own word."""
        task = self._safe(board.task, task_id) or {}
        body = task.get("body") or ""
        name = verify.requested(body)
        if name is None:
            return
        v = self.settings.verifiers.get(name)
        # Only lanes listed on the verifier may trigger it: a check runs code as hermes, and a lane without Bash
        # must not get it by writing a test file and filing a card that names the check.
        runnable = v is not None and r.lane.name in v.lanes
        if v is None:
            outcome = verify.Outcome(name, None, False, None,
                                     f"verifier {name!r} is not in lanes.toml (known: {sorted(self.settings.verifiers)})", 0.0)
        elif not runnable:
            outcome = verify.Outcome(name, None, False, None,
                                     f"verifier {name} is not allowed on lane {r.lane.name}", 0.0)
        else:
            env = worker.worker_env(dict(os.environ), self.settings, r.lane, r.board, task_id)
            outcome = self.run_verifier(v, r.cwd or r.lane.cwd, env)
        try:
            verify.record(r.log_path, outcome, task_id, r.lane.name)
        except OSError:
            log.exception("could not record the verify outcome for %s", task_id)
        metric = "" if outcome.metric is None else f" ({outcome.metric:g})"
        if outcome.passed:
            self._safe(board.comment, task_id, f"Verified: {name} passed{metric}.", AUTHOR)
            return
        k, n = verify.attempt(body) or (1, self.settings.max_attempts)
        n = min(n, self.settings.max_attempts)
        title = verify.base_title(task.get("title") or task_id)
        if runnable and k < n:
            made = self._safe(board.create, f"[retry {k + 1}/{n}] {title}",
                              verify.retry_body(body, outcome, k + 1, n, task_id),
                              r.lane.name, r.lane.name, (task_id,), None)
            said = (f"Verify {name} failed{metric}; opened retry {k + 1}/{n}." if made
                    else f"Verify {name} failed{metric}; could not open the retry card.")
        else:
            made = self._safe(board.create, f"[needs Konrad] {title}", verify.escalation_body(body, outcome, k, n, task_id),
                              r.lane.name, r.lane.name, (task_id,), "blocked")
            said = (f"Verify {name} failed{metric}; blocked for Konrad." if made
                    else f"Verify {name} failed{metric}; could not open the escalation card.")
        self._safe(board.comment, task_id, said, AUTHOR)

    def _enforce_runtime(self, now: float) -> None:
        if self.org is not None:
            self.org.enforce_runtime(now, KILL_GRACE_SECONDS)
        for r in self.running.values():
            limit = r.started + r.lane.max_runtime_minutes * 60
            if r.interrupted_at is None and now > limit:
                r.timed_out, r.interrupted_at = True, now
                self._signal(r, signal.SIGINT)        # ends the turn cleanly; SIGTERM would leave it unfinished
            elif r.interrupted_at is not None and now > r.interrupted_at + KILL_GRACE_SECONDS:
                self._signal(r, signal.SIGTERM)

    def _roll_day(self, now: float) -> None:
        # The cap's day is the org's day (Asia/Tokyo), not the droplet's UTC clock: on 2026-10-15 the 05:30 Tokyo
        # stand-ups and the morning's DMs used the whole cap by 13:45 Tokyo and nothing ran until 09:00 the next morning.
        day = datetime.fromtimestamp(now, ZoneInfo(self.settings.day_timezone)).strftime("%Y-%m-%d")
        if day != self.state.day:
            self.state.day, self.state.spawned_today = day, 0
            self.state.save(self.state_path)

    @staticmethod
    def _signal(r: Running, sig: int) -> None:
        try:
            r.proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except BoardError as exc:
            log.warning("board call failed: %s", exc)
            return None

    # -- shutdown ------------------------------------------------------------------------------------------------
    def stop_all(self, wait_seconds: float = 20, sleep: Callable[[float], None] = time.sleep) -> None:
        """SIGINT every worker so it ends its turn, wait a little, then SIGTERM what's left. Their tasks stay
        `running`; the next start's recover() blocks them with a reason."""
        everyone = list(self.running.values()) + (list(self.org.running.values()) if self.org is not None else [])
        for r in everyone:
            self._signal(r, signal.SIGINT)
        waited = 0.0
        while waited < wait_seconds and any(r.proc.poll() is None for r in everyone):
            sleep(0.5)
            waited += 0.5
        for r in everyone:
            if r.proc.poll() is None:
                self._signal(r, signal.SIGTERM)
