import asyncio
import json
from exo_nownodes import wss


ACK = {"id": "1", "data": {"subscribed": True}}


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
                       {"id": "1", "data": {"address": "0x1", "tx": {"txid": "0xbb", "blockHeight": 5}}}])]
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
    sock = FakeWS([ACK, {"data": {"tx": {"txid": "0x1", "confirmations": 1}}}, {"data": {"tx": {"txid": "0x2", "confirmations": 1}}}])
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


def _run(sockets, on_tx, stop, backoff=(0, 0)):
    asyncio.run(asyncio.wait_for(wss.subscribe_addresses(
        "ethereum", ["0x1"], on_tx, api_key="K", connect=lambda u, **kw: sockets.pop(0), stop=stop, backoff=backoff), 5))


def test_mempool_then_mined_announces_once_on_mined():
    mem = {"txid": "0xaa", "confirmations": 0, "blockHeight": -1}
    mined = {"txid": "0xaa", "confirmations": 1, "blockHeight": 9}
    stop, got = asyncio.Event(), []
    sock = FakeWS([ACK, {"data": {"tx": mem}}, {"data": {"tx": mined}}, {"data": {"tx": mined}}])

    def on_tx(t):
        got.append(t)
        stop.set()
    _run([sock], on_tx, stop)
    assert got == [mined]


def test_mempool_only_tx_is_silent():
    stop, got = asyncio.Event(), []
    sock = FakeWS([ACK, {"data": {"tx": {"txid": "0xaa", "confirmations": 0, "blockHeight": 0}}}])
    sock2 = FakeWS([ACK, {"data": {"tx": {"txid": "0xzz", "blockHeight": 3}}}])
    _run([sock, sock2], lambda t: (got.append(t["txid"]), stop.set()), stop)
    assert got == ["0xzz"]


def test_slow_handler_does_not_block_recv():
    import time
    stop, order, recvd_at = asyncio.Event(), [], {}

    class TimedWS(FakeWS):
        async def recv(self):
            m = await super().recv()
            recvd_at[len(recvd_at)] = time.monotonic()
            return m
    sock = TimedWS([ACK, {"data": {"tx": {"txid": "0x1", "confirmations": 1}}}, {"data": {"tx": {"txid": "0x2", "confirmations": 1}}}])
    t0 = {}

    def on_tx(t):
        t0.setdefault("start", time.monotonic())
        time.sleep(0.4)
        order.append(t["txid"])
        if t["txid"] == "0x2":
            stop.set()
    _run([sock], on_tx, stop)
    assert order == ["0x1", "0x2"]                       # ordering kept
    assert recvd_at[2] - t0["start"] < 0.3               # 2nd message received while 1st handler still sleeping


def test_unacked_subscribe_reconnects_with_backoff_and_resets_after_ack():
    stop, got = asyncio.Event(), []
    bad = FakeWS([{"id": "1", "data": {"error": "nope"}}])
    good = FakeWS([ACK, {"data": {"tx": {"txid": "0x1", "confirmations": 1}}}])
    _run([bad, good], lambda t: (got.append(t["txid"]), stop.set()), stop)
    assert got == ["0x1"] and len(good.sent) == 1


def test_backoff_not_reset_before_ack(monkeypatch):
    delays, stop = [], asyncio.Event()
    real = asyncio.sleep

    async def fake_sleep(d):
        delays.append(d)
        if len(delays) == 3:
            stop.set()
        await real(0)
    monkeypatch.setattr(wss.asyncio, "sleep", fake_sleep)
    socks = [FakeWS([]) for _ in range(5)]
    _run(socks, lambda t: None, stop, backoff=(1, 2, 5))
    assert delays[:3] == [1, 2, 5]
