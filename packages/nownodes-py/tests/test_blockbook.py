import pytest
from exo_nownodes.blockbook import Blockbook
from exo_nownodes.rpc import RpcError


class Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def test_address_url_header_params():
    seen = {}

    def get(url, **kw):
        seen.update(url=url, **kw)
        return Resp(200, {"address": "0xabc", "balance": "1"})

    j = Blockbook("polygon", "K", get=get).address("0xabc", "tokenBalances", pageSize=5)
    assert j["balance"] == "1"
    assert seen["url"] == "https://maticbook.nownodes.io/api/v2/address/0xabc"
    assert seen["params"] == {"details": "tokenBalances", "pageSize": 5} and seen["headers"]["api-key"] == "K"


def test_fetcher_shape_and_errors():
    bb = Blockbook("ethereum", "K", get=lambda url, **kw: Resp(503, {}))
    with pytest.raises(RpcError, match="HTTP 503"):
        bb.fetcher()("https://eth-blockbook.nownodes.io/api/v2/address/0x1", {"details": "basic"})


def test_tx():
    bb = Blockbook("base", "K", get=lambda url, **kw: Resp(200, {"txid": url.rsplit("/", 1)[1], "confirmations": 3}))
    assert bb.tx("0xdead")["confirmations"] == 3


def test_non_json_200_is_rpc_error():
    class Bad(Resp):
        def json(self):
            raise ValueError("x")
    with pytest.raises(RpcError, match="invalid JSON from base"):
        Blockbook("base", "K", get=lambda u, **k: Bad(200, None)).tx("0x1")


def test_counter_oserror_keeps_result(caplog):
    class C:
        def add(self, n=1):
            raise OSError("full")
    assert Blockbook("base", "K", get=lambda u, **k: Resp(200, {"a": 1}), counter=C()).tx("0x1") == {"a": 1}
    assert "not updated" in caplog.text


def test_network_error_context_has_no_key():
    import requests

    def get(url, **kw):
        raise requests.ConnectionError("fail SECRETKEY")
    with pytest.raises(RpcError) as e:
        Blockbook("base", "SECRETKEY", get=get).tx("0x1")
    assert "SECRETKEY" not in str(e.value)
    assert e.value.__context__ is None or "SECRETKEY" not in str(e.value.__context__)


def test_missing_key_and_unknown_chain(monkeypatch):
    monkeypatch.delenv("NOWNODES_API_KEY", raising=False)
    def get(*a, **k):
        raise AssertionError("must not send")
    with pytest.raises(RpcError, match="NOWNODES_API_KEY is not set"):
        Blockbook("base", get=get).tx("0x1")
    with pytest.raises(ValueError):
        Blockbook("solana", "K")
