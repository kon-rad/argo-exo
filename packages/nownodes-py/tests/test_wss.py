import asyncio
import json
from exo_nownodes import wss


class FakeWS:
    def __init__(self, messages, drop_after=None):
        self.sent, self.messages, self.drop_after = [], list(messages), drop_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, m):
        self.sent.append(json.loads(m))

    async def recv(self):
        if not self.messages:
            raise ConnectionError("dropped")
        return json.dumps(self.messages.pop(0))


def test_wss_url_redacted():
    assert wss.redact_url("wss://eth-blockbook.nownodes.io/wss/SECRET") == "wss://eth-blockbook.nownodes.io/wss/***"


def test_reconnects_and_dedups():
    tx = {"txid": "0xaa", "confirmations": 1}
    sockets = [FakeWS([{"id": "1", "data": {"subscribed": True}}, {"id": "1", "data": {"address": "0x1", "tx": tx}}]),
               FakeWS([{"id": "1", "data": {"subscribed": True}}, {"id": "1", "data": {"address": "0x1", "tx": tx}},
                       {"id": "1", "data": {"address": "0x1", "tx": {"txid": "0xbb"}}}])]
    used, seen, stop = list(sockets), [], asyncio.Event()

    def on_tx(t):
        seen.append(t["txid"])
        if t["txid"] == "0xbb":
            stop.set()

    connect = lambda url, **kw: sockets.pop(0)
    asyncio.run(asyncio.wait_for(wss.subscribe_addresses("ethereum", ["0x1"], on_tx, api_key="K",
                                                          connect=connect, stop=stop, backoff=(0, 0)), 5))
    assert seen == ["0xaa", "0xbb"]
    assert all(s.sent[0]["method"] == "subscribeAddresses" and s.sent[0]["params"]["addresses"] == ["0x1"] for s in used)


def test_handler_error_does_not_drop_socket_or_log_url(caplog):
    sock = FakeWS([{"data": {"tx": {"txid": "0x1"}}}, {"data": {"tx": {"txid": "0x2"}}}])
    stop, seen = asyncio.Event(), []

    def on_tx(t):
        seen.append(t["txid"])
        if t["txid"] == "0x2":
            stop.set()
        else:
            raise RuntimeError("boom SECRETKEY")

    caplog.set_level("DEBUG")
    asyncio.run(asyncio.wait_for(wss.subscribe_addresses("ethereum", ["0x1"], on_tx, api_key="SECRETKEY",
                                                          connect=lambda u, **kw: sock, stop=stop), 5))
    assert seen == ["0x1", "0x2"] and "SECRETKEY" not in caplog.text
