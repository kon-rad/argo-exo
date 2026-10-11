import subprocess
import sys
from pathlib import Path

from exo_deck import demo
from exo_deck.config import Settings
from exo_deck.kiosk.app import create_app

BIN = Path(__file__).resolve().parents[1] / "bin" / "exo-demo"
LOOP0 = 1_800_000_000.0 - (1_800_000_000.0 % demo.LOOP_S)    # a wall-clock time at the start of a loop


def test_flag_file_and_env(tmp_path):
    assert not demo.enabled(tmp_path, env={})
    demo.set_enabled(tmp_path, True)
    assert demo.enabled(tmp_path, env={})
    demo.set_enabled(tmp_path, False)
    assert not demo.enabled(tmp_path, env={})
    assert demo.enabled(tmp_path, env={"EXO_DEMO": "1"})


def test_scenario_starts_with_one_request_and_ends_with_everything_done():
    tasks, feed = demo.board(LOOP0 + 1)
    live = [t for t in tasks if t["id"].startswith("t-1")]
    assert [t["status"] for t in live] == ["triage"] and feed[0]["from"] == "you"
    tasks, feed = demo.board(LOOP0 + demo.LOOP_S - 1)
    assert all(t["status"] == "done" for t in tasks)
    assert feed[-1]["to"] == "you"


def test_delegation_children_point_at_the_parent_and_move_through_columns():
    tasks, _ = demo.board(LOOP0 + 30)
    kids = {t["id"]: t for t in tasks if t["parent"] == "t-101"}
    assert set(kids) == {"t-102", "t-103", "t-104", "t-105"}
    assert kids["t-104"]["status"] == "done" and kids["t-102"]["status"] == "running"


def test_scenario_is_ordered_and_moves_only_known_tasks():
    ats = [at for at, _, _ in demo.SCENARIO]
    assert ats == sorted(ats) and max(ats) < demo.LOOP_S
    created = set()
    for _, kind, p in demo.SCENARIO:
        if kind == "task":
            created.add(p[0])
        elif kind == "move":
            assert p[0] in created
            assert p[1] in demo.COLS


def test_agents_panel_shape_matches_the_live_panel():
    j = demo.agents_panel(LOOP0 + 40)
    assert j["online"] is True and set(j["cols"]) == {"queued", "ready", "running", "done"}
    assert all(len(c) <= 4 for c in j["cols"].values()) and len(j["feed"]) == 3
    assert j["feed"][0]["ts"] >= j["feed"][-1]["ts"]           # newest message first


def test_every_kiosk_panel_has_demo_data():
    from exo_deck.state import PANELS
    for name in PANELS:
        assert isinstance(demo.panel(name, LOOP0), dict), name


def client(tmp_path):
    s = Settings.from_env({"DECK_ROOT": str(tmp_path)})
    s.state.mkdir(parents=True)
    providers = {"talk": lambda page: {"turns": [{"role": "you", "text": "live", "ts": 0}]},
                 "agents": lambda page: {"online": False, "cols": {}, "counts": {}}}
    return create_app(s, providers, hermes_ok=lambda: False, wall=lambda: LOOP0 + 20).test_client(), s


def test_kiosk_switches_to_demo_data_live(tmp_path, monkeypatch):
    monkeypatch.delenv("EXO_DEMO", raising=False)
    c, s = client(tmp_path)
    assert c.get("/api/panel/talk").json["turns"][0]["text"] == "live"
    st = c.get("/api/state").json
    assert st["demo"] is False and st["hermes"] is False
    demo.set_enabled(s.state, True)
    assert c.get("/api/panel/talk").json["turns"][0]["text"] != "live"
    assert c.get("/api/panel/agents").json["online"] is True
    st = c.get("/api/state").json
    assert st["demo"] is True and st["hermes"] is True and st["pending"] == 2
    demo.set_enabled(s.state, False)
    assert c.get("/api/panel/talk").json["turns"][0]["text"] == "live"


def test_exo_demo_cli(tmp_path):
    run = lambda *a: subprocess.run([sys.executable, str(BIN), *a], capture_output=True, text=True,
                                    env={"DECK_ROOT": str(tmp_path)}).stdout.strip()
    assert run() == "demo=off"
    assert run("on") == "demo=on" and (tmp_path / "state" / "demo").exists()
    assert run("toggle") == "demo=off" and not (tmp_path / "state" / "demo").exists()
