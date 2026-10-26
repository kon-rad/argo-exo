import pytest
from exo_bridge.app import create_app
from exo_bridge.config import Config
from exo_bridge.board import BoardError
from exo_bridge.talk import TalkError

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeBoard:
    def __init__(self, fail=False):
        self.created, self.listed, self.fail = [], [], fail

    def create(self, title, body, assignee, idempotency_key):
        if self.fail:
            raise BoardError("boom")
        self.created.append((title, body, assignee))
        return {"id": "t_9", "title": title, "assignee": assignee, "status": "ready"}

    def list(self, assignees):
        self.listed.append(assignees)
        return [{"id": "t_9", "title": "x", "assignee": "builder", "status": "running",
                 "created_at": 1, "completed_at": None, "result": None, "body": "secret-long-body"}]


class FakeWatcher:
    def __init__(self):
        self.watched = []

    def watch(self, card_id):
        self.watched.append(card_id)


class FakeTalker:
    def __init__(self, fail=False):
        self.fail, self.asked = fail, []

    def ask(self, text, session):
        if self.fail:
            raise TalkError("hermes down")
        self.asked.append((text, session))
        return "You hold 0.4 ETH."


def client(board=None, talker=None, watcher=None, **extra):
    env = {"EXO_BRIDGE_TOKEN": TOKEN, "EXO_BRIDGE_HOST": "100.64.0.5",  # public-ok
           "API_SERVER_KEY": "k", **extra}
    app = create_app(Config.from_env(env), board or FakeBoard(), talker or FakeTalker(), watcher=watcher)
    return app.test_client()


def test_health_needs_no_token():
    assert client().get("/health").json == {"ok": True}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_routes_need_the_token(headers):
    assert client().get("/board", headers=headers).status_code == 401


def test_talk():
    t = FakeTalker()
    r = client(talker=t).post("/talk", json={"text": "what's my portfolio", "session": "exo-deck-2026-10-07"}, headers=AUTH)
    assert r.status_code == 200 and r.json == {"reply": "You hold 0.4 ETH."}
    assert t.asked == [("what's my portfolio", "exo-deck-2026-10-07")]


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "x" * 2001}, {"text": 5}])
def test_talk_rejects_bad_input(body):
    assert client().post("/talk", json=body, headers=AUTH).status_code == 400


def test_talk_upstream_failure_is_502():
    r = client(talker=FakeTalker(fail=True)).post("/talk", json={"text": "hi"}, headers=AUTH)
    assert r.status_code == 502 and r.json == {"error": "hermes unavailable"}


def test_create_task_maps_the_agent_to_a_roster_bot_and_watches_it():
    b, w = FakeBoard(), FakeWatcher()
    r = client(board=b, watcher=w).post("/tasks", json={"text": "look into gas price APIs for a tile", "agent": "researcher"}, headers=AUTH)
    assert r.status_code == 201 and r.json["task"]["id"] == "t_9"
    title, body, assignee = b.created[0]
    assert assignee == "vega" and body.startswith("look into") and len(title) <= 80
    assert w.watched == ["t_9"]


def test_lanes_and_the_override_map_pass_through():
    b = FakeBoard()
    c = client(board=b, BRIDGE_AGENT_MAP="builder=kael")
    c.post("/tasks", json={"text": "x", "agent": "cc-coder"}, headers=AUTH)
    c.post("/tasks", json={"text": "y", "agent": "builder"}, headers=AUTH)
    assert [a for _, _, a in b.created] == ["cc-coder", "kael"]


def test_unknown_agent_is_400():
    assert client().post("/tasks", json={"text": "x", "agent": "weather"}, headers=AUTH).status_code == 400


@pytest.mark.parametrize("agent", ["trader", "portfolio", "wallet"])
def test_money_agents_are_refused(agent):
    b = FakeBoard()
    assert client(board=b, EXO_AGENTS=f"{agent},builder").post("/tasks", json={"text": "x", "agent": agent}, headers=AUTH).status_code == 400
    assert b.created == []


def test_board_failure_is_502():
    assert client(board=FakeBoard(fail=True)).post("/tasks", json={"text": "x", "agent": "builder"}, headers=AUTH).status_code == 502


def test_board_is_trimmed_and_filtered_to_the_mapped_bots():
    b = FakeBoard()
    tasks = client(board=b).get("/board", headers=AUTH).json["tasks"]
    assert tasks[0]["status"] == "running" and "body" not in tasks[0]
    assert b.listed == [("vega", "rune", "cc-coder", "cc-research", "cc-writer", "cc-reviewer")]


def test_oversized_request_is_rejected():
    r = client().post("/talk", data=b"x" * 100_000, content_type="application/json", headers=AUTH)
    assert r.status_code == 413


def test_non_json_body_is_400():
    assert client().post("/tasks", data="nope", content_type="text/plain", headers=AUTH).status_code == 400


def test_register_extension_routes():
    from flask import Blueprint
    bp = Blueprint("ext", __name__)

    @bp.get("/ping")
    def ping():
        return {"pong": True}

    env = {"EXO_BRIDGE_TOKEN": TOKEN, "EXO_BRIDGE_HOST": "100.64.0.5", "API_SERVER_KEY": "k"}  # public-ok
    app = create_app(Config.from_env(env), FakeBoard(), FakeTalker(), blueprints=[bp])
    c = app.test_client()
    assert c.get("/ping").status_code == 401           # extension routes are authed too
    assert c.get("/ping", headers=AUTH).json == {"pong": True}


def test_502_bodies_do_not_leak_detail():
    class Leaky(FakeBoard):
        def create(self, *a):
            raise BoardError("create: SECRET stderr http://127.0.0.1:8642")

        def list(self, assignees):
            raise BoardError("SECRET")

    class LeakyTalker(FakeTalker):
        def ask(self, *a):
            raise TalkError("SECRET http://127.0.0.1:8642")

    c = client(board=Leaky(), talker=LeakyTalker())
    for r in (c.post("/talk", json={"text": "hi"}, headers=AUTH),
              c.post("/tasks", json={"text": "x", "agent": "builder"}, headers=AUTH),
              c.get("/board", headers=AUTH)):
        assert r.status_code == 502
        assert "SECRET" not in r.get_data(as_text=True) and "127.0.0.1" not in r.get_data(as_text=True)
    assert c.post("/tasks", json={"text": "x", "agent": "builder"}, headers=AUTH).json == {"error": "board failed"}
