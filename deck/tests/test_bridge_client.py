import pytest
import requests
from exo_deck.bridge_client import Bridge, BridgeError


class Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def test_talk_sends_token_and_session():
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return Resp(200, {"reply": "hi"})

    assert Bridge("http://b:8765", "tok", post=post).talk("hello", "exo-deck-2026-10-07") == "hi"
    assert seen["url"] == "http://b:8765/talk" and seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["json"] == {"text": "hello", "session": "exo-deck-2026-10-07"} and seen["timeout"] <= 130


def test_delegate_and_board():
    b = Bridge("http://b:8765/", "tok", post=lambda u, **k: Resp(201, {"task": {"id": "t_1"}}),
               get=lambda u, **k: Resp(200, {"tasks": [{"id": "t_1"}]}))
    assert b.delegate("find X", "researcher") == {"id": "t_1"}
    assert b.board() == [{"id": "t_1"}]


def test_errors_become_bridge_error():
    with pytest.raises(BridgeError):
        Bridge("http://b", "t", post=lambda u, **k: Resp(502, {"error": "down"})).talk("x", "s")
    with pytest.raises(BridgeError):
        Bridge("http://b", "t", post=lambda u, **k: (_ for _ in ()).throw(requests.ConnectTimeout())).talk("x", "s")
