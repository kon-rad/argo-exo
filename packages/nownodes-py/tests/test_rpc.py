import pytest
import requests
from exo_nownodes import hosts
from exo_nownodes.rpc import Rpc, RpcError
from exo_nownodes.usage import Usage


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def test_hosts_cover_the_four_chains():
    for t in (hosts.RPC, hosts.BLOCKBOOK, hosts.CHAIN_IDS):
        assert set(t) >= {"ethereum", "base", "arbitrum", "polygon"}
    assert hosts.BLOCKBOOK["polygon"] == "maticbook.nownodes.io"


def test_call_sends_header_and_returns_result(tmp_path):
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return Resp(200, {"jsonrpc": "2.0", "id": kw["json"]["id"], "result": "0x1"})

    u = Usage(tmp_path / "u.json")
    assert Rpc("ethereum", "KEY", post=post, counter=u).call("eth_chainId", []) == "0x1"
    assert seen["url"] == "https://" + hosts.RPC["ethereum"] and seen["headers"]["api-key"] == "KEY"
    assert seen["json"]["method"] == "eth_chainId" and seen["timeout"] <= 30
    assert u.month_total() == 1


def test_jsonrpc_error_raises():
    post = lambda url, **kw: Resp(200, {"jsonrpc": "2.0", "id": 1, "error": {"code": 3, "message": "execution reverted"}})
    with pytest.raises(RpcError, match="execution reverted"):
        Rpc("ethereum", "K", post=post).call("eth_call", [{}, "latest"])


def test_retries_then_raises():
    calls, sleeps = [], []

    def post(url, **kw):
        calls.append(1)
        return Resp(429)

    with pytest.raises(RpcError, match="HTTP 429"):
        Rpc("base", "K", post=post, sleep=sleeps.append).call("eth_blockNumber", [])
    assert len(calls) == 3 and sleeps == [0.5, 1.5]


def test_retry_then_success():
    replies = iter([Resp(503), Resp(200, {"jsonrpc": "2.0", "id": 1, "result": "0x10"})])
    assert Rpc("base", "K", post=lambda u, **k: next(replies), sleep=lambda s: None).call("eth_blockNumber", []) == "0x10"


def test_network_error_is_rpc_error_and_key_never_in_error_text():
    def post(url, **kw):
        raise requests.ConnectionError(f"failed to reach https://x?key=SECRETKEY")
    with pytest.raises(RpcError) as e:
        Rpc("ethereum", "SECRETKEY", post=post, sleep=lambda s: None).call("eth_chainId", [])
    assert "SECRETKEY" not in str(e.value)


def test_key_never_in_error_text():
    post = lambda url, **kw: Resp(200, {"error": {"code": -32000, "message": "bad key SECRETKEY"}})
    with pytest.raises(RpcError) as e:
        Rpc("ethereum", "SECRETKEY", post=post).call("eth_chainId", [])
    assert "SECRETKEY" not in str(e.value)


def test_batch_keeps_order():
    def post(url, **kw):
        body = kw["json"]
        return Resp(200, [{"id": c["id"], "result": c["method"]} for c in reversed(body)])
    assert Rpc("ethereum", "K", post=post).batch([("a", []), ("b", [])]) == ["a", "b"]


def test_usage_warns_at_80_percent(tmp_path, caplog):
    u = Usage(tmp_path / "u.json", monthly_limit=10)
    for _ in range(9):
        u.add()
    assert u.month_total() == 9 and "80%" in caplog.text
