"""arecord between Talk press and release. Start and stop run in different hook processes,
so the recorder is tracked by a pid file; arecord finalises the WAV header on SIGINT."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable

MAX_SECONDS = "30"


def _pid(state: Path) -> int | None:
    try:
        return int((state / "rec.pid").read_text().strip())
    except (OSError, ValueError):
        return None


def _read_cmdline(pid: int) -> str | None:
    """argv of `pid` from /proc, "" if the pid is gone, None where there is no /proc (macOS)."""
    if not Path("/proc/self").exists():
        return None
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def _alive(pid: int, kill: Callable = os.kill) -> bool:
    try:
        kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _ours(pid: int, kill: Callable, cmdline: Callable) -> bool:
    """A recorded pid is ours only if it is alive and looks like arecord; anything else is stale."""
    if not _alive(pid, kill):
        return False
    cl = cmdline(pid)
    return cl is None or "arecord" in cl


def start(state: Path, cmd: list, popen: Callable = subprocess.Popen, kill: Callable = os.kill,
          cmdline: Callable = _read_cmdline) -> bool:
    state.mkdir(parents=True, exist_ok=True)
    pid = _pid(state)
    if pid and _ours(pid, kill, cmdline):
        return False
    wav = state / "rec.wav"
    wav.unlink(missing_ok=True)
    proc = popen([*cmd, "-d", MAX_SECONDS, str(wav)], start_new_session=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (state / "rec.pid").write_text(str(proc.pid))
    (state / "listening").touch()
    return True


def _finish(state: Path, sig: int, kill: Callable, wait_s: float, cmdline: Callable) -> None:
    pid = _pid(state)
    try:
        if pid and _ours(pid, kill, cmdline):
            try:
                kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline and _alive(pid, kill):
                time.sleep(0.05)
            if _alive(pid, kill):
                try:
                    kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    finally:
        (state / "rec.pid").unlink(missing_ok=True)
        (state / "listening").unlink(missing_ok=True)


def stop(state: Path, min_bytes: int, kill: Callable = os.kill, wait_s: float = 3.0,
         cmdline: Callable = _read_cmdline) -> Path | None:
    _finish(state, signal.SIGINT, kill, wait_s, cmdline)
    wav = state / "rec.wav"
    if not wav.exists() or wav.stat().st_size < min_bytes:
        return None
    return wav


def cancel(state: Path, kill: Callable = os.kill, cmdline: Callable = _read_cmdline) -> None:
    _finish(state, signal.SIGTERM, kill, 1.0, cmdline)
    (state / "rec.wav").unlink(missing_ok=True)
