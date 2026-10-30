"""[org.routing]: the engine's model router before a Claude Code bot's card wake. `cli-org route` is mocked."""
import json
import subprocess
from datetime import datetime

import pytest

from cc_dispatcher.config import load
from cc_dispatcher.dispatcher import Dispatcher
from cc_dispatcher.org import OrgRunner, org_command

from test_cc_dispatcher import FakeBoard, FakeProc
from test_org import TZ, FakeOrgBoard, lanes_with_org, log_result, org_vault

CATALOG = {"version": "v1", "models": [
    {"id": "haiku", "aliases": [], "provider": "anthropic", "tier": "t1", "can_code": True, "local": False},
    {"id": "sonnet", "aliases": [], "provider": "anthropic", "tier": "t2", "can_code": True, "local": False},
    {"id": "opus", "aliases": [], "provider": "anthropic", "tier": "t3", "can_code": True, "local": False},
    {"id": "ollama-m1max/qwen3.6:35b-a3b", "aliases": ["qwen3.6:35b-a3b"], "provider": "ollama-m1max", "tier": "t1",
     "can_code": False, "local": True},
]}
CARD = {"id": "t_r1", "assignee": "rune", "priority": 1, "title": "Fix the parser", "work_type": "coding"}


class FakeCliOrg:
    """`cli-org route decide|outcome` as subprocess.run: answers decide with `decision` (or raises / fails)."""

    def __init__(self, decision=None, exit_code=None, stdout=None, raises=None):
        self.decision, self.exit_code, self.stdout, self.raises = decision, exit_code, stdout, raises
        self.calls = []

    def __call__(self, cmd, capture_output=True, text=True, timeout=None):
        self.calls.append(list(cmd))
        assert timeout and capture_output and text
        if cmd[1:3] == ["route", "outcome"]:
            return subprocess.CompletedProcess(cmd, 0, f"decision {cmd[3]}: {cmd[4]}\n", "")
        if self.raises:
            raise self.raises
        out = self.stdout if self.stdout is not None else json.dumps({**self.decision, "decision_id": 7})
        code = self.exit_code if self.exit_code is not None else (0 if (self.decision or {}).get("action") == "run" else 2)
        return subprocess.CompletedProcess(cmd, code, out, "boom" if code not in (0, 2) else "")

    def decides(self):
        return [c for c in self.calls if c[1:3] == ["route", "decide"]]

    def outcomes(self):
        return [c[3:] for c in self.calls if c[1:3] == ["route", "outcome"]]


def run(model, tier, turns):
    return {"action": "run", "model": model, "tier": tier, "floor": "t2", "turns": turns, "tokens": None, "reason": f"cheapest healthy: {model}"}


def make(tmp_path, mode="off", cli=None, card=CARD, wakeups=(), routing_extra="", agent_extra=None):
    vault = org_vault(tmp_path)
    (vault / ".agents" / "org" / "routing.json").write_text(json.dumps(CATALOG))
    if agent_extra:
        f = vault / ".agents" / "org" / "org-agents.json"
        doc = json.loads(f.read_text())
        for name, extra in agent_extra.items():
            doc["agents"][name].update(extra)
        f.write_text(json.dumps(doc))
    lanes = lanes_with_org(tmp_path, vault)
    lanes.write_text(lanes.read_text() + f'\n[org.routing]\nmode = "{mode}"\n{routing_extra}\n')
    settings = load(lanes)
    ob = FakeOrgBoard(wakeups)
    spawned = []

    def spawn(settings, agent, lane, task, prompt, session_id, run_id, cwd, log_path):
        p = FakeProc()
        spawned.append({"agent": agent, "task": task, "log": log_path, "proc": p,
                        "cmd": org_command(settings, agent, lane, task, prompt, session_id)})
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()
        return p
    cli = cli or FakeCliOrg(run("sonnet", "t2", 30))
    clock = {"t": datetime(2026, 10, 15, 4, 0, tzinfo=TZ).timestamp()}
    board = FakeBoard(ready=[dict(card)] if card else [])
    runner = OrgRunner(settings, {"exo": board}, ob, spawn=spawn, prepare_cwd=lambda s, l, t: f"/work/{t}",
                       clock=lambda: clock["t"], route_run=cli)
    d = Dispatcher(settings, {"exo": board}, spawn=lambda *a: FakeProc(), prepare_cwd=lambda s, l, t, base=None: l.cwd,
                   clock=lambda: clock["t"], org=runner)
    return d, runner, ob, board, spawned, cli


def flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def finish(d, s, code=0, subtype="success", turns=12):
    s["proc"].code = code
    s["log"].write_text(json.dumps({"type": "assistant", "session_id": "33333333-3333-4333-8333-333333333333"}) + "\n" +
                        json.dumps({"type": "result", "subtype": subtype, "num_turns": turns, "is_error": subtype != "success",
                                    "session_id": "33333333-3333-4333-8333-333333333333", "result": "done"}) + "\n")
    d.tick()


# -- config -----------------------------------------------------------------------------------------------------------
def test_routing_is_off_by_default_and_validated(tmp_path):
    vault = org_vault(tmp_path)
    settings = load(lanes_with_org(tmp_path, vault))
    r = settings.org.routing
    assert (r.mode, r.max_tier, r.timeout_seconds) == ("off", None, 20)
    assert r.config == vault / ".agents/org/routing.json"
    lanes = lanes_with_org(tmp_path, vault)
    lanes.write_text(lanes.read_text() + '\n[org.routing]\nmode = "always"\n')
    with pytest.raises(ValueError, match="mode"):
        load(lanes)
    lanes = lanes_with_org(tmp_path, vault)
    lanes.write_text(lanes.read_text() + '\n[org.routing]\nmode = "on"\nmax_tier = "t9"\n')
    with pytest.raises(ValueError, match="max_tier"):
        load(lanes)


def test_repo_lanes_file_loads_with_routing_off():
    from pathlib import Path
    import tomllib
    raw = tomllib.loads((Path(__file__).resolve().parents[2] / "cc-lanes" / "lanes.toml").read_text())
    assert raw["org"]["routing"]["mode"] == "off"


# -- off ----------------------------------------------------------------------------------------------------------------
def test_off_never_asks_the_router(tmp_path):
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="off")
    d.tick()
    assert runner.router is None and cli.calls == []
    assert spawned[0]["agent"].model == "sonnet" and flag(spawned[0]["cmd"], "--max-turns") == "60"


# -- shadow ---------------------------------------------------------------------------------------------------------
def test_shadow_logs_the_decision_runs_unchanged_and_records_the_outcome(tmp_path, caplog):
    caplog.set_level("INFO")
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="shadow", cli=FakeCliOrg(run("haiku", "t1", 20)),
                                              routing_extra='max_tier = "t3"')
    d.tick()
    (decide,) = cli.decides()
    assert decide[:3] == ["/opt/cli-org", "route", "decide"]
    assert flag(decide, "--work") == "coding" and flag(decide, "--tiers") == "t2,t3" and flag(decide, "--card") == "t_r1"
    assert flag(decide, "--agent") == "rune" and flag(decide, "--run").startswith("org-rune-") and "--record" in decide
    assert flag(decide, "--text") == "Fix the parser" and flag(decide, "--config").endswith(".agents/org/routing.json")
    assert "--json" in decide
    s = spawned[0]
    assert s["agent"].model == "sonnet" and flag(s["cmd"], "--model") == "sonnet" and flag(s["cmd"], "--max-turns") == "60"
    assert "rune: route shadow run haiku (t1): cheapest healthy: haiku" in caplog.text
    assert cli.outcomes() == []
    finish(d, s)
    assert cli.outcomes() == [["7", "done", "--turns", "12"]]


def test_a_budget_stop_is_recorded_as_failed_so_the_next_wake_escalates(tmp_path):
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="shadow")
    d.tick()
    finish(d, spawned[0], code=0, subtype="error_max_turns", turns=60)
    assert cli.outcomes() == [["7", "failed", "--turns", "60"]]


def test_card_less_wakes_are_not_routed(tmp_path):
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="on", card=None,
                                              wakeups=[{"id": 1, "agent_name": "rune", "reason": "dm", "ref": "55"}])
    d.tick()
    assert len(spawned) == 1 and cli.calls == [] and spawned[0]["agent"].model == "sonnet"


# -- on ------------------------------------------------------------------------------------------------------------
def test_on_run_sets_the_model_and_the_turn_budget(tmp_path):
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="on", cli=FakeCliOrg(run("opus", "t3", 40)), routing_extra='max_tier = "t3"')
    d.tick()
    s = spawned[0]
    assert s["agent"].model == "opus" and flag(s["cmd"], "--model") == "opus" and flag(s["cmd"], "--max-turns") == "40"
    assert any("rune (opus) started" in c[1] for c in board.comments)
    finish(d, s)
    assert cli.outcomes() == [["7", "done", "--turns", "12"]]


