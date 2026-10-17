"""runtime: hermes bots (a Hermes profile on the local model) and the local model gate."""
import json
from datetime import datetime

from cc_dispatcher import org as orgmod
from cc_dispatcher.config import load
from cc_dispatcher.local_gate import LocalGate
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


class Gate:
    def __init__(self, up=True, profiles=("argo-cmo",)):
        self.up, self.profiles = up, profiles


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


def test_a_hermes_bot_waits_while_its_model_host_is_down(tmp_path):
    d, runner, ob, spawned, clock = make(tmp_path, wakeups=[{"id": 1, "agent_name": "argo-cmo", "reason": "dm", "ref": "77"}])
    hspawned = with_hermes_spawn(runner)
    runner.gate = Gate(up=False)
    d.tick()
    assert hspawned == [] and ob.open_wakeups()  # still open
    runner.gate.up = True
    clock["t"] += 60
    d.tick()
    assert len(hspawned) == 1


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
    h["log"].write_text(json.dumps({"type": "cc_dispatch", "run_id": h["run_id"]}) + "\nPosted the draft to media.\n\nsession_id: 20261017_120000_abc\n")
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


# -- the gate ------------------------------------------------------------------------------------------------------
class GateBoard(FakeBoard):
    def __init__(self, ready=(), blocked=(), events=None):
        super().__init__(ready=ready)
        self.blocked = list(blocked)
        self.events = events or {}
        self.scheduled, self.unblocked = [], []

    def tasks(self, assignee, status):
        rows = {"ready": self.ready, "blocked": self.blocked}.get(status, [])
        return [t for t in rows if t.get("assignee") == assignee]

    def schedule(self, task_id, reason):
        self.scheduled.append(task_id)
        self.ready = [t for t in self.ready if t["id"] != task_id]

    def unblock(self, task_id, reason):
        self.unblocked.append(task_id)

    def show(self, task_id):
        return {"task": {"id": task_id}, "events": self.events.get(task_id, [])}


def gate_with(tmp_path, board, results):
    it = iter(results)
    return LocalGate("http://host:11434/api/version", ("m1-writer", "lena"), {"exo": board}, tmp_path / "gate.json",
                     probe=lambda url: next(it))


def test_one_failed_probe_is_a_blip(tmp_path):
    board = GateBoard(ready=[{"id": "t1", "assignee": "m1-writer"}])
    g = gate_with(tmp_path, board, [False, True])
    g.tick(100)
    assert g.up and board.scheduled == []
    g.tick(130)
    assert g.up and board.scheduled == [] and board.unblocked == []


def test_down_parks_ready_cards_and_back_releases_them_and_the_ones_hermes_gave_up_on(tmp_path):
    board = GateBoard(ready=[{"id": "t1", "assignee": "m1-writer"}, {"id": "t2", "assignee": "cc-coder"}],
                      blocked=[{"id": "t3", "assignee": "lena"}, {"id": "t4", "assignee": "lena"}],
                      events={"t3": [{"kind": "claimed", "created_at": 90}, {"kind": "gave_up", "created_at": 110}],
                              "t4": [{"kind": "blocked", "created_at": 120}]})
    g = gate_with(tmp_path, board, [False, False, True])
    g.tick(100)
    g.tick(130)
    assert not g.up and board.scheduled == ["t1"]          # only local profiles' cards
    assert json.loads((tmp_path / "gate.json").read_text())["parked"] == {"t1": "exo"}
    g.tick(400)
    assert g.up and set(board.unblocked) == {"t1", "t3"}   # t4 was blocked by a human reason, not the outage
    assert json.loads((tmp_path / "gate.json").read_text()) == {"down_since": None, "parked": {}}


def test_the_gate_survives_a_restart(tmp_path):
    board = GateBoard(ready=[{"id": "t1", "assignee": "lena"}])
    g = gate_with(tmp_path, board, [False, False])
    g.tick(100)
    g.tick(130)
    again = gate_with(tmp_path, board, [True])
    assert not again.up
    again.tick(200)
    assert again.up and board.unblocked == ["t1"]


def test_local_section_loads(tmp_path):
    vault = org_vault(tmp_path)
    path = lanes_with_org(tmp_path, vault)
    path.write_text(path.read_text() + '\n[local]\nhealth_url = "http://host:11434/api/version"\nprofiles = ["m1-writer", "argo-cmo"]\n')
    s = load(path)
    assert s.local.health_url.endswith("/api/version") and s.local.profiles == ("m1-writer", "argo-cmo")
    assert load(lanes_with_org(tmp_path, vault)).local is None
