import pytest
from exo_bridge.app import create_app
from exo_bridge.config import Config
from exo_bridge.kanban import KanbanError
from exo_bridge.talk import TalkError

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeKanban:
    def __init__(self, fail=False):
        self.created, self.subs, self.fail = [], [], fail

    def create(self, title, body, assignee, max_runtime, idempotency_key):
        if self.fail:
            raise KanbanError("boom")
        self.created.append((title, body, assignee, max_runtime))
        return {"id": "t_9", "title": title, "assignee": assignee, "status": "ready"}

    def subscribe(self, task_id, chat_id):
        self.subs.append((task_id, chat_id))

    def list(self):
        return [{"id": "t_9", "title": "x", "assignee": "builder", "status": "running",
                 "created_at": 1, "completed_at": None, "result": None, "body": "secret-long-body"}]


class FakeTalker:
    def __init__(self, fail=False):
        self.fail, self.asked = fail, []

    def ask(self, text, session):
        if self.fail:
            raise TalkError("hermes down")
        self.asked.append((text, session))
        return "You hold 0.4 ETH."


def client(kanban=None, talker=None, chat_id=""):
    env = {"EXO_BRIDGE_TOKEN": TOKEN, "EXO_BRIDGE_HOST": "100.64.0.5",  # public-ok
           "API_SERVER_KEY": "k", "EXO_TELEGRAM_CHAT_ID": chat_id}
    app = create_app(Config.from_env(env), kanban or FakeKanban(), talker or FakeTalker())
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


def test_create_task_and_subscribe():
    k = FakeKanban()
    r = client(kanban=k, chat_id="777").post("/tasks", json={"text": "look into gas price APIs for a tile", "agent": "researcher"}, headers=AUTH)
    assert r.status_code == 201 and r.json["task"]["id"] == "t_9"
    title, body, assignee, runtime = k.created[0]
    assert assignee == "researcher" and runtime == "30m" and body.startswith("look into")
    assert len(title) <= 80
    assert k.subs == [("t_9", "777")]


def test_unknown_agent_is_400():
    assert client().post("/tasks", json={"text": "x", "agent": "weather"}, headers=AUTH).status_code == 400


def test_kanban_failure_is_502():
    assert client(kanban=FakeKanban(fail=True)).post("/tasks", json={"text": "x", "agent": "builder"}, headers=AUTH).status_code == 502


def test_board_is_trimmed():
    tasks = client().get("/board", headers=AUTH).json["tasks"]
    assert tasks[0]["status"] == "running" and "body" not in tasks[0]


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
    app = create_app(Config.from_env(env), FakeKanban(), FakeTalker(), blueprints=[bp])
    c = app.test_client()
    assert c.get("/ping").status_code == 401           # extension routes are authed too
    assert c.get("/ping", headers=AUTH).json == {"pong": True}


def test_502_bodies_do_not_leak_detail():
    class Leaky(FakeKanban):
        def create(self, *a):
            raise KanbanError("create: SECRET stderr http://127.0.0.1:8642")

        def list(self):
            raise KanbanError("SECRET")

    class LeakyTalker(FakeTalker):
        def ask(self, *a):
            raise TalkError("SECRET http://127.0.0.1:8642")

    c = client(kanban=Leaky(), talker=LeakyTalker())
    for r in (c.post("/talk", json={"text": "hi"}, headers=AUTH),
              c.post("/tasks", json={"text": "x", "agent": "builder"}, headers=AUTH),
              c.get("/board", headers=AUTH)):
        assert r.status_code == 502
        assert "SECRET" not in r.get_data(as_text=True) and "127.0.0.1" not in r.get_data(as_text=True)
    assert c.post("/tasks", json={"text": "x", "agent": "builder"}, headers=AUTH).json == {"error": "kanban failed"}
