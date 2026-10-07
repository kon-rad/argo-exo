"""Run the CRE workflow `exo` through `cre workflow simulate` and pull its result JSON out of the output.

The simulator prints logs, then a "Workflow Simulation Result:" marker and the handler's return value. The handlers
return JSON *strings* (main.ts JSON.stringify's the result), so the value is usually a JSON-encoded string of JSON.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("exo-guardian")

CRE_DIR = Path(__file__).resolve().parents[2] / "cre"
MARKER = "Workflow Simulation Result:"
TIMEOUT_S = 240
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class SimulationError(RuntimeError):
    """`kind` is a short fixed label, safe for the ledger and the caller; the message may quote simulator output
    and is only for the local log."""

    def __init__(self, message: str, kind: str = "simulation failed"):
        super().__init__(message)
        self.kind = kind


# A marker line *starts* with the marker, after optional whitespace and a status glyph (✓, ►, ANSI already
# stripped). "[USER LOG] Workflow Simulation Result: …" or a result whose text quotes the marker is not one.
_MARKER_LINE = re.compile(r"^[^\w\[\]{}\"'<>]*" + re.escape(MARKER) + r"(.*)$")


def _is_result(val) -> bool:
    return isinstance(val, dict) and ("verdict" in val or "ok" in val)


def parse_result(stdout: str) -> dict:
    """The workflow's result: the value belonging to the one marker line, i.e. the rest of that line, or the next
    non-empty line when the marker ends its line. It must be a single JSON object (or a JSON string of one) on that
    line. No marker, more than one marker, or anything else there raises: nothing else in the output is trusted."""
    lines = [_ANSI.sub("", ln) for ln in stdout.splitlines()]
    marks = [(i, m) for i, ln in enumerate(lines) if (m := _MARKER_LINE.match(ln))]
    if len(marks) != 1:
        raise SimulationError(f"expected one result marker, found {len(marks)}: " + stdout.strip()[-300:],
                              "no workflow result")
    i, m = marks[0]
    value = m.group(1).strip()
    if not value:
        value = next((ln.strip() for ln in lines[i + 1:] if ln.strip()), "")
    try:
        val = json.loads(value)
        if isinstance(val, str):
            val = json.loads(val)
    except (json.JSONDecodeError, TypeError):
        val = None
    if not _is_result(val):
        raise SimulationError("the result marker's value is not a workflow result: " + value[:300], "no workflow result")
    return val


# Compiling the workflow to WASM is ~23 s of a ~28 s simulation, so it is built once and passed with --wasm. The
# binary lives in the Guardian's own checkout (exoguard-owned) and is rebuilt when any workflow source is newer.
WASM_SOURCES = ("main.ts", "src", "package.json", "bun.lock", "tsconfig.json")
BUILD_TIMEOUT_S = 240
_BUILD_LOCK = threading.Lock()


def _newest_source(exo: Path) -> float:
    newest = 0.0
    for name in WASM_SOURCES:
        p = exo / name
        for f in ([p] if p.is_file() else p.rglob("*") if p.is_dir() else []):
            if f.is_file() and "node_modules" not in f.parts:
                newest = max(newest, f.stat().st_mtime)
    return newest


def ensure_wasm(cwd: Path = CRE_DIR, run=subprocess.run) -> Path | None:
    """The prebuilt workflow binary, built now if missing or stale; None (simulate compiles it) when there is no
    workflow source here or the build fails. Never raises: a failed build only costs speed."""
    exo = Path(cwd) / "exo"
    if not (exo / "main.ts").is_file():
        return None
    out = exo / "build" / "exo.wasm"
    with _BUILD_LOCK:
        if out.is_file() and out.stat().st_mtime >= _newest_source(exo):
            return out
        out.parent.mkdir(exist_ok=True)
        tmp = out.with_name("exo.tmp.wasm")   # cre appends .wasm to any -o name not ending in it
        try:
            p = run(["cre", "workflow", "build", "exo", "-o", str(tmp)], cwd=str(cwd), capture_output=True,
                    text=True, timeout=BUILD_TIMEOUT_S)
            if p.returncode != 0 or not tmp.is_file():
                log.warning("cre workflow build failed (exit %s); simulate will compile", p.returncode)
                return None
            os.replace(tmp, out)
            return out
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("cre workflow build failed (%s); simulate will compile", type(exc).__name__)
            return None
        finally:
            tmp.unlink(missing_ok=True)


def run_simulation(payload: dict, trigger_index: int, broadcast: bool, run=subprocess.run,
                   cwd: Path = CRE_DIR) -> tuple[dict, int]:
    """(result, latency ms). Without `broadcast`, report writes are dry runs (report_tx is the zero hash)."""
    wasm = ensure_wasm(cwd, run)
    fd, path = tempfile.mkstemp(prefix="exo-payload-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        cmd = ["cre", "workflow", "simulate", "exo", "--target", "mainnet", "--non-interactive",
               "--trigger-index", str(trigger_index), "--http-payload", path]
        if wasm is not None:
            # relative to the workflow folder (cre resolves it there), and short: cre refuses one over 97 characters
            cmd += ["--wasm", os.path.relpath(wasm, Path(cwd) / "exo")]
        if broadcast:
            cmd.append("--broadcast")
        t0 = time.monotonic()
        try:
            p = run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise SimulationError(f"simulation timed out after {TIMEOUT_S}s", "simulator timed out") from exc
        except OSError as exc:
            raise SimulationError(f"could not run cre: {exc}", "simulator not runnable") from exc
        ms = int((time.monotonic() - t0) * 1000)
    finally:
        Path(path).unlink(missing_ok=True)
    if p.returncode != 0:
        log.debug("cre exited %s: %s", p.returncode, (p.stderr or p.stdout or "").strip()[-300:])
        raise SimulationError((p.stderr or p.stdout or "").strip()[-300:], f"simulator exited {p.returncode}")
    return parse_result(p.stdout), ms
