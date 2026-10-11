"""One dispatcher pass at a time: reap finished workers, enforce runtimes, then claim and spawn new work."""
from __future__ import annotations

import json
import logging
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import worker
from .board import Board, BoardError
from .config import Lane, Settings

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
    boards: dict[str, Board]
    spawn: Callable = worker.spawn
    prepare_cwd: Callable = worker.prepare_cwd
    clock: Callable[[], float] = time.time
    running: dict[str, Running] = field(default_factory=dict)

    def __post_init__(self):
        self.state_path = self.settings.runs_dir / "state.json"
        self.state = State.load(self.state_path)

    # -- startup -------------------------------------------------------------------------------------------------
    def recover(self) -> list[str]:
        """Tasks left `running` on a cc lane belong to a dispatcher that died; their workers died with it (systemd
        kills the cgroup). Block them with a reason instead of waiting out a TTL of an hour or more."""
        blocked = []
        for board_name, board in self.boards.items():
            for lane in self.settings.lanes.values():
                for task in self._safe(board.tasks, lane.name, "running") or []:
                    if task["id"] in self.running:
                        continue
                    self._safe(board.block, task["id"],
                               "cc-dispatcher restarted while this task ran; unblock to retry", "transient")
                    blocked.append(task["id"])
        return blocked

    # -- one pass ------------------------------------------------------------------------------------------------
    def tick(self) -> None:
        now = self.clock()
        self._reap(now)
        self._enforce_runtime(now)
        self._roll_day(now)
        if not self._may_spawn(now):
            return
        for board_name, board in self.boards.items():
            for lane in self.settings.lanes.values():
                for task in sorted(self._safe(board.tasks, lane.name, "ready") or [],
                                   key=lambda t: (-int(t.get("priority") or 0), int(t.get("created_at") or 0))):
                    if not self._may_spawn(now):
                        return
                    self._start(board_name, board, lane, task["id"], now)

    def _may_spawn(self, now: float) -> bool:
        s = self.settings
        if s.pause_file.exists() or now < self.state.paused_until:
            return False
        return len(self.running) < s.max_in_progress and self.state.spawned_today < s.daily_cap

    def _start(self, board_name: str, board: Board, lane: Lane, task_id: str, now: float) -> None:
        ttl = lane.max_runtime_minutes * 60 + 900   # outlives our own runtime limit, so only we end the run
        if not board.claim(task_id, ttl):
            return
        log_path = self.settings.runs_dir / f"{task_id}.jsonl"
        try:
            cwd = self.prepare_cwd(self.settings, lane, task_id)
            proc = self.spawn(self.settings, lane, board_name, task_id, cwd, log_path)
        except Exception as exc:   # worktree or exec failure: the task must not sit claimed until the TTL ends
            log.exception("spawn failed for %s", task_id)
            self._safe(board.block, task_id, f"cc-dispatcher could not start the worker: {exc}"[:500], "capability")
            return
        self.running[task_id] = Running(board_name, task_id, lane, proc, log_path, now)
        self.state.spawned_today += 1
        self.state.save(self.state_path)
        self._safe(board.comment, task_id, f"Claude Code worker started ({lane.name}, {lane.model}). "
                                           f"Live log: {log_path}", AUTHOR)
        log.info("started %s on %s (pid %s)", task_id, lane.name, getattr(proc, "pid", "?"))

    def _reap(self, now: float) -> None:
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
                       f"Claude worker {why}. Last output:\n{worker.tail(r.log_path)}"[:3500], "transient")

    def _enforce_runtime(self, now: float) -> None:
        for r in self.running.values():
            limit = r.started + r.lane.max_runtime_minutes * 60
            if r.interrupted_at is None and now > limit:
                r.timed_out, r.interrupted_at = True, now
                self._signal(r, signal.SIGINT)        # ends the turn cleanly; SIGTERM would leave it unfinished
            elif r.interrupted_at is not None and now > r.interrupted_at + KILL_GRACE_SECONDS:
                self._signal(r, signal.SIGTERM)

    def _roll_day(self, now: float) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
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
        for r in self.running.values():
            self._signal(r, signal.SIGINT)
        waited = 0.0
        while waited < wait_seconds and any(r.proc.poll() is None for r in self.running.values()):
            sleep(0.5)
            waited += 0.5
        for r in self.running.values():
            if r.proc.poll() is None:
                self._signal(r, signal.SIGTERM)