@pytest.mark.parametrize("action", ["park", "ask-spec", "wait", "blocked"])
def test_on_a_no_run_decision_finishes_the_wake_and_starts_nothing(tmp_path, action):
    decision = {"action": action, "floor": "t3", "reason": "floor t3 is above the bot's envelope (t2); needs konrad"}
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="on", cli=FakeCliOrg(decision),
                                              wakeups=[{"id": 4, "agent_name": "rune", "reason": "task", "ref": "t_r1"}])
    d.tick()
    assert spawned == [] and runner.running == {}
    assert ob.finished and ob.finished[0][0] == [4] and ob.finished[0][2] == 0
    assert ob.finished[0][3].startswith(f"routed {action}: floor t3")
    (block,) = board.blocks
    assert block[0] == "t_r1" and f"routed {action}" in block[1]
    assert block[2] == ("transient" if action == "wait" else "needs_input")
    assert cli.outcomes() == []
    assert "rune" not in runner.state.wakes          # not a run: no cooldown, no daily wake spent


def test_shadow_runs_even_when_the_router_would_park(tmp_path):
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="shadow", cli=FakeCliOrg({"action": "park", "floor": "t3", "reason": "x"}))
    d.tick()
    assert len(spawned) == 1 and board.blocks == []


# -- failures and unsupported models -------------------------------------------------------------------------------
@pytest.mark.parametrize("cli", [
    FakeCliOrg(raises=subprocess.TimeoutExpired("cli-org", 20)),
    FakeCliOrg(raises=OSError("no such file")),
    FakeCliOrg(decision=run("opus", "t3", 40), exit_code=1),
    FakeCliOrg(stdout="not json"),
    FakeCliOrg(stdout=json.dumps({"action": "fly"})),
], ids=["timeout", "missing-binary", "exit-1", "bad-json", "unknown-action"])
def test_a_router_failure_never_blocks_the_wake(tmp_path, caplog, cli):
    d, runner, ob, board, spawned, _ = make(tmp_path, mode="on", cli=cli)
    d.tick()
    s = spawned[0]
    assert s["agent"].model == "sonnet" and flag(s["cmd"], "--max-turns") == "60"
    assert "router failed" in caplog.text and board.blocks == []
    finish(d, s)
    assert cli.outcomes() == []                     # no decision id, nothing to record


def test_a_missing_routing_json_is_a_skip(tmp_path, caplog):
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="on")
    runner.router.settings.config.unlink()
    d.tick()
    assert cli.calls == [] and spawned[0]["agent"].model == "sonnet" and "route skipped" in caplog.text


def test_a_model_claude_cannot_run_is_a_skip(tmp_path, caplog):
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="on", cli=FakeCliOrg(run("ollama-m1max/qwen3.6:35b-a3b", "t1", 20)))
    d.tick()
    s = spawned[0]
    assert s["agent"].model == "sonnet" and flag(s["cmd"], "--max-turns") == "60"
    assert "claude cannot run ollama-m1max/qwen3.6:35b-a3b; running sonnet" in caplog.text
    finish(d, s)
    assert cli.outcomes() == [["7", "done", "--turns", "12"]]


def test_a_bot_whose_model_is_not_in_the_catalog_is_skipped(tmp_path, caplog):
    caplog.set_level("INFO")
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="on", agent_extra={"rune": {"model": "gpt-9"}})
    d.tick()
    assert cli.calls == [] and spawned[0]["agent"].model == "gpt-9"
    assert "route skipped, model gpt-9 is not in the catalog" in caplog.text


# -- fixed bots ---------------------------------------------------------------------------------------------------------
def test_a_fixed_bot_keeps_its_model_and_never_climbs(tmp_path, caplog):
    caplog.set_level("INFO")
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="on", cli=FakeCliOrg(run("haiku", "t1", 20)),
                                              routing_extra='max_tier = "t3"', agent_extra={"rune": {"routing": "fixed"}})
    d.tick()
    assert flag(cli.decides()[0], "--tiers") == "t2"
    s = spawned[0]
    assert s["agent"].model == "sonnet" and flag(s["cmd"], "--max-turns") == "60"
    assert "rune: fixed bot, keeps sonnet" in caplog.text


def test_a_fixed_bot_still_honours_a_park(tmp_path):
    d, runner, ob, board, spawned, cli = make(tmp_path, mode="on", cli=FakeCliOrg({"action": "park", "floor": "t3", "reason": "deploy -> t3"}),
                                              agent_extra={"rune": {"routing": {"mode": "fixed"}}})
    d.tick()
    assert spawned == [] and board.blocks and "routed park: deploy -> t3" in board.blocks[0][1]
