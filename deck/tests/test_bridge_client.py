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


def test_pending_approvals_returns_items_with_a_fixed_timeout():
    seen = {}

    def get(url, **kw):
        seen.update(url=url, **kw)
        return Resp(200, {"items": [{"id": "p1"}]})

    assert Bridge("http://b:8765", "tok", get=get).pending_approvals() == [{"id": "p1"}]
    assert seen["url"] == "http://b:8765/approvals/pending" and seen["timeout"] and seen["headers"]["Authorization"] == "Bearer tok"


def test_pending_approvals_rejects_a_non_list():
    with pytest.raises(BridgeError):
        Bridge("http://b", "t", get=lambda u, **k: Resp(200, {"items": {"id": "p1"}})).pending_approvals()
    with pytest.raises(BridgeError):
        Bridge("http://b", "t", get=lambda u, **k: Resp(502, {"error": "ledger unavailable"})).pending_approvals()


def test_report_executed_bodies_and_409():
    seen = []

    def post(url, **kw):
        seen.append((url, kw["json"], kw["timeout"]))
        return Resp(200, {"ok": True})

    b = Bridge("http://b", "t", post=post)
    assert b.report_executed("p1", tx_hash="0x" + "ab" * 32) is True
    assert b.report_executed("p1", error="gas estimate reverted") is True
    assert seen[0][:2] == ("http://b/approvals/p1/executed", {"tx_hash": "0x" + "ab" * 32})
    assert seen[1][1] == {"error": "gas estimate reverted"} and all(t for *_, t in seen)
    assert Bridge("http://b", "t", post=lambda u, **k: Resp(409, {"error": "not awaiting the key"})) \
        .report_executed("p1", tx_hash="0x" + "ab" * 32) is False
    for status in (400, 502):
        with pytest.raises(BridgeError):
            Bridge("http://b", "t", post=lambda u, **k: Resp(status, {"error": "x"})).report_executed("p1", error="e")
    with pytest.raises(ValueError):
        b.report_executed("p1")
    with pytest.raises(ValueError):
        b.report_executed("p1", tx_hash="0x1", error="e")
    with pytest.raises(ValueError):
        b.report_executed("../guard", error="e")          # the id goes into the URL path


def test_freeze_posts_reason():
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return Resp(200, {"ok": True, "report_tx": "0x", "simulated": True})

    assert Bridge("http://b", "t", post=post).freeze("panic")["ok"] is True
    assert seen["url"] == "http://b/freeze" and seen["json"] == {"reason": "panic"} and seen["timeout"]
