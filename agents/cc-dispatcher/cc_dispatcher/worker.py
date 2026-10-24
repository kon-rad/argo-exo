"""Build and start one headless Claude Code worker for one kanban task."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .config import Lane, Settings

# The worker's environment is built from scratch, never copied from the dispatcher: an ANTHROPIC_API_KEY would
# outrank the subscription login, and nothing from the Hermes env belongs in an agent's environment.
PASS_THROUGH = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ")


def kanban_tools(hermes_bin: str, board: str) -> list[str]:
    """Allow rules for the board verbs a worker needs, pinned to its board. Both spellings of the binary."""
    verbs = ("show", "list", "comment", "complete", "block", "schedule", "create", "attach")
    rules = []
    for exe in (hermes_bin, "hermes"):
        rules += [f"Bash({exe} kanban --board {board} {v} *)" for v in verbs]
    return sorted(set(rules))


def engine_board_tools(cli_board_bin: str) -> list[str]:
    """Allow rules for the engine board's verbs a worker needs (board-design.md section 5.2): no --board on any of
    these (a card id already names its board), and no per-worker --author (the engine's author is always the
    caller's ORG_AGENT, set once in worker_env, never a flag the worker could spoof)."""
    verbs = ("show", "comment", "create", "finish", "move")
    rules = [f"Bash({exe} {v} *)" for exe in (cli_board_bin, "cli-board") for v in verbs]
    return sorted(set(rules))


def worker_env(base: dict, settings: Settings, lane: Lane, board: str, task_id: str) -> dict:
    env = {k: base[k] for k in PASS_THROUGH if k in base}
    bindir = str(Path(settings.claude_bin).parent)
    env["PATH"] = f"{bindir}:{Path(settings.hermes_bin).parent}:/usr/local/bin:/usr/bin:/bin"
    env.update({
        "HERMES_KANBAN_TASK": task_id, "HERMES_KANBAN_BOARD": board,
        "HERMES_PROFILE": lane.name,              # default --author for `hermes kanban comment`
        "CC_LANE": lane.name,
    })
    if settings.board_backend == "engine":
        # The engine's board_card_* functions check "only its own cards" against this identity; the worker is
        # always its own lane (never cc-dispatcher's), so it can only ever touch the card it was started for.
        env["ORG_AGENT"] = lane.name
    return env


def command(settings: Settings, lane: Lane, board: str, task_id: str) -> list[str]:
    if settings.board_backend == "engine":
        prompt = f"Work card {task_id} on board {board}. Start by reading it: cli-board show {task_id} --json"
        allowed = list(lane.allowed_tools) + engine_board_tools(settings.cli_board_bin)
    else:
        prompt = (f"Work kanban task {task_id} on board {board}. Start by reading it: "
                  f"hermes kanban --board {board} show {task_id} --json")
        allowed = list(lane.allowed_tools) + kanban_tools(settings.hermes_bin, board)
    return [
        settings.claude_bin, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--model", lane.model,
        "--append-system-prompt", lane.prompt,
        "--permission-mode", lane.permission_mode,
        "--permission-prompts", "none",
        "--allowedTools", ",".join(allowed),
        "--disallowedTools", ",".join(lane.denied_tools),
    ]


def prepare_cwd(settings: Settings, lane: Lane, task_id: str, run=subprocess.run, base: str | None = None) -> str:
    """The lane's cwd, or a fresh git worktree of it on branch cc/<task> for worktree lanes. The branch starts from
    `base` (a retry: the failed card's branch, so its commits carry over) when that ref exists, else from HEAD."""
    if not lane.worktree:
        return lane.cwd
    dest = settings.work_dir / task_id
    if not dest.exists():
        settings.work_dir.mkdir(parents=True, exist_ok=True)
        start: list[str] = []
        if base and not base.startswith("-"):
            found = run(["git", "-C", lane.cwd, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
                        capture_output=True, text=True, timeout=30)
            if found.returncode == 0:
                start = [base]
        run(["git", "-C", lane.cwd, "worktree", "add", "-b", f"cc/{task_id}", str(dest), *start],
            check=True, capture_output=True, text=True, timeout=60)
    return str(dest)


def spawn(settings: Settings, lane: Lane, board: str, task_id: str, cwd: str, log_path: Path,
          base_env: dict | None = None) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab")
    try:
        return subprocess.Popen(
            command(settings, lane, board, task_id), cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            env=worker_env(base_env if base_env is not None else dict(os.environ), settings, lane, board, task_id),
            start_new_session=True,   # its own process group, so SIGINT reaches only this worker
        )
    finally:
        log.close()                    # the child holds its own descriptor


def tail(log_path: Path, lines: int = 20) -> str:
    """Readable tail of a stream-json log: assistant text and the final result, else the raw last lines."""
    try:
        raw = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return "(no log)"
    out = []
    for line in raw:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if ev.get("type") == "result":
            out.append(f"[result] {ev.get('subtype', '')}: {str(ev.get('result', ''))[:400]}")
        elif ev.get("type") == "assistant":
            for block in (ev.get("message") or {}).get("content", []):
                if block.get("type") == "text":
                    out.append(block["text"][:400])
                elif block.get("type") == "tool_use":
                    out.append(f"[tool] {block.get('name')}")
    return "\n".join(out[-lines:]) or "(empty log)"


def _events(log_path: Path):
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            yield ev


def auth_failed(log_path: Path) -> bool:
    """The subscription login is missing or expired. Every task would fail the same way, so the caller pauses."""
    for ev in _events(log_path):
        if ev.get("subtype") == "api_retry" and ev.get("error") in ("authentication_failed", "oauth_org_not_allowed"):
            return True
        if ev.get("type") == "result" and "not logged in" in str(ev.get("result", "")).lower():
            return True
    return False


def hit_rate_limit(log_path: Path) -> bool:
    return any(ev.get("subtype") == "api_retry" and ev.get("error") == "rate_limit" for ev in _events(log_path))
