"""Drive the real Flask app through the deck's real Bridge client so the two cannot drift."""
import pytest
from exo_bridge.app import create_app
from exo_bridge.config import Config
from exo_bridge.kanban import KanbanError
from exo_bridge.talk import TalkError
from exo_deck.bridge_client import Bridge, BridgeError

TOKEN = "t" * 40


class K:
    fail = False

    def create(self, title, body, assignee, max_runtime, idempotency_key):
        if self.fail:
            raise KanbanError("nope")
        return {"id": "t_1", "title": title, "assignee": assignee, "status": "ready"}

    def subscribe(self, *a):
        pass

    def list(self):
        return [{"id": "t_1", "title": "x", "assignee": "builder", "status": "ready"}]


class T:
    fail = False

    def ask(self, text, session):
        if self.fail:
            raise TalkError("hermes down")
        return f"echo {text}"


class Resp:
    def __init__(self, r):
        self.r = r
        self.status_code = r.status_code

    def json(self):
        return self.r.json


def make(token=TOKEN, k=None, t=None):
    env = {"EXO_BRIDGE_TOKEN": TOKEN, "EXO_BRIDGE_HOST": "100.64.0.5", "API_SERVER_KEY": "k"}  # public-ok
    c = create_app(Config.from_env(env), k or K(), t or T()).test_client()

    def post(url, headers=None, json=None, timeout=None):
        return Resp(c.post(url.replace("http://b", ""), headers=headers, json=json))

    def get(url, headers=None, timeout=None):
        return Resp(c.get(url.replace("http://b", ""), headers=headers))

    return Bridge("http://b", token, post=post, get=get)


def test_talk():
    assert make().talk("hello", "s1") == "echo hello"


def test_delegate():
    assert make().delegate("dig", "researcher")["id"] == "t_1"


def test_board():
    assert make().board()[0]["status"] == "ready"


def test_bad_token_is_bridge_error():
    with pytest.raises(BridgeError, match="401"):
        make(token="x" * 40).board()


def test_unknown_agent_is_bridge_error():
    with pytest.raises(BridgeError, match="400"):
        make().delegate("x", "weather")


def test_upstream_failures_are_bridge_errors():
    t = T(); t.fail = True
    with pytest.raises(BridgeError, match="502"):
        make(t=t).talk("hi", "s")
    k = K(); k.fail = True
    with pytest.raises(BridgeError, match="502"):
        make(k=k).delegate("x", "builder")
