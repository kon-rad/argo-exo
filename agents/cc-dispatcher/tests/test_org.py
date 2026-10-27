"""The agent company runner: wakeups from the board, board cards for agents, coalescing, caps, routines, sessions."""
import json
from dataclasses import replace
import os
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
        self.messages = {}            # ref -> {"thread_id", "task_id"}; an unknown ref is a new top-level message
        self.override_rows, self.override_reads, self.fail_overrides = {}, 0, False   # agent -> cli-org overrides row

    def overrides(self):
        self.override_reads += 1
        if self.fail_overrides:
            raise BoardError("cli-org overrides: db down")
        return self.override_rows

    def message(self, agent, ref):
        return self.messages.get(str(ref), {"thread_id": None, "task_id": None})

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

    def release(self):
        n = 0
        for w in self.wakeups:
            if w.get("claimed_at") and not w.get("done_at"):
                w["claimed_at"] = None
                n += 1
        self.released = n
        return n

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
        spawned.append({"agent": agent.name, "model": agent.model, "effort": agent.effort, "runtime": agent.runtime, "lane": lane.name if lane else None, "task": task, "prompt": prompt,
                        "session": session_id, "run_id": run_id, "cwd": cwd, "log": log_path, "proc": p})
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()            # the runner wrote the header already; a worker only appends
        return p
    # 2026-10-15 04:00 Asia/Tokyo, before the 05:30 routine so only the routine test fires it
    clock = {"t": t if t is not None else datetime(2026, 10, 15, 4, 0, tzinfo=TZ).timestamp()}
    board = board or FakeBoard()
    runner = OrgRunner(settings, {"exo": board}, ob, spawn=spawn, prepare_cwd=lambda s, l, t: f"/work/{t}", clock=lambda: clock["t"])
    d = Dispatcher(settings, {"exo": board}, spawn=lambda *a: FakeProc(), prepare_cwd=lambda s, l, t, base=None: l.cwd,
                   clock=lambda: clock["t"], org=runner)
    return d, runner, ob, spawned, clock


def log_result(path: Path, session="33333333-3333-4333-8333-333333333333", result="posted a status", assistant=True):
    lines = []
    if assistant:
        lines.append(json.dumps({"type": "assistant", "session_id": session, "message": {"content": [{"type": "text", "text": "hi"}]}}))
    lines.append(json.dumps({"type": "result", "subtype": "success", "session_id": session, "result": result}))
    path.write_text("\n".join(lines) + "\n")


# -- config -------------------------------------------------------------------------------------------------------
def test_org_section_loads_and_keeps_hermes_agents_apart(tmp_path):
    vault = org_vault(tmp_path)
    s = load(lanes_with_org(tmp_path, vault))
    assert s.org.cli_org == "/opt/cli-org" and s.org.allowed_tools == ("Read", "Bash(cli-org *)")
    assert s.org.denied_tools == ("Bash(sudo *)", "Read(CURRENT/**)")
    agents = orgmod.load_agents(s.org.agents_file)
    assert set(agents) == {"atlas", "rune", "vera", "argo-cmo"} and agents["atlas"].is_lead and agents["rune"].lane == "cc-test"
    assert agents["argo-cmo"].is_hermes and not agents["atlas"].is_hermes
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
    log_result(atlas["log"], session="22222222-2222-4222-8222-222222222222")
    atlas["proc"].code = 0
    d.tick()
    assert ob.finished[0] == ([1, 2], atlas["run_id"], 0, "posted a status")
    assert ob.session_sets == [("atlas", "22222222-2222-4222-8222-222222222222")]
    assert "atlas" not in runner.running


