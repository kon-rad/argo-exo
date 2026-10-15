"""The agent company runner: wakeups from the board, kanban tasks for agents, coalescing, caps, routines, sessions."""
import json
import signal
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cc_dispatcher import org as orgmod
from cc_dispatcher.board import BoardError
from cc_dispatcher.config import load
from cc_dispatcher.dispatcher import Dispatcher
from cc_dispatcher.org import OrgRunner, describe_wakeups, org_command, org_env, routine_due, wake_prompt

from test_cc_dispatcher import FakeBoard, FakeProc

TZ = ZoneInfo("Asia/Tokyo")


def org_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".claude" / "agents").mkdir(parents=True)
    (vault / ".agents" / "org").mkdir(parents=True)
    (vault / ".agents" / "skills" / "org-member").mkdir(parents=True)
    (vault / ".agents" / "skills" / "org-member" / "SKILL.md").write_text("---\nname: org-member\n---\nSKILL TEXT")
    for name, extra in (("atlas", {"is_lead": True, "model": "opus", "routines": [{"name": "daily-standup", "at": "05:30", "kind": "standup"}]}),
                        ("rune", {"lane": "cc-test"}), ("vera", {"wakes_per_day": 1, "cooldown_minutes": 0})):
        (vault / ".claude" / "agents" / f"{name}.md").write_text(f"---\nname: {name}\nmodel: sonnet\n---\n\nIDENTITY {name}")
    agents = {
        "version": "v1", "company": "Argo", "ceo": "konrad", "teams": {"platform": {"name": "Platform", "lead": "atlas"}},
        "agents": {
            "atlas": {"role": "Head", "team": "platform", "team_name": "Platform", "runtime": "claude-code", "model": "opus", "lane": None,
                      "is_lead": True, "prompt_file": ".claude/agents/atlas.md", "tools": ["Bash(cli-org *)"], "wakes_per_day": 6,
                      "cooldown_minutes": 10, "max_runtime_minutes": 30, "routines": [{"name": "daily-standup", "at": "05:30", "kind": "standup"}]},
            "rune": {"role": "Engineer", "team": "platform", "team_name": "Platform", "runtime": "claude-code", "model": "sonnet", "lane": "cc-test",
                     "is_lead": False, "prompt_file": ".claude/agents/rune.md", "tools": ["Bash(cli-org *)"], "wakes_per_day": 6,
                     "cooldown_minutes": 10, "max_runtime_minutes": 30, "routines": []},
            "vera": {"role": "Reviewer", "team": "platform", "team_name": "Platform", "runtime": "claude-code", "model": "sonnet", "lane": None,
                     "is_lead": False, "prompt_file": ".claude/agents/vera.md", "tools": [], "wakes_per_day": 1,
                     "cooldown_minutes": 0, "max_runtime_minutes": 5, "routines": []},
            "argo-cmo": {"role": "CMO", "team": "media", "team_name": "Media", "runtime": "hermes", "prompt_file": "x", "tools": []},
        },
    }
    (vault / ".agents" / "org" / "org-agents.json").write_text(json.dumps(agents))
    return vault


def lanes_with_org(tmp_path, vault, enabled="true"):
    d = tmp_path / "lanes"
    d.mkdir(exist_ok=True)
    (d / "worker-protocol.md").write_text("PROTOCOL")
    (d / "cc-test.md").write_text("ROLE")
    (d / "lanes.toml").write_text(f"""
[dispatcher]
boards = ["exo"]
max_in_progress = 2
daily_cap = 10
runs_dir = "{tmp_path / 'runs'}"
work_dir = "{tmp_path / 'work'}"
claude_bin = "/opt/claude/claude"
hermes_bin = "/opt/hermes/hermes"

[defaults]
cwd = "{vault}"
allowed_tools = ["Read"]
denied_tools = ["Bash(sudo *)"]

[lanes.cc-test]
description = "test lane"
worktree = true
allowed_tools = ["Bash(git commit *)"]

[org]
enabled = {enabled}
vault = "{vault}"
cli_org = "/opt/cli-org"
timezone = "Asia/Tokyo"
allowed_tools = ["Bash(cli-org *)"]
denied_tools = ["Read(CURRENT/**)"]
""")
    return d / "lanes.toml"


