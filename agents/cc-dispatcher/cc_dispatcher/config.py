"""lanes.toml -> Settings + Lane objects. Stdlib only (tomllib), so it runs on the droplet's system python."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LANE_NAME = re.compile(r"^cc-[a-z0-9-]{1,30}$")   # never a Hermes profile name: the cc- prefix is the contract
VERIFIER_NAME = re.compile(r"^[a-z0-9-]{1,40}$")
MODES = {"default", "acceptEdits", "plan", "auto", "dontAsk"}


MAX_VERIFIER_SECONDS = 600


@dataclass(frozen=True)
class Verifier:
    """A check the dispatcher runs after a worker completes. Named here, never taken from a card: cards come from
    agents, the bridge and voice, and a command from a card body would run as hermes."""
    name: str
    cmd: tuple[str, ...]
    cwd: str                       # "worker" = the worker's cwd (its worktree for worktree lanes), else an absolute path
    timeout_seconds: int
    metric_regex: str | None


@dataclass(frozen=True)
class Lane:
    name: str
    description: str
    prompt: str                    # role prompt (<lane>.md) + the shared worker protocol
    cwd: str
    model: str
    max_runtime_minutes: int
    permission_mode: str
    allowed_tools: tuple[str, ...]
    denied_tools: tuple[str, ...]
    worktree: bool = False


@dataclass(frozen=True)
class Settings:
    boards: tuple[str, ...]
    max_in_progress: int
    daily_cap: int
    poll_seconds: int
    runs_dir: Path
    work_dir: Path
    pause_file: Path
    rate_limit_pause_minutes: int
    claude_bin: str
    hermes_bin: str
    lanes: dict[str, Lane] = field(default_factory=dict)
    verifiers: dict[str, Verifier] = field(default_factory=dict)
    max_attempts: int = 3


def load(path: str | Path) -> Settings:
    path = Path(path)
    raw = tomllib.loads(path.read_text())
    d, defaults = raw.get("dispatcher", {}), raw.get("defaults", {})
    protocol = (path.parent / "worker-protocol.md").read_text()
    lanes: dict[str, Lane] = {}
    for name, cfg in raw.get("lanes", {}).items():
        if not LANE_NAME.match(name):
            raise ValueError(f"lane {name!r}: names must match {LANE_NAME.pattern}")
        merged = {**defaults, **cfg}
        # Tool lists add to the defaults; denies can never be dropped by a lane.
        allowed = tuple(defaults.get("allowed_tools", [])) + tuple(cfg.get("allowed_tools", []))
        denied = tuple(defaults.get("denied_tools", [])) + tuple(cfg.get("denied_tools", []))
        mode = merged.get("permission_mode", "acceptEdits")
        if mode not in MODES:
            # bypassPermissions is deliberately not in MODES: nobody answers prompts, so denies are the only guard.
            raise ValueError(f"lane {name}: permission_mode {mode!r} not in {sorted(MODES)}")
        role = path.parent / f"{name}.md"
        if not role.exists():
            raise ValueError(f"lane {name}: missing role prompt {role.name}")
        lanes[name] = Lane(
            name=name, description=merged.get("description", ""),
            prompt=role.read_text().strip() + "\n\n" + protocol.strip() + "\n",
            cwd=merged.get("cwd", "."), model=merged.get("model", "sonnet"),
            max_runtime_minutes=int(merged.get("max_runtime_minutes", 45)),
            permission_mode=mode, allowed_tools=allowed, denied_tools=denied,
            worktree=bool(merged.get("worktree", False)),
        )
    if not lanes:
        raise ValueError("no lanes configured")
    verifiers: dict[str, Verifier] = {}
    for vname, vcfg in raw.get("verifiers", {}).items():
        if not VERIFIER_NAME.match(vname):
            raise ValueError(f"verifier {vname!r}: names must match {VERIFIER_NAME.pattern}")
        cmd = vcfg.get("cmd")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            raise ValueError(f"verifier {vname}: cmd must be a non-empty list of strings")
        cwd = str(vcfg.get("cwd", "worker"))
        if cwd != "worker" and not Path(cwd).is_absolute():
            raise ValueError(f"verifier {vname}: cwd must be 'worker' or an absolute path, got {cwd!r}")
        timeout_seconds = int(vcfg.get("timeout_seconds", 600))
        if timeout_seconds <= 0:
            raise ValueError(f"verifier {vname}: timeout_seconds must be > 0, got {timeout_seconds}")
        if timeout_seconds > MAX_VERIFIER_SECONDS:   # verifiers run inside the dispatcher loop; keep claims alive
            raise ValueError(f"verifier {vname}: timeout_seconds must be <= {MAX_VERIFIER_SECONDS}, got {timeout_seconds}")
        metric_regex = vcfg.get("metric_regex")
        if metric_regex is not None:
            try:
                re.compile(metric_regex)
            except re.error as e:
                raise ValueError(f"verifier {vname}: metric_regex is invalid: {e}")
        verifiers[vname] = Verifier(
            name=vname, cmd=tuple(cmd), cwd=cwd,
            timeout_seconds=timeout_seconds, metric_regex=metric_regex,
        )
    runs_dir = Path(d.get("runs_dir", "/home/hermes/cc-runs"))
    max_attempts = int(d.get("max_attempts", 3))
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    return Settings(
        boards=tuple(d.get("boards", ["exo"])), max_in_progress=int(d.get("max_in_progress", 2)),
        daily_cap=int(d.get("daily_cap", 20)), poll_seconds=int(d.get("poll_seconds", 30)),
        runs_dir=runs_dir, work_dir=Path(d.get("work_dir", "/home/hermes/cc-work")),
        pause_file=Path(d.get("pause_file", runs_dir / "PAUSE")),
        rate_limit_pause_minutes=int(d.get("rate_limit_pause_minutes", 60)),
        claude_bin=d.get("claude_bin", "claude"), hermes_bin=d.get("hermes_bin", "hermes"),
        lanes=lanes, verifiers=verifiers, max_attempts=max_attempts,
    )