def test_every_wake_opens_its_log_with_a_header_the_ledger_can_read(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[
        {"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "7"},
        {"id": 2, "agent_name": "atlas", "reason": "mention", "ref": "9"},
    ])
    ob.sessions["atlas"] = {"session_id": "11111111-1111-4111-8111-111111111111", "session_started": datetime.now(TZ).date().isoformat()}
    ob.messages["9"] = {"thread_id": 3, "task_id": None}      # a reply in a thread: the session continues
    d.tick()
    atlas = spawned[0]
    head = json.loads(Path(atlas["log"]).read_text().splitlines()[0])
    assert head["type"] == "cc_dispatch" and head["run_id"] == atlas["run_id"] and head["agent"] == "atlas"
    assert head["team"] == "platform" and head["reasons"] == [{"reason": "dm", "ref": "7"}, {"reason": "mention", "ref": "9"}]
    assert head["wakeup_ids"] == [1, 2] and head["task_id"] is None and head["board"] is None
    assert head["resumed"] is True and head["model"] == "opus" and head["at"].startswith("2026-10-1")
    # A fresh session on a board card: resumed false, the card in the header, no wakeups.
    log_result(atlas["log"], session="11111111-1111-4111-8111-111111111111"); atlas["proc"].code = 0
    clock["t"] += 3600
    runner.boards["exo"].ready.append({"id": "t_c0ffee01", "assignee": "rune", "priority": 1})
    d.tick()
    rune = next(s for s in spawned if s["agent"] == "rune")
    head = json.loads(Path(rune["log"]).read_text().splitlines()[0])
    assert head["agent"] == "rune" and head["task_id"] == "t_c0ffee01" and head["board"] == "exo"
    assert head["reasons"] == [] and head["wakeup_ids"] == [] and head["resumed"] is False


def test_lanes_and_the_org_take_turns_going_first(tmp_path):
    # Two slots, two lane cards ready and two bots woken: the first tick serves the lanes, the second the org, so a
    # busy lane never holds both slots while bots wait (2026-10-15, two research cards vs four bots' cards).
    board = FakeBoard(ready=[{"id": "t_l1"}, {"id": "t_l2"}, {"id": "t_l3"}])
    d, runner, ob, spawned, clock = make(tmp_path, board=board, wakeups=[
        {"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "7"},
        {"id": 2, "agent_name": "rune", "reason": "dm", "ref": "8"},
    ])
    d.tick()
    assert sorted(d.running) == ["t_l1", "t_l2"] and spawned == []
    for r in d.running.values():
        r.proc.code = 0
    clock["t"] += 60
    d.tick()
    assert [s["agent"] for s in spawned] == ["atlas", "rune"] and "t_l3" not in d.running


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
    ob.messages["4"] = {"thread_id": 3, "task_id": None}      # a reply: resumes
    clock["t"] += 5 * 60
    d.tick()
    assert len(spawned) == 2
    clock["t"] += 6 * 60
    d.tick()
    assert len(spawned) == 3 and spawned[2]["agent"] == "atlas"
    assert spawned[2]["session"] == "33333333-3333-4333-8333-333333333333"       # resumed from the stored session


def test_global_slots_are_shared_with_the_lanes(tmp_path):
    board = FakeBoard(ready=[{"id": "t_lane", "assignee": "cc-test"}])
    d, runner, ob, spawned, clock = make(tmp_path, board=board, wakeups=[
        {"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "1"}, {"id": 2, "agent_name": "rune", "reason": "dm", "ref": "2"}])
    d.tick()
    assert len(d.running) == 1 and [s["agent"] for s in spawned] == ["atlas"]   # max_in_progress = 2


def test_a_board_card_assigned_to_an_agent_runs_as_that_agent_in_its_lane(tmp_path):
    board = FakeBoard(ready=[{"id": "t_org1", "assignee": "rune", "priority": 1}], status="running")
    d, runner, ob, spawned, clock = make(tmp_path, board=board)
    d.tick()
    assert len(spawned) == 1
    s = spawned[0]
    assert s["agent"] == "rune" and s["task"] == ("exo", "t_org1") and s["lane"] == "cc-test" and s["cwd"] == "/work/t_org1"
    assert board.claims == [("t_org1", 30 * 60 + 900, "rune")]
    assert "board card t_org1 on board exo" in s["prompt"] and "cli-board finish t_org1" in s["prompt"]
    assert any("rune (sonnet) started" in c[1] for c in board.comments)
    s["proc"].code = 1
    log_result(s["log"], result="Error: gave up", assistant=True)
    d.tick()
    assert board.attached == [("t_org1", str(s["log"]))]
    assert board.blocks and "rune exited (code 1) without complete or block" in board.blocks[0][1]
    assert ob.finished == []                           # no board wakeups were involved
    assert ob.session_sets == [("rune", "33333333-3333-4333-8333-333333333333")]


def test_resume_failure_clears_the_session(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "1"}])
    ob.sessions["atlas"] = {"session_id": "44444444-4444-4444-8444-444444444444", "session_started": datetime.now(TZ).date().isoformat()}
    ob.messages["1"] = {"thread_id": 1, "task_id": None}
    d.tick()
    assert spawned[0]["session"] == "44444444-4444-4444-8444-444444444444"
    spawned[0]["proc"].code = 1
    spawned[0]["log"].write_text(json.dumps({"type": "result", "subtype": "error", "error": "No conversation found with session ID old"}) + "\n")
    d.tick()
    assert ob.session_sets == [("atlas", None)]
    assert ob.finished[0][2] == 1 and "No conversation found" in ob.finished[0][3]


def _saved(ob, agent="atlas"):
    ob.sessions[agent] = {"session_id": "44444444-4444-4444-8444-444444444444", "session_started": datetime.now(TZ).date().isoformat()}


def test_a_new_top_level_message_starts_fresh(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "7"}])
    _saved(ob)
    d.tick()
    assert spawned[0]["session"] is None


def test_a_reply_in_a_thread_resumes(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "mention", "ref": "8"}])
    _saved(ob)
    ob.messages["8"] = {"thread_id": 5, "task_id": None}
    d.tick()
    assert spawned[0]["session"] == "44444444-4444-4444-8444-444444444444"


