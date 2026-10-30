"""The engine's model router for Claude Code bot wakes: `cli-org route decide` before a card wake, `route outcome` after.

Engine reference: docs/org-routing.md ("cli-org route", "Dispatcher integration") and the zen-agent dispatcher's copy
of this integration in app/server/src/dispatch/index.ts; the semantics here mirror it. Vault design:
Areas/argo/second-brain/agent-company/org-config-and-model-routing-design.md.

- Only card wakes are routed. DMs, routines and other card-less wakes keep the bot's model.
- The envelope is the bot's own tier up to `max_tier` (unset: its own tier only). A bot whose model is not in the
  catalog is skipped with a log line.
- A router that times out, exits with an error or prints something that is not a decision never holds a wake up:
  the bot runs on its own model and the log says why.
- `claude` runs only Anthropic models. A `run` decision naming any other model (a provider/ollama id) is a skip.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Callable

from . import worker
from .board import _arg
from .config import TIERS, RoutingSettings

log = logging.getLogger("cc-dispatcher.routing")
NO_RUN = ("park", "ask-spec", "wait", "blocked")
CLAUDE_ALIASES = ("haiku", "sonnet", "opus")
CLAUDE_ID = re.compile(r"^claude-[a-z0-9.-]+$")


class RouterError(RuntimeError):
    pass


class Router:
    def __init__(self, settings: RoutingSettings, cli_org: str, run: Callable = subprocess.run):
        self.settings, self.bin, self.run = settings, cli_org, run

    @property
    def mode(self) -> str:
        return self.settings.mode

    def catalog(self) -> list[dict]:
        """The models in routing.json. Read on every routed wake (a small file) so a recompile needs no restart."""
        path = self.settings.config
        if path is None:
            raise RouterError("no routing.json configured")
        try:
            models = json.loads(Path(path).read_text()).get("models")
        except (OSError, ValueError, AttributeError) as exc:
            raise RouterError(f"routing config {path}: {exc}") from exc
        if not isinstance(models, list):
            raise RouterError(f"routing config {path}: no models list")
        return [m for m in models if isinstance(m, dict) and m.get("id")]

    @staticmethod
    def find(models: list[dict], model: str) -> dict | None:
        return next((m for m in models if m["id"] == model or model in (m.get("aliases") or [])), None)

    def envelope(self, own_tier: str, fixed: bool) -> list[str]:
        """Own tier up to max_tier. A fixed bot (merger, deployer) never climbs: its own tier only."""
        lo = TIERS.index(own_tier)
        hi = lo if fixed or not self.settings.max_tier else max(lo, TIERS.index(self.settings.max_tier))
        return list(TIERS[lo:hi + 1])

    def decide(self, agent_name: str, model: str, fixed: bool, card_id: str, work: str, text: str | None,
               run_id: str) -> dict | None:
        """The router's decision as a dict (`action`, plus `model`, `tier`, `turns` for a run, and `decision_id`),
        recorded in route_decisions. None when the wake is not routed; the reason is logged."""
        try:
            models = self.catalog()
        except RouterError as exc:
            log.warning("%s: route skipped, %s; running %s", agent_name, exc, model)
            return None
        own = self.find(models, model)
        if own is None or own.get("tier") not in TIERS:
            log.info("%s: route skipped, model %s is not in the catalog", agent_name, model)
            return None
        cmd = [self.bin, "route", "decide", "--work", work, "--tiers", ",".join(self.envelope(own["tier"], fixed)),
               "--card", card_id, "--config", str(self.settings.config),
               "--record", "--agent", agent_name, "--run", run_id, "--json"]
        if text:
            cmd += ["--text", _arg(text[:500])]   # cli-org reads any argument starting with -- as a flag
        try:
            p = self.run(cmd, capture_output=True, text=True, timeout=self.settings.timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("%s: router failed (%s); running %s", agent_name, exc, model)
            return None
        # decide exits 0 for `run` and 2 for any other decision; both print the decision.
        if p.returncode not in (0, 2):
            log.warning("%s: router failed (exit %s: %s); running %s", agent_name, p.returncode,
                        (p.stderr or p.stdout).strip()[-300:], model)
            return None
        try:
            d = json.loads(p.stdout)
        except ValueError:
            d = None
        ok = isinstance(d, dict) and (d.get("action") in NO_RUN or (d.get("action") == "run" and d.get("model")))
        if not ok:
            log.warning("%s: router failed (not a decision: %r); running %s", agent_name, p.stdout[:120], model)
            return None
        return d

    def outcome(self, decision_id: int, result: str, turns: int | None) -> None:
        cmd = [self.bin, "route", "outcome", str(decision_id), result, *(["--turns", str(turns)] if turns is not None else [])]
        try:
            p = self.run(cmd, capture_output=True, text=True, timeout=self.settings.timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("route outcome %s %s not recorded: %s", decision_id, result, exc)
            return
        if p.returncode != 0:
            log.warning("route outcome %s %s not recorded: %s", decision_id, result, (p.stderr or p.stdout).strip()[-300:])

    def claude_model(self, model: str) -> str | None:
        """What `claude --model` takes for a catalog id, or None when the claude CLI cannot run it."""
        try:
            entry = self.find(self.catalog(), model)
        except RouterError:
            entry = None
        if entry is not None and entry.get("provider") != "anthropic":
            return None
        if model in CLAUDE_ALIASES or CLAUDE_ID.match(model):
            return model
        return None


def run_outcome(log_path: Path, exit_code: int, timed_out: bool, left_running: bool) -> tuple[str, int | None]:
    """("done" | "failed", turns) for `cli-org route outcome`. A budget stop (max turns or max runtime), an error exit
    or a card left running is `failed`: the router counts it as a turn-cap miss and starts the card's next wake a
    tier up, as the engine does for zen-agent's `stopped: budget`."""
    subtype, is_error, turns = None, False, None
    for ev in worker._events(log_path):
        if ev.get("type") == "result":
            subtype, is_error = ev.get("subtype"), bool(ev.get("is_error"))
            turns = ev.get("num_turns") if isinstance(ev.get("num_turns"), int) else turns
    failed = timed_out or exit_code != 0 or left_running or is_error or str(subtype or "").startswith("error")
    return ("failed" if failed else "done"), turns
