import pytest
from exo_nownodes.rpc import Rpc, RpcError
from exo_presale.rpc import LocalRpc, base_rpc


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


def recorder(data=None):
    seen = []

    def post(url, **kw):
        seen.append((url, kw))
        return Resp(data if data is not None else {"jsonrpc": "2.0", "id": kw["json"]["id"] if isinstance(kw["json"], dict) else 0, "result": "0x1"})
    return post, seen


def test_default_is_nownodes_base():
    r = base_rpc({"NOWNODES_API_KEY": "k"})
    assert isinstance(r, Rpc) and not isinstance(r, LocalRpc) and r.url == "https://base.nownodes.io"


def test_short_timeout_and_no_backoff_on_the_state_path():
    post, seen = recorder()
    r = base_rpc({"NOWNODES_API_KEY": "k"}, timeout=3, _post=post)
    r.call("eth_blockNumber", [])
    assert seen[0][1]["timeout"] == 3
    import time
    t = time.monotonic()
    with pytest.raises(RpcError):
        base_rpc({"NOWNODES_API_KEY": "k"}, timeout=3, _post=lambda url, **kw: Resp({}, 503)).call("eth_blockNumber", [])
    assert time.monotonic() - t < 0.5          # retries don't sleep the 0.5 s + 1.5 s backoff while holding the lock


def test_default_timeout_untouched():
    post, seen = recorder()
    base_rpc({"NOWNODES_API_KEY": "k"}, _post=post).call("eth_blockNumber", [])
    assert seen[0][1]["timeout"] == 30


@pytest.mark.parametrize("url", ["http://127.0.0.1:8546", "http://127.0.0.1:8546/", "http://127.0.0.1:9/base"])
def test_loopback_override_accepted_and_never_sends_the_key(url):
    post, seen = recorder()
    r = base_rpc({"EXO_BASE_RPC_URL": url, "NOWNODES_API_KEY": "secret-key"}, _post=post)
    assert isinstance(r, LocalRpc)
    assert r.call("eth_chainId", []) == "0x1"
    sent_url, kw = seen[0]
    assert sent_url == url and "api-key" not in {k.lower() for k in kw.get("headers", {})}
    assert "secret-key" not in repr(kw)


@pytest.mark.parametrize("url", ["https://mainnet.base.org", "http://localhost:8546", "http://127.0.0.1",
                                 "https://127.0.0.1:8546", "http://127.0.0.1.evil.example:80", "http://u:p@127.0.0.1:8546",
                                 "http://192.0.2.3:8546", "http://[::1]:8546", "http://0.0.0.0:8546", " http://127.0.0.1:8546",
                                 "http://127.0.0.1:8546?x=https://evil", "http://127.0.0.1:0"])  # public-ok
def test_override_refuses_anything_but_http_loopback_v4(url):
    with pytest.raises(SystemExit):
        base_rpc({"EXO_BASE_RPC_URL": url})


def test_local_rpc_batch_and_errors():
    post, _ = recorder([{"jsonrpc": "2.0", "id": 0, "result": "0x2"}])
    r = LocalRpc("http://127.0.0.1:8546", post=post)
    # ids are assigned per call; the fake reply id 0 won't match, so a missing reply is an error, never a silent None
    with pytest.raises(RpcError):
        r.batch([("eth_chainId", [])])
    r2 = LocalRpc("http://127.0.0.1:8546", post=lambda url, **kw: Resp({"jsonrpc": "2.0", "id": kw["json"]["id"], "error": {"message": "execution reverted"}}))
    with pytest.raises(RpcError, match="revert"):
        r2.call("eth_call", [])
    def boom(url, **kw):
        import requests
        raise requests.ConnectionError("refused")
    with pytest.raises(RpcError, match="network"):
        LocalRpc("http://127.0.0.1:8546", post=boom).call("eth_chainId", [])
