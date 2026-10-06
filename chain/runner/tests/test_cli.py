"""cli.py talks to exo-bridge over HTTP (it never holds the ledger DSN). Tested against a fake bridge."""
import json
from pathlib import Path

import pytest
import requests
from exo_guardian import cli

ENV = {"EXO_BRIDGE_URL": "http://100.64.0.5:8765/", "EXO_BRIDGE_TOKEN": "t" * 40}  # public-ok
REQ = {"source": "voice", "intent": {"kind": "send", "summary": "x"}, "tx": {"chain_id": 1, "to": "0x" + "a" * 40}}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeBridge:
    def __init__(self, status=200, body=None, exc=None):
        self.status, self.body, self.exc, self.calls = status, body if body is not None else {"verdict": "approve"}, exc, []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.exc:
            raise self.exc
        return Resp(self.status, self.body)


def test_guard_posts_to_the_bridge(capsys):
    b = FakeBridge(body={"proposal_id": "p", "verdict": "approve", "queued": True})
    assert cli.main(["guard", json.dumps(REQ)], env=ENV, post=b) == 0
    [(url, kw)] = b.calls
    assert url == "http://100.64.0.5:8765/guard" and kw["json"] == REQ  # public-ok
    assert kw["headers"] == {"Authorization": "Bearer " + "t" * 40} and kw["timeout"] >= 260
    assert json.loads(capsys.readouterr().out)["queued"] is True


@pytest.mark.parametrize("status,body,code", [(502, {"error": "guardian unavailable"}, 1), (400, {"error": "invalid guard request"}, 2),
                                              (401, {"error": "unauthorized"}, 1), (503, {"error": "guardian not configured"}, 1),
                                              (200, ValueError("not json"), 1), (200, ["x"], 1)])
def test_bridge_errors(capsys, status, body, code):
    assert cli.main(["guard", json.dumps(REQ)], env=ENV, post=FakeBridge(status, body)) == code
    out = capsys.readouterr()
    if status == 200:
        assert out.out == ""
    else:
        assert json.loads(out.out) == body


def test_bridge_down_is_1(capsys):
    assert cli.main(["guard", json.dumps(REQ)], env=ENV, post=FakeBridge(exc=requests.ConnectionError("refused"))) == 1
    assert "bridge unreachable" in capsys.readouterr().err


@pytest.mark.parametrize("argv,env", [(["guard", "{not json"], ENV), (["guard", "[]"], ENV), (["freeze"], ENV),
                                      (["guard", json.dumps(REQ)], {}), (["guard", json.dumps(REQ)], {"EXO_BRIDGE_URL": "http://x"})])
def test_usage_errors_are_2(argv, env):
    b = FakeBridge()
    assert cli.main(argv, env=env, post=b) == 2 and b.calls == []


def test_cli_never_touches_the_ledger():
    src = (Path(cli.__file__)).read_text()
    assert "DSN" not in src and "PgStore" not in src and "psycopg" not in src and "service" not in src