class FakeOrgBoard:
    def __init__(self, wakeups=()):
        self.wakeups = [dict(w) for w in wakeups]
        self.claims, self.finished, self.added, self.sessions, self.session_sets = [], [], [], {}, []
        self.fail_claim = False

    def open_wakeups(self):
        return [w for w in self.wakeups if not w.get("claimed_at")]

    def claim(self, agent, run_id):
        if self.fail_claim:
            raise BoardError("db down")
        mine = [w for w in self.wakeups if w["agent_name"] == agent and not w.get("claimed_at")]
        for w in mine:
            w["claimed_at"] = "now"
        self.claims.append((agent, run_id, [w["id"] for w in mine]))
        return [dict(w) for w in mine]

    def done(self, ids, run_id, exit_code, result):
        self.finished.append((list(ids), run_id, exit_code, result))
        for w in self.wakeups:
            if w["id"] in ids:
                w["done_at"] = "now"

    def add(self, agent, reason, ref):
        key = (agent, reason, ref)
        if any((w["agent_name"], w["reason"], w["ref"]) == key and not w.get("done_at") for w in self.wakeups):
            return None
        wid = len(self.wakeups) + 1
        self.wakeups.append({"id": wid, "agent_name": agent, "reason": reason, "ref": ref})
        self.added.append(key)
        return wid

    def session(self, agent):
        return self.sessions.get(agent, {"session_id": None, "session_started": None})

    def set_session(self, agent, session_id):
        self.session_sets.append((agent, session_id))
        self.sessions[agent] = {"session_id": session_id, "session_started": datetime.now(TZ).date().isoformat()}


def make(tmp_path, board=None, wakeups=(), t=None):
    vault = org_vault(tmp_path)
    settings = load(lanes_with_org(tmp_path, vault))
    assert settings.org is not None
    ob = FakeOrgBoard(wakeups)
    spawned = []

    def spawn(settings, agent, lane, task, prompt, session_id, run_id, cwd, log_path):
        p = FakeProc()
        spawned.append({"agent": agent.name, "lane": lane.name if lane else None, "task": task, "prompt": prompt,
                        "session": session_id, "run_id": run_id, "cwd": cwd, "log": log_path, "proc": p})
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return p
    # 2026-10-15 04:00 Asia/Tokyo, before the 05:30 routine so only the routine test fires it
    clock = {"t": t if t is not None else datetime(2026, 10, 15, 4, 0, tzinfo=TZ).timestamp()}
    board = board or FakeBoard()
    runner = OrgRunner(settings, {"exo": board}, ob, spawn=spawn, prepare_cwd=lambda s, l, t: f"/work/{t}", clock=lambda: clock["t"])
    d = Dispatcher(settings, {"exo": board}, spawn=lambda *a: FakeProc(), prepare_cwd=lambda s, l, t, base=None: l.cwd,
                   clock=lambda: clock["t"], org=runner)
    return d, runner, ob, spawned, clock


def log_result(path: Path, session="sess-1", result="posted a status", assistant=True):
    lines = []
    if assistant:
        lines.append(json.dumps({"type": "assistant", "session_id": session, "message": {"content": [{"type": "text", "text": "hi"}]}}))
    lines.append(json.dumps({"type": "result", "subtype": "success", "session_id": session, "result": result}))
    path.write_text("\n".join(lines) + "\n")


# -- config -------------------------------------------------------------------------------------------------------
def test_org_section_loads_and_hermes_agents_are_skipped(tmp_path):
    vault = org_vault(tmp_path)
    s = load(lanes_with_org(tmp_path, vault))
    assert s.org.cli_org == "/opt/cli-org" and s.org.allowed_tools == ("Read", "Bash(cli-org *)")
    assert s.org.denied_tools == ("Bash(sudo *)", "Read(CURRENT/**)")
    agents = orgmod.load_agents(s.org.agents_file)
    assert set(agents) == {"atlas", "rune", "vera"} and agents["atlas"].is_lead and agents["rune"].lane == "cc-test"
    assert load(lanes_with_org(tmp_path, vault, enabled="false")).org is None


def test_missing_org_files_disable_the_org_without_failing(tmp_path, caplog):
    vault = org_vault(tmp_path)
    (vault / ".agents" / "org" / "org-agents.json").unlink()
    s = load(lanes_with_org(tmp_path, vault))
    assert s.org is None and "[org] disabled" in caplog.text


def test_repo_lanes_file_has_the_org_section():
    raw = (Path(__file__).resolve().parents[2] / "cc-lanes" / "lanes.toml").read_text()
    assert "[org]" in raw and 'cli_org = "/home/hermes/.local/bin/cli-org"' in raw


