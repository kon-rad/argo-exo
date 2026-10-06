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


def _alive(pid: int, kill: Callable = os.kill) -> bool:
    try:
        kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, just not ours


def start(state: Path, cmd: list, popen: Callable = subprocess.Popen) -> bool:
    state.mkdir(parents=True, exist_ok=True)
    pid = _pid(state)
    if pid and _alive(pid):
        return False
    wav = state / "rec.wav"
    wav.unlink(missing_ok=True)
    proc = popen([*cmd, "-d", MAX_SECONDS, str(wav)], start_new_session=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (state / "rec.pid").write_text(str(proc.pid))
    (state / "listening").touch()
    return True


def _finish(state: Path, sig: int, kill: Callable, wait_s: float) -> None:
    pid = _pid(state)
    if pid:
        try:
            kill(pid, sig)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
    (state / "rec.pid").unlink(missing_ok=True)
    (state / "listening").unlink(missing_ok=True)


def stop(state: Path, min_bytes: int, kill: Callable = os.kill, wait_s: float = 3.0) -> Path | None:
    _finish(state, signal.SIGINT, kill, wait_s)
    wav = state / "rec.wav"
    if not wav.exists() or wav.stat().st_size < min_bytes:
        return None
    return wav


def cancel(state: Path, kill: Callable = os.kill) -> None:
    _finish(state, signal.SIGTERM, kill, 1.0)
    (state / "rec.wav").unlink(missing_ok=True)