def test_a_routine_starts_fresh(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "routine", "ref": "weekly-reflection"}])
    _saved(ob)
    d.tick()
    assert spawned[0]["session"] is None


def test_the_owners_ship_answer_resumes(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "owner", "ref": "ship:3"}])
    _saved(ob)
    d.tick()
    assert spawned[0]["session"] == "44444444-4444-4444-8444-444444444444"


def test_a_ticket_resumes_only_after_the_session_worked_it(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "task", "ref": "t_new"}])
    _saved(ob)
    d.tick()
    assert spawned[0]["session"] is None                 # a new task: fresh
    spawned[0]["log"].write_text(json.dumps({"type": "result", "session_id": "55555555-5555-4555-8555-555555555555", "result": "done"}) + "\n")
    spawned[0]["proc"].code = 0
    d.tick()
    assert d.org.state.session_tasks["atlas"] == ["t_new"]
    clock["t"] += 3600
    ob.wakeups.append({"id": 9, "agent_name": "atlas", "reason": "task", "ref": "t_new"})
    d.tick()
    assert spawned[1]["session"] == "55555555-5555-4555-8555-555555555555"                 # the same ticket again: resume


def test_a_session_id_the_cli_cannot_resume_is_dropped(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "owner", "ref": "ship:3"}])
    ob.sessions["atlas"] = {"session_id": "57322bd09651", "session_started": datetime.now(TZ).date().isoformat()}
    d.tick()
    assert spawned[0]["session"] is None and ob.session_sets == [("atlas", None)]


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
    assert "Bash(cli-board finish *)" in allowed and not any("hermes kanban" in t for t in allowed)
    assert cmd[cmd.index("--disallowedTools") + 1] == "Bash(sudo *),Read(CURRENT/**)"
    assert cmd[cmd.index("--max-turns") + 1] == "60" and "--permission-prompts" in cmd
    no_task = org_command(settings, agents["atlas"], None, None, "P", None)
    assert "--resume" not in no_task and "PROTOCOL" not in no_task[no_task.index("--append-system-prompt") + 1]
    env = org_env({"HOME": "/home/hermes", "ANTHROPIC_API_KEY": "nope", "PATH": "/x"}, settings, agents["rune"], "org-rune-1", ("exo", "t_1"))
    assert env["ORG_AGENT"] == "rune" and env["ORG_RUN"] == "org-rune-1" and "HERMES_KANBAN_TASK" not in env
    assert "ANTHROPIC_API_KEY" not in env and env["PATH"].startswith("/opt/claude:/opt/hermes:")


