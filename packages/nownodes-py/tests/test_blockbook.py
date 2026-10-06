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
