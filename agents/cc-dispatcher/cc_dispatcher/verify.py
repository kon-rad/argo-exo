"""Check a completed task with a named verifier from lanes.toml, and say what happens next.

A card asks for a check with a line `verify: <name>`; retries carry `cc-attempt: k/N` and `cc-retry-of: <task>`,
the card whose branch (cc/<task>) the retry's worktree starts from. The verifier's command comes
only from lanes.toml. The outcome is appended to the task's run log as a `cc_verify` line, which the Second Brain
ledger reads (Projects/secondbrain-app/usage-ledger-plan.md, Part B).
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Verifier

VERIFY_LINE = re.compile(r"^verify:\s*([a-z0-9-]{1,40})\s*$", re.M)
ATTEMPT_LINE = re.compile(r"^cc-attempt:\s*(\d+)/(\d+)\s*$", re.M)
RETRY_OF_LINE = re.compile(r"^cc-retry-of:\s*([A-Za-z0-9_][A-Za-z0-9_-]{0,63})\s*$", re.M)
TITLE_PREFIX = re.compile(r"^\[(?:retry \d+/\d+|needs Konrad)\]\s*")
OUTPUT_TAIL = 4000


@dataclass(frozen=True)
class Outcome:
    name: str
    exit_code: int | None
    passed: bool
    metric: float | None
    output: str
    seconds: float


def requested(body: str) -> str | None:
    m = VERIFY_LINE.search(body or "")
    return m.group(1) if m else None


def attempt(body: str) -> tuple[int, int] | None:
    m = ATTEMPT_LINE.search(body or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def retry_of(body: str) -> str | None:
    """The task id whose branch a retry builds on. Only the last line counts; ids never start with '-'."""
    found = RETRY_OF_LINE.findall(body or "")
    return found[-1] if found else None


def _metric(regex: str | None, text: str) -> float | None:
    if not regex:
        return None
    found = re.findall(regex, text)
    if not found:
        return None
    last = found[-1] if isinstance(found[-1], str) else found[-1][0]
    try:
        return float(last)
    except ValueError:
        return None


def _run_in_group(cmd, cwd, env, timeout, **_):
    """Like subprocess.run, but the command gets its own process group so a timeout kills grandchildren too."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def run(v: Verifier, worker_cwd: str, env: dict, runner=_run_in_group, clock=time.monotonic) -> Outcome:
    cwd = worker_cwd if v.cwd == "worker" else v.cwd
    start = clock()
    try:
        p = runner(list(v.cmd), cwd=cwd, env=env, capture_output=True, text=True, timeout=v.timeout_seconds,
                   encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return Outcome(v.name, None, False, None, f"timed out after {v.timeout_seconds} s", clock() - start)
    except OSError as exc:
        return Outcome(v.name, None, False, None, f"could not run: {exc}", clock() - start)
    text = ((p.stdout or "") + (p.stderr or ""))[-OUTPUT_TAIL:]
    return Outcome(v.name, p.returncode, p.returncode == 0, _metric(v.metric_regex, text), text, clock() - start)


def record(log_path: Path, outcome: Outcome, task_id: str, lane: str, now: datetime | None = None) -> None:
    line = {"type": "cc_verify", "task_id": task_id, "lane": lane, "name": outcome.name, "passed": outcome.passed,
            "exit_code": outcome.exit_code, "metric": outcome.metric, "output": outcome.output,
            "seconds": round(outcome.seconds, 3), "at": (now or datetime.now(timezone.utc)).isoformat()}
    try:
        with open(log_path, "rb") as f:
            f.seek(-1, 2)
            torn = f.read(1) != b"\n"
    except OSError:                  # missing or empty file: nothing to separate from
        torn = False
    with open(log_path, "a") as f:
        f.write(("\n" if torn else "") + json.dumps(line) + "\n")


def base_title(title: str) -> str:
    return TITLE_PREFIX.sub("", title or "").strip()


def _strip_attempt(body: str) -> str:
    return RETRY_OF_LINE.sub("", ATTEMPT_LINE.sub("", body or "")).rstrip()


def _markers(attempt_no: int, max_attempts: int, failed_task: str | None) -> str:
    lines = f"cc-attempt: {attempt_no}/{max_attempts}"
    return lines + (f"\ncc-retry-of: {failed_task}" if failed_task else "")


def retry_body(body: str, outcome: Outcome, attempt_no: int, max_attempts: int, failed_task: str | None = None) -> str:
    """failed_task: the card that just failed; the retry's worktree starts from its branch cc/<failed_task>."""
    return (f"{_strip_attempt(body)}\n\n{_markers(attempt_no, max_attempts, failed_task)}\n\n"
            f"## Why this is a retry\nThe previous attempt was marked complete, but verifier `{outcome.name}` "
            f"failed (exit {outcome.exit_code}). Make it pass; never edit the verifier or the tests to get there.\n\n"
            f"Last output:\n{outcome.output}\n")


def escalation_body(body: str, outcome: Outcome, attempt_no: int, max_attempts: int,
                    failed_task: str | None = None) -> str:
    return (f"{_strip_attempt(body)}\n\n{_markers(attempt_no, max_attempts, failed_task)}\n\n"
            f"## Needs Konrad\nVerifier `{outcome.name}` failed {attempt_no} of {max_attempts} attempts "
            f"(last exit {outcome.exit_code}). Unblock this card to give it one more try, or archive it.\n\n"
            f"Last output:\n{outcome.output}\n")