# -- waking --------------------------------------------------------------------------------------------------------
def test_a_dm_wakes_the_agent_once_with_all_its_wakeups(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[
        {"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "7"},
        {"id": 2, "agent_name": "atlas", "reason": "mention", "ref": "9"},
        {"id": 3, "agent_name": "rune", "reason": "handoff", "ref": "11"},
    ])
    d.tick()
    assert [s["agent"] for s in spawned] == ["atlas", "rune"]          # leads first
    atlas = spawned[0]
    assert ob.claims[0] == ("atlas", atlas["run_id"], [1, 2])
    assert "a direct message (message 7" in atlas["prompt"] and "a mention (message 9" in atlas["prompt"]
    assert "cli-org post platform" in atlas["prompt"] and atlas["cwd"] == str(runner.org.vault)
    assert atlas["session"] is None and atlas["run_id"].startswith("org-atlas-")
    assert d.state.spawned_today == 2
    # Nothing new: no second run while they run, and none after they finish until a new wakeup.
    d.tick()
    assert len(spawned) == 2
    log_result(atlas["log"], session="sess-atlas")
    atlas["proc"].code = 0
    d.tick()
    assert ob.finished[0] == ([1, 2], atlas["run_id"], 0, "posted a status")
    assert ob.session_sets == [("atlas", "sess-atlas")]
    assert "atlas" not in runner.running


def test_cooldown_and_daily_cap_hold_an_agent_back(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "vera", "reason": "dm", "ref": "1"}])
    d.tick()
    assert [s["agent"] for s in spawned] == ["vera"]
    spawned[0]["proc"].code = 0
    log_result(spawned[0]["log"])
    ob.add("vera", "dm", "2")
    d.tick()
    assert len(spawned) == 1                       # wakes_per_day = 1
    # atlas: cooldown 10 min
    ob.add("atlas", "dm", "3")
    d.tick()
    assert [s["agent"] for s in spawned] == ["vera", "atlas"]
    spawned[1]["proc"].code = 0
    log_result(spawned[1]["log"])
    d.tick()
    ob.add("atlas", "dm", "4")
    clock["t"] += 5 * 60
    d.tick()
    assert len(spawned) == 2
    clock["t"] += 6 * 60
    d.tick()
    assert len(spawned) == 3 and spawned[2]["agent"] == "atlas"
    assert spawned[2]["session"] == "sess-1"       # resumed from the stored session


def test_global_slots_are_shared_with_the_lanes(tmp_path):
    board = FakeBoard(ready=[{"id": "t_lane", "assignee": "cc-test"}])
    d, runner, ob, spawned, clock = make(tmp_path, board=board, wakeups=[
        {"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "1"}, {"id": 2, "agent_name": "rune", "reason": "dm", "ref": "2"}])
    d.tick()
    assert len(d.running) == 1 and [s["agent"] for s in spawned] == ["atlas"]   # max_in_progress = 2


def test_a_kanban_task_assigned_to_an_agent_runs_as_that_agent_in_its_lane(tmp_path):
    board = FakeBoard(ready=[{"id": "t_org1", "assignee": "rune", "priority": 1}], status="running")
    d, runner, ob, spawned, clock = make(tmp_path, board=board)
    d.tick()
    assert len(spawned) == 1
    s = spawned[0]
    assert s["agent"] == "rune" and s["task"] == ("exo", "t_org1") and s["lane"] == "cc-test" and s["cwd"] == "/work/t_org1"
    assert board.claims == [("t_org1", 30 * 60 + 900)]
    assert "kanban task t_org1 on board exo" in s["prompt"] and "complete" in s["prompt"]
    assert any("rune (sonnet) started" in c[1] for c in board.comments)
    s["proc"].code = 1
    log_result(s["log"], result="Error: gave up", assistant=True)
    d.tick()
    assert board.attached == [("t_org1", str(s["log"]))]
    assert board.blocks and "rune exited (code 1) without complete or block" in board.blocks[0][1]
    assert ob.finished == []                           # no board wakeups were involved
    assert ob.session_sets == [("rune", "sess-1")]


def test_resume_failure_clears_the_session(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "1"}])
    ob.sessions["atlas"] = {"session_id": "old", "session_started": datetime.now(TZ).date().isoformat()}
    d.tick()
    assert spawned[0]["session"] == "old"
    spawned[0]["proc"].code = 1
    spawned[0]["log"].write_text(json.dumps({"type": "result", "subtype": "error", "error": "No conversation found with session ID old"}) + "\n")
    d.tick()
    assert ob.session_sets == [("atlas", None)]
    assert ob.finished[0][2] == 1 and "No conversation found" in ob.finished[0][3]


def test_old_sessions_are_not_resumed(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "1"}])
    ob.sessions["atlas"] = {"session_id": "ancient", "session_started": "2026-09-01"}
    d.tick()
    assert spawned[0]["session"] is None and ob.session_sets == [("atlas", None)]