def test_prompt_wording():
    a = orgmod.OrgAgent("atlas", "Head", "platform", "Platform", "opus", None, True, "x", (), 6, 10, 30, ())
    text = wake_prompt(a, [{"reason": "konrad", "ref": "5"}, {"reason": "routine", "ref": "weekly-reflection"}], None)
    assert text.startswith("You are atlas, Head on the Platform team.")
    assert "a message from konrad (message 5" in text and "weekly-reflection" in text and "kanban" not in text
    owner = wake_prompt(a, [{"reason": "owner", "ref": "6"}], None)
    assert "a message from konrad (message 6" in owner
    assert describe_wakeups([{"reason": "task", "ref": "t_9"}]) == ["board card t_9"]


def test_a_routine_whose_wake_add_fails_is_retried_next_tick(tmp_path, caplog):
    t = datetime(2026, 10, 15, 5, 31, tzinfo=TZ).timestamp()
    d, runner, ob, spawned, clock = make(tmp_path, t=t)
    ob.fail_claim = True

    def failing_add(agent, reason, ref):
        raise BoardError("env broken")
    ob.add = failing_add
    d.tick()
    assert "retrying next tick" in caplog.text and "atlas/daily-standup" not in runner.state.routines
    ob.add = FakeOrgBoard.add.__get__(ob)
    ob.fail_claim = False
    d.tick()
    assert runner.state.routines["atlas/daily-standup"] == "2026-10-15" and ob.added == [("atlas", "routine", "daily-standup")]


def test_repeated_timeout_on_a_task_escalates_instead_of_looping_forever(tmp_path):
    """tau/t_cf79b6dc, 2026-10-24: a card that keeps hitting the runtime cap every time it is re-woken is not a flaky
    ('transient') failure that should keep auto-retrying; after a few runs in a row it must stop and ask Konrad."""
    board = FakeBoard(ready=[{"id": "t_org1", "assignee": "rune", "priority": 1}], status="running")
    d, runner, ob, spawned, clock = make(tmp_path, board=board)
    d.tick()
    assert len(spawned) == 1
    clock["t"] += 30 * 60 + 1
    d.tick()                                            # interrupted
    clock["t"] += 31
    d.tick()                                             # killed
    spawned[0]["proc"].code = -15
    d.tick()                                             # reaped: first timeout on this card
    assert board.blocks[-1][2] == "transient"
    assert runner.state.task_timeouts["t_org1"] == 1

    board.ready = [{"id": "t_org1", "assignee": "rune", "priority": 1}]   # re-queued, re-woken
    board.claimable = True
    d.tick()
    assert len(spawned) == 2
    clock["t"] += 30 * 60 + 1
    d.tick()
    clock["t"] += 31
    d.tick()
    spawned[1]["proc"].code = -15
    d.tick()                                             # reaped: second timeout in a row on the same card
    assert board.blocks[-1][2] == "needs_input"
    assert "2 times in a row" in board.blocks[-1][1]
    assert "t_org1" not in runner.state.task_timeouts    # a fresh run later starts the streak over, not mid-count


