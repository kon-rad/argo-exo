"""runtime: hermes bots (a Hermes profile on the local model)."""
import json
from datetime import datetime

from cc_dispatcher import org as orgmod
from cc_dispatcher.config import load
from cc_dispatcher.org import hermes_command, hermes_result_of

from test_cc_dispatcher import FakeBoard, FakeProc
from test_org import TZ, lanes_with_org, make, org_vault


def with_hermes_spawn(runner):
    spawned = []

    def spawn_hermes(settings, agent, prompt, session_id, run_id, cwd, log_path):
        p = FakeProc()
        spawned.append({"agent": agent.name, "prompt": prompt, "session": session_id, "run_id": run_id, "log": log_path, "proc": p})
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()
        return p
    runner.spawn_hermes = spawn_hermes
    return spawned


def test_a_hermes_bot_wakes_through_hermes_and_its_cards_are_left_to_hermes(tmp_path):
    board = FakeBoard(ready=[{"id": "t_h1", "assignee": "argo-cmo"}])
    d, runner, ob, spawned, clock = make(tmp_path, board=board, wakeups=[{"id": 1, "agent_name": "argo-cmo", "reason": "dm", "ref": "77"}])
    hspawned = with_hermes_spawn(runner)
    d.tick()
    assert spawned == [] and len(hspawned) == 1
    h = hspawned[0]
    assert h["agent"] == "argo-cmo" and h["log"].suffix == ".log" and "message 77" in h["prompt"]
    assert board.claims == []                    # its card stays for Hermes's own dispatcher
    assert d.state.spawned_today == 0            # no Claude quota used


def test_a_hermes_bot_runs_past_the_daily_cap(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "argo-cmo", "reason": "dm", "ref": "77"}])
    hspawned = with_hermes_spawn(runner)
    d._roll_day(clock["t"])
    d.state.spawned_today = d.settings.daily_cap
    d.tick()
    assert len(hspawned) == 1 and d.state.spawned_today == d.settings.daily_cap


def test_a_finished_hermes_wake_stores_its_session_and_closes_the_wakeups(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "argo-cmo", "reason": "dm", "ref": "77"}])
    hspawned = with_hermes_spawn(runner)
    d.tick()
    h = hspawned[0]
    h["log"].write_text(json.dumps({"type": "cc_dispatch", "run_id": h["run_id"]}) + "\n  \u26a0 tirith security scanner enabled but not available\n"
                        "Posted the draft to media.\n\nsession_id: 20261017_120000_abc\n")
    h["proc"].code = 0
    clock["t"] += 30
    d.tick()
    assert ob.session_sets[-1] == ("argo-cmo", "20261017_120000_abc")
    ids, run_id, code, text = ob.finished[-1]
    assert ids == [1] and code == 0 and text == "Posted the draft to media."


def test_a_bot_that_changed_runtime_starts_a_fresh_session(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "argo-cmo", "reason": "dm", "ref": "77"}])
    hspawned = with_hermes_spawn(runner)
    ob.sessions["argo-cmo"] = {"session_id": "5b32ace6-2042-4784-bab0-2b5ced19fee1", "session_started": datetime.now(TZ).date().isoformat()}
    d.tick()
    assert hspawned[0]["session"] is None and ob.session_sets[-1] == ("argo-cmo", None)
    ob.sessions["argo-cmo"] = {"session_id": "20261017_120000_abc", "session_started": datetime.now(TZ).date().isoformat()}
    assert runner._session_to_resume(runner.agents["argo-cmo"]) == "20261017_120000_abc"


def test_hermes_command_sends_identity_once_per_session(tmp_path):
    vault = org_vault(tmp_path)
    s = load(lanes_with_org(tmp_path, vault))
    agent = orgmod.load_agents(s.org.agents_file)["argo-cmo"]
    (vault / "x").write_text("---\nname: argo-cmo\n---\nIDENTITY argo-cmo")
    fresh = hermes_command(s, agent, "WAKE", None)
    assert fresh[:4] == ["/opt/hermes/hermes", "-p", "argo-cmo", "chat"] and "-Q" in fresh and "--resume" not in fresh
    q = fresh[fresh.index("-q") + 1]
    assert q.startswith("IDENTITY argo-cmo") and "SKILL TEXT" in q and q.endswith("WAKE")
    resumed = hermes_command(s, agent, "WAKE", "sess-9")
    assert resumed[resumed.index("-q") + 1] == "WAKE" and resumed[-2:] == ["--resume", "sess-9"]


def test_hermes_result_with_no_reply(tmp_path):
    p = tmp_path / "x.log"
    p.write_text(json.dumps({"type": "cc_dispatch"}) + "\n")
    assert hermes_result_of(p) == (None, "", False)