def test_runtime_limit_interrupts_then_kills(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "vera", "reason": "dm", "ref": "1"}])
    d.tick()
    p = spawned[0]["proc"]
    clock["t"] += 5 * 60 + 1
    d.tick()
    assert p.signals == [signal.SIGINT]
    clock["t"] += 31
    d.tick()
    assert p.signals == [signal.SIGINT, signal.SIGTERM]
    p.code = -15
    d.tick()
    assert ob.finished[0][3].startswith("hit its max runtime (5 min)")


def test_routines_fire_once_a_day_in_tokyo_time(tmp_path):
    t = datetime(2026, 10, 15, 5, 0, tzinfo=TZ).timestamp()
    d, runner, ob, spawned, clock = make(tmp_path, t=t)
    d.tick()
    assert ob.added == []
    clock["t"] += 31 * 60                          # 05:31
    d.tick()
    assert ob.added == [("atlas", "routine", "daily-standup")]
    assert [s["agent"] for s in spawned] == ["atlas"] and "your routine `daily-standup`" in spawned[0]["prompt"]
    clock["t"] += 3600
    d.tick()
    assert ob.added == [("atlas", "routine", "daily-standup")]   # not twice
    clock["t"] += 24 * 3600
    spawned[0]["proc"].code = 0
    log_result(spawned[0]["log"])
    d.tick()
    assert len(ob.added) == 2
    assert runner.state.routines["atlas/daily-standup"] == "2026-10-16"


def test_routine_due():
    at = {"at": "05:30"}
    assert routine_due(at, datetime(2026, 10, 15, 5, 30, tzinfo=TZ))
    assert not routine_due(at, datetime(2026, 10, 15, 5, 29, tzinfo=TZ))
    sun = {"on": "sun 20:00"}
    assert routine_due(sun, datetime(2026, 10, 18, 20, 5, tzinfo=TZ))     # a Sunday
    assert not routine_due(sun, datetime(2026, 10, 17, 20, 5, tzinfo=TZ))
    assert not routine_due({"at": "soon"}, datetime(2026, 10, 15, tzinfo=TZ))


def test_board_failure_is_logged_not_fatal(tmp_path, caplog):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "1"}])
    ob.fail_claim = True
    d.tick()
    assert spawned == [] and "wake-claim failed" in caplog.text


# -- the claude command ---------------------------------------------------------------------------------------------
def test_command_and_env(tmp_path):
    vault = org_vault(tmp_path)
    settings = load(lanes_with_org(tmp_path, vault))
    agents = orgmod.load_agents(settings.org.agents_file)
    cmd = org_command(settings, agents["rune"], settings.lanes["cc-test"], ("exo", "t_1"), "PROMPT", "sess-9")
    assert cmd[:3] == ["/opt/claude/claude", "-p", "PROMPT"]
    assert cmd[cmd.index("--model") + 1] == "sonnet" and cmd[-2:] == ["--resume", "sess-9"]
    sysp = cmd[cmd.index("--append-system-prompt") + 1]
    assert sysp.startswith("IDENTITY rune") and "SKILL TEXT" in sysp and "PROTOCOL" in sysp and "name: rune" not in sysp
    allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
    assert "Read" in allowed and "Bash(cli-org *)" in allowed and "Bash(git commit *)" in allowed
    assert "Bash(/opt/hermes/hermes kanban --board exo complete *)" in allowed
    assert cmd[cmd.index("--disallowedTools") + 1] == "Bash(sudo *),Read(CURRENT/**)"
    assert cmd[cmd.index("--max-turns") + 1] == "60" and "--permission-prompts" in cmd
    no_task = org_command(settings, agents["atlas"], None, None, "P", None)
    assert "--resume" not in no_task and "PROTOCOL" not in no_task[no_task.index("--append-system-prompt") + 1]
    env = org_env({"HOME": "/home/hermes", "ANTHROPIC_API_KEY": "nope", "PATH": "/x"}, settings, agents["rune"], "org-rune-1", ("exo", "t_1"))
    assert env["ORG_AGENT"] == "rune" and env["ORG_RUN"] == "org-rune-1" and env["HERMES_KANBAN_TASK"] == "t_1"
    assert "ANTHROPIC_API_KEY" not in env and env["PATH"].startswith("/opt/claude:/opt/hermes:")


def test_prompt_wording():
    a = orgmod.OrgAgent("atlas", "Head", "platform", "Platform", "opus", None, True, "x", (), 6, 10, 30, ())
    text = wake_prompt(a, [{"reason": "konrad", "ref": "5"}, {"reason": "routine", "ref": "weekly-reflection"}], None)
    assert text.startswith("You are atlas, Head on the Platform team.")
    assert "a message from konrad (message 5" in text and "weekly-reflection" in text and "kanban" not in text
    assert describe_wakeups([{"reason": "task", "ref": "t_9"}]) == ["kanban task t_9"]