def test_timeout_streak_resets_once_the_task_moves_on(tmp_path):
    board = FakeBoard(ready=[{"id": "t_org1", "assignee": "rune", "priority": 1}], status="running")
    d, runner, ob, spawned, clock = make(tmp_path, board=board)
    d.tick()
    clock["t"] += 30 * 60 + 1
    d.tick()
    clock["t"] += 31
    d.tick()
    spawned[0]["proc"].code = -15
    d.tick()
    assert runner.state.task_timeouts["t_org1"] == 1
    board.final = "blocked"                             # the next run finished (blocked itself) instead of timing out
    board.ready = [{"id": "t_org1", "assignee": "rune", "priority": 1}]
    board.claimable = True
    d.tick()
    spawned[1]["proc"].code = 0
    log_result(spawned[1]["log"])
    d.tick()
    assert "t_org1" not in runner.state.task_timeouts


def test_roster_runtime_change_is_picked_up_without_a_restart(tmp_path):
    """rune, 2026-10-24: flipping an agent's runtime on disk (claude-code -> zen-agent) must stop cc-dispatcher from
    spawning it on the next tick, not just on the next process restart (self.agents was loaded once at startup)."""
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "rune", "reason": "dm", "ref": "1"}])
    agents_path = tmp_path / "vault" / ".agents" / "org" / "org-agents.json"
    d.tick()
    assert [s["agent"] for s in spawned] == ["rune"]
    spawned[0]["proc"].code = 0
    log_result(spawned[0]["log"])
    d.tick()

    raw = json.loads(agents_path.read_text())
    raw["agents"]["rune"]["runtime"] = "zen-agent"
    agents_path.write_text(json.dumps(raw))
    os.utime(agents_path, (clock["t"] + 10, clock["t"] + 10))
    ob.add("rune", "dm", "2")
    clock["t"] += 20
    d.tick()
    assert "rune" not in runner.agents
    assert [s["agent"] for s in spawned] == ["rune"]     # no second spawn attempt


def test_recover_releases_wakeups_claimed_by_a_dead_dispatcher(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "1", "claimed_at": "earlier"}])
    d.tick()
    assert spawned == []                 # claimed: not ours to start
    assert d.recover() == [] and ob.released == 1
    d.tick()
    assert [s["agent"] for s in spawned] == ["atlas"]



# -- bot powers (2026-10-19) ----------------------------------------------------------------------------------------
class ListingBoard(FakeBoard):
    """A board that answers one `list --status ready` for everyone, like the real one."""

    def __init__(self, ready):
        super().__init__(ready=ready)
        self.listings = 0

    def ready_tasks(self):
        self.listings += 1
        return list(self.ready)


def test_one_listing_per_board_per_tick_filtered_by_assignee(tmp_path):
    board = ListingBoard([{"id": "t_a", "assignee": "rune"}, {"id": "t_b", "assignee": "cc-coder"}, {"id": "t_c", "assignee": "argo-cmo"}])
    d, runner, ob, spawned, clock = make(tmp_path, board=board)
    runner.tick(clock["t"], lambda: True)
    assert board.listings == 1
    assert [(s["agent"], s["task"]) for s in spawned] == [("rune", ("exo", "t_a"))]   # lanes and Hermes profiles are not the org's


