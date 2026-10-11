"""lanes.toml -> Settings + Lane objects. Stdlib only (tomllib), so it runs on the droplet's system python."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LANE_NAME = re.compile(r"^cc-[a-z0-9-]{1,30}$")   # never a Hermes profile name: the cc- prefix is the contract
MODES = {"default", "acceptEdits", "plan", "auto", "dontAsk"}


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
    runs_dir = Path(d.get("runs_dir", "/home/hermes/cc-runs"))
    return Settings(
        boards=tuple(d.get("boards", ["exo"])), max_in_progress=int(d.get("max_in_progress", 2)),
        daily_cap=int(d.get("daily_cap", 20)), poll_seconds=int(d.get("poll_seconds", 30)),
        runs_dir=runs_dir, work_dir=Path(d.get("work_dir", "/home/hermes/cc-work")),
        pause_file=Path(d.get("pause_file", runs_dir / "PAUSE")),
        rate_limit_pause_minutes=int(d.get("rate_limit_pause_minutes", 60)),
        claude_bin=d.get("claude_bin", "claude"), hermes_bin=d.get("hermes_bin", "hermes"),
        lanes=lanes,
    )