def test_code_bots_get_the_worktree_dir_and_a_lean_prompt(tmp_path):
    vault = org_vault(tmp_path)
    lanes = lanes_with_org(tmp_path, vault)
    lanes.write_text(lanes.read_text() + 'bots_work_dir = "/home/hermes/cc-work/bots"\n')
    settings = load(lanes)
    raw = json.loads((vault / ".agents" / "org" / "org-agents.json").read_text())
    raw["agents"]["rune"]["capabilities"] = ["code"]
    (vault / ".agents" / "org" / "org-agents.json").write_text(json.dumps(raw))
    agents = orgmod.load_agents(settings.org.agents_file)
    assert agents["rune"].capabilities == ("code",) and agents["atlas"].capabilities == ()
    cmd = org_command(settings, agents["rune"], None, ("exo", "t_1"), "P", None)
    assert cmd[cmd.index("--add-dir") + 1] == "/home/hermes/cc-work/bots"
    assert "--add-dir" not in org_command(settings, agents["atlas"], None, None, "P", None)
    text = wake_prompt(agents["rune"], [{"reason": "dm", "ref": "7"}], ("exo", "t_1"))
    assert "cli-org thread <id>" in text and "`cli-org read` to see everything" not in text and "cli-org recall" in text


def test_the_owners_ship_answer_is_described_as_one():
    lines = describe_wakeups([{"reason": "owner", "ref": "ship:12"}])
    assert lines == ["the owner answered ship request #12 (`cli-org ship list` shows it; approved: merge or deploy now, "
                     "denied: the note says what to fix)"]


def test_ready_cards_with_iso_created_at_sort_without_crashing(tmp_path):
    from cc_dispatcher.board import created_ts
    assert created_ts({"created_at": 1793000000}) == 1793000000.0
    assert created_ts({"created_at": "1793000000"}) == 1793000000.0
    assert created_ts({"created_at": "2026-10-26T09:46:12.252Z"}) > 1.7e9
    assert created_ts({}) == 0.0 and created_ts({"created_at": "junk"}) == 0.0


# -- owner overrides (cutover plan 5a, card t_edade866) -----------------------------------------------------------
DM = {"id": 1, "agent_name": "atlas", "reason": "dm", "ref": "7"}


def test_an_override_changes_the_model_and_effort_of_the_next_wake_with_no_reload(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[DM])
    ob.override_rows = {"atlas": {"agent": "atlas", "runtime": None, "model": "haiku", "effort": "low"}}
    d.tick()
    assert (spawned[0]["model"], spawned[0]["effort"], spawned[0]["runtime"]) == ("haiku", "low", "claude-code")
    cmd = org_command(runner.settings, replace(runner.agents["atlas"], model="haiku", effort="low"), None, None, "P", None)
    assert cmd[cmd.index("--model") + 1] == "haiku" and cmd[cmd.index("--effort") + 1] == "low"


def test_no_override_leaves_the_roster_model_and_passes_no_effort_flag(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[DM])
    d.tick()
    assert (spawned[0]["model"], spawned[0]["effort"]) == (runner.agents["atlas"].model, None)
    assert "--effort" not in org_command(runner.settings, runner.agents["atlas"], None, None, "P", None)


def test_overrides_are_read_once_per_ttl_and_a_failed_read_keeps_the_last_good_copy(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[DM, {"id": 2, "agent_name": "rune", "reason": "handoff", "ref": "11"}])
    ob.override_rows = {"atlas": {"agent": "atlas", "model": "haiku"}}
    d.tick()
    assert ob.override_reads == 1 and [s["model"] for s in spawned][0] == "haiku"      # atlas and rune share one read
    log_result(spawned[0]["log"])
    spawned[0]["proc"].code = 0
    clock["t"] += 3600
    ob.fail_overrides = True
    ob.wakeups.append({"id": 3, "agent_name": "atlas", "reason": "dm", "ref": "12"})
    d.tick()
    assert ob.override_reads == 2     # tried again after the TTL and failed; the tick did not crash
    assert [s["model"] for s in spawned if s["agent"] == "atlas"] == ["haiku", "haiku"]      # the last good copy applied


def test_an_override_onto_another_lanes_runtime_is_not_spawned_here(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[DM])
    ob.override_rows = {"atlas": {"agent": "atlas", "runtime": "zen-agent", "model": "openrouter/x/y"}}
    d.tick()
    assert spawned == [] and ob.claims == []        # left unclaimed for the zen-agent lane
