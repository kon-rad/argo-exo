import json

import pytest
from eth_utils import keccak
from exo_deck import approve_hook
from exo_deck.approve_hook import run_hook
from exo_deck.bridge_client import BridgeError
from exo_deck.exo_module import approval_hash, execute_calldata
from exo_nownodes.rpc import RpcError

MODULE = "0x" + "44" * 20
NOW = 1_800_000_000
PID = "11111111-1111-4111-8111-111111111111"
RAW = b"\x02signed"
TXH = "0x" + keccak(RAW).hex()


def word(n: int) -> str:
    return "0x" + n.to_bytes(32, "big").hex()


class FakeRpc:
    def __init__(self, until=NOW + 300, used=0, frozen=0, receipt=None, known=None):
        self.sent, self.calls = [], []
        self.until, self.used, self.frozen, self.receipt, self.known = until, used, frozen, receipt, known

    def views(self, sel):
        return {"bfc3b08f": word(self.until), "b07c411f": word(self.used), "054f7d9c": word(self.frozen)}[sel]

    def batch(self, calls):
        return [self.call(m, p) for m, p in calls]

    def call(self, m, p):
        self.calls.append(m)
        if m == "eth_sendRawTransaction":
            self.sent.append(p[0])
            return TXH
        if m == "eth_call":
            return self.views(p[0]["data"][2:10])
        if m == "eth_getTransactionReceipt":
            return self.receipt
        if m == "eth_getTransactionByHash":
            return self.known
        return {"eth_getTransactionCount": "0x0", "eth_estimateGas": "0x5208", "eth_maxPriorityFeePerGas": "0x1",
                "eth_getBlockByNumber": {"baseFeePerGas": "0x1"}, "eth_chainId": "0x1"}[m]


class FakeSigner:
    address = "0x" + "dd" * 20

    def __init__(self):
        self.signed = []

    def sign(self, tx):
        self.signed.append(tx)
        return RAW


class FakeBridge:
    def __init__(self, fail=False):
        self.reports, self.fail = [], fail

    def report_executed(self, pid, tx_hash=None, error=None):
        if self.fail:
            raise BridgeError("network: down")
        self.reports.append((pid, tx_hash, error))
        return True


def item(tmp_path, **kw):
    p = tmp_path / "tx-approved" / f"1_{PID}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    it = {"id": PID, "summary": "Send 20 USDC", "chain": "ethereum", "to": "0x" + "22" * 20, "value": "7",
          "data": "0xa9059cbb", "salt": "0x" + "00" * 31 + "aa", "expires_at": NOW + 300}
    it.update(kw)
    p.write_text(json.dumps(it))
    return p


def run(p, rpc, br=None, signer=None, spoken=None):
    return run_hook(p, signer or FakeSigner(), rpc, br or FakeBridge(), MODULE,
                    (spoken if spoken is not None else []).append, now=lambda: NOW)


def test_hook_signs_sends_records(tmp_path):
    p, rpc, br, signer = item(tmp_path), FakeRpc(), FakeBridge(), FakeSigner()
    assert run(p, rpc, br, signer) == TXH
    it = json.loads(p.read_text())
    assert it["sent_tx"] == TXH and "reported" not in it and "settled" not in it and (tmp_path / "tx-sent").read_text() == TXH
    assert br.reports == [] and rpc.sent == ["0x" + RAW.hex()]     # broadcast is not executed: the sweep settles it
    (tx,) = signer.signed
    assert tx["to"].lower() == MODULE and tx["value"] == 0     # the Safe pays `value`, never the hot key
    assert tx["data"] == execute_calldata("0x" + "22" * 20, 7, bytes.fromhex("a9059cbb"), bytes.fromhex("00" * 31 + "aa"))


def test_hook_is_idempotent(tmp_path):
    p, rpc, br, signer = item(tmp_path), FakeRpc(), FakeBridge(), FakeSigner()
    run(p, rpc, br, signer)
    assert run(p, rpc, br, signer) is None
    assert len(rpc.sent) == 1 and len(signer.signed) == 1 and br.reports == []


def test_marker_is_written_before_broadcast(tmp_path):
    p = item(tmp_path)
    seen = {}

    class Peek(FakeRpc):
        def call(self, m, prm):
            if m == "eth_sendRawTransaction":
                seen.update(json.loads(p.read_text()))
            return super().call(m, prm)

    run(p, Peek())
    assert seen["sending"]["tx_hash"] == TXH and seen["sending"]["raw"] == "0x" + RAW.hex() and "sent_tx" not in seen


def test_crash_after_broadcast_resumes_from_receipt_without_resigning(tmp_path):
    p = item(tmp_path, sending={"tx_hash": TXH, "raw": "0x" + RAW.hex()})
    rpc, signer, br = FakeRpc(receipt={"status": "0x1", "transactionHash": TXH}), FakeSigner(), FakeBridge()
    assert run(p, rpc, br, signer) == TXH
    assert rpc.sent == [] and signer.signed == [] and br.reports == [(PID, TXH, None)]
    assert json.loads(p.read_text())["sent_tx"] == TXH


def test_marker_with_tx_in_mempool_is_treated_as_sent(tmp_path):
    p = item(tmp_path, sending={"tx_hash": TXH, "raw": "0x" + RAW.hex()})
    rpc = FakeRpc(known={"hash": TXH})
    assert run(p, rpc) == TXH and rpc.sent == []


def test_marker_unknown_to_node_rebroadcasts_the_same_bytes(tmp_path):
    p = item(tmp_path, sending={"tx_hash": TXH, "raw": "0x" + RAW.hex()})
    rpc, signer = FakeRpc(), FakeSigner()
    assert run(p, rpc, signer=signer) == TXH
    assert rpc.sent == ["0x" + RAW.hex()] and signer.signed == []      # identical signed bytes, same nonce


def test_marker_whose_raw_does_not_hash_to_the_marker_is_refused(tmp_path):
    p = item(tmp_path, sending={"tx_hash": "0x" + "00" * 32, "raw": "0x" + RAW.hex()})
    rpc, spoken = FakeRpc(), []
    assert run(p, rpc, spoken=spoken) is None and rpc.sent == []
    assert "sent_tx" not in json.loads(p.read_text())


def test_reverted_receipt_reports_failure(tmp_path):
    p = item(tmp_path, sending={"tx_hash": TXH, "raw": "0x" + RAW.hex()})
    br, spoken = FakeBridge(), []
    assert run(p, FakeRpc(receipt={"status": "0x0"}), br, spoken=spoken) is None
    assert br.reports == [(PID, None, "reverted onchain")] and "didn't go through" in spoken[0]
    assert json.loads(p.read_text())["failed"] == "reverted onchain"


def test_transport_error_on_send_leaves_marker_and_reports_nothing(tmp_path):
    class Flaky(FakeRpc):
        def call(self, m, prm):
            if m == "eth_sendRawTransaction":
                raise RpcError("network: read timed out")
            return super().call(m, prm)

    p, br, spoken = item(tmp_path), FakeBridge(), []
    assert run(p, Flaky(), br, spoken=spoken) is None
    it = json.loads(p.read_text())
    assert it["sending"]["tx_hash"] == TXH and "failed" not in it and "sent_tx" not in it and br.reports == []
    assert spoken and "not sure" in spoken[0].lower()
    # next run: the node has it mined -> recorded, never re-signed
    signer = FakeSigner()
    assert run(p, FakeRpc(receipt={"status": "0x1"}), br, signer) == TXH and signer.signed == []


def test_explicit_rejection_on_send_reports_fixed_error(tmp_path):
    class Rejects(FakeRpc):
        def call(self, m, prm):
            if m == "eth_sendRawTransaction":
                raise RpcError("eth_sendRawTransaction: insufficient funds for gas * price + value: secret-detail")
            return super().call(m, prm)

    p, br = item(tmp_path), FakeBridge()
    assert run(p, Rejects(), br) is None
    assert br.reports == [(PID, None, "broadcast rejected")]


@pytest.mark.parametrize("msg", ["eth_sendRawTransaction: already known",
                                 "eth_sendRawTransaction: nonce too low: next nonce 4, tx nonce 3"])
def test_ambiguous_rejections_leave_the_marker(tmp_path, msg):
    class Ambig(FakeRpc):
        def call(self, m, prm):
            if m == "eth_sendRawTransaction":
                raise RpcError(msg)
            return super().call(m, prm)

    p, br = item(tmp_path), FakeBridge()
    assert run(p, Ambig(), br) is None and br.reports == []
    assert "failed" not in json.loads(p.read_text())


def test_send_failure_reports_error(tmp_path):
    class Bad(FakeRpc):
        def call(self, m, p):
            if m == "eth_estimateGas":
                raise RpcError("eth_estimateGas: execution reverted: NotApproved 0xdeadbeef")
            return super().call(m, p)

    p, br, spoken = item(tmp_path), FakeBridge(), []
    assert run(p, Bad(), br, spoken=spoken) is None
    assert br.reports == [(PID, None, "gas estimate reverted")] and "didn't go through" in spoken[0]
    assert "0xdeadbeef" not in json.dumps(br.reports) and "0xdeadbeef" not in spoken[0]
    rpc = FakeRpc()
    assert run(p, rpc, br) is None and rpc.sent == [] and len(br.reports) == 1   # a failed item stays failed


@pytest.mark.parametrize("state,err", [({"used": 1}, "approval already used"), ({"frozen": 1}, "frozen"),
                                       ({"until": 0}, "not approved onchain"), ({"until": NOW - 1}, "not approved onchain")])
def test_onchain_recheck_blocks_signing(tmp_path, state, err):
    p, br, signer = item(tmp_path), FakeBridge(), FakeSigner()
    rpc = FakeRpc(**state)
    assert run(p, rpc, br, signer) is None
    assert signer.signed == [] and rpc.sent == [] and br.reports == [(PID, None, err)]


def test_recheck_uses_the_locally_recomputed_hash(tmp_path):
    p = item(tmp_path)
    want = approval_hash(1, MODULE, "0x" + "22" * 20, 7, bytes.fromhex("a9059cbb"), bytes.fromhex("00" * 31 + "aa"))
    datas = []

    class Rec(FakeRpc):
        def call(self, m, prm):
            if m == "eth_call":
                datas.append(prm[0]["data"])
            return super().call(m, prm)

    run(p, Rec())
    assert "0xbfc3b08f" + want.hex() in datas and "0xb07c411f" + want.hex() in datas


@pytest.mark.parametrize("bad", [{"salt": "0x01"}, {"to": MODULE}, {"value": "-3"}, {"chain": "base"}])
def test_invalid_item_is_never_signed(tmp_path, bad):
    p, br, signer = item(tmp_path, **bad), FakeBridge(), FakeSigner()
    assert run(p, FakeRpc(), br, signer) is None
    assert signer.signed == [] and br.reports == [(PID, None, "invalid queue item")]


def test_rpc_down_before_signing_fails_closed(tmp_path):
    class Down(FakeRpc):
        def batch(self, calls):
            raise RpcError("network: down")

    p, br, signer = item(tmp_path), FakeBridge(), FakeSigner()
    assert run(p, Down(), br, signer) is None and signer.signed == []
    assert br.reports == [(PID, None, "rpc unavailable")]


def test_bridge_down_after_settling_keeps_the_outcome_and_retries_report(tmp_path):
    from exo_deck.approve_hook import sweep
    p, rpc = item(tmp_path), FakeRpc()
    assert run(p, rpc, FakeBridge()) == TXH
    rpc.receipt = {"status": "0x1"}
    sweep(tmp_path, rpc, FakeBridge(fail=True), MODULE, lambda s: None, now=lambda: NOW)
    it = json.loads(p.read_text())
    assert it["settled"] == "executed" and it.get("reported") is not True
    br = FakeBridge()
    assert run(p, rpc, br) is None                                # the hook itself retries a settled report too
    assert br.reports == [(PID, TXH, None)] and len(rpc.sent) == 1 and json.loads(p.read_text())["reported"] is True


def test_unreadable_item_does_nothing(tmp_path):
    p = tmp_path / "tx-approved" / "1_x.json"
    p.parent.mkdir(parents=True)
    p.write_text("not json")
    br, rpc = FakeBridge(), FakeRpc()
    assert run(p, rpc, br) is None and rpc.calls == [] and br.reports == []


def test_main_with_locked_key_puts_the_item_back(tmp_path, monkeypatch):
    p = item(tmp_path)
    monkeypatch.setenv("DECK_ROOT", str(tmp_path.parent))
    spoken = []
    monkeypatch.setattr(approve_hook, "_speak", spoken.append)
    monkeypatch.setenv("EXO_MODULE_ADDRESS", MODULE)
    monkeypatch.setenv("EXO_HOT_KEYSTORE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("EXO_HOT_PASSFILE", str(tmp_path / "missing.pass"))
    assert approve_hook.main([str(p)]) == 1
    assert not p.exists() and (tmp_path / "tx-queue" / p.name).exists()
    assert "exo-unlock" in spoken[0]


# ---- fix round 1 -------------------------------------------------------------------------------------------


def test_marker_is_fsynced_before_the_send(tmp_path, monkeypatch):
    import os
    from exo_deck import state as st
    events = []
    real = os.fsync

    def rec(fd):
        events.append("fsync")
        real(fd)

    monkeypatch.setattr(st, "_fsync", rec)
    p = item(tmp_path)

    class Rec(FakeRpc):
        def call(self, m, prm):
            if m == "eth_sendRawTransaction":
                events.append("send")
                assert "sending" in json.loads(p.read_text())
            return super().call(m, prm)

    run(p, Rec())
    i = events.index("send")
    assert events[:i].count("fsync") >= 2                    # temp file + directory, before the broadcast
    assert events[i + 1:].count("fsync") >= 2                # sent_tx is durable too


def test_failed_outcome_is_durable(tmp_path, monkeypatch):
    from exo_deck import state as st
    calls = []
    monkeypatch.setattr(st, "_fsync", lambda fd: calls.append(fd))
    run(item(tmp_path, salt="0x01"), FakeRpc())
    assert len(calls) >= 2


def _lock_is_free(state):
    import fcntl, os
    fd = os.open(state / ".hot-key.lock", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def test_report_and_speech_happen_after_the_key_lock_is_released(tmp_path):
    p = item(tmp_path)
    free = []

    class B(FakeBridge):
        def report_executed(self, pid, tx_hash=None, error=None):
            free.append(_lock_is_free(tmp_path))
            return super().report_executed(pid, tx_hash, error)

    class Peek(FakeRpc):
        def call(self, m, prm):
            if m == "eth_sendRawTransaction":
                free.append(_lock_is_free(tmp_path))          # sanity: held during the broadcast
            return super().call(m, prm)

    run(p, Peek(), B())
    assert free == [False]                          # a broadcast reports nothing
    run_hook(p, None, Peek(receipt={"status": "0x1"}), B(), MODULE, lambda s: None, now=lambda: NOW, resume_only=True)
    assert free == [False, True]                    # the settle report runs after the lock is released
    spoken_free = []
    p2 = tmp_path / "tx-approved" / "2_x.json"
    p2.write_text(json.dumps(dict(json.loads(p.read_text()), salt="0x01", sent_tx=None, sending=None, reported=None)))
    run_hook(p2, FakeSigner(), FakeRpc(), FakeBridge(), MODULE, lambda s: spoken_free.append(_lock_is_free(tmp_path)),
             now=lambda: NOW)
    assert spoken_free == [True]


def test_sweep_completes_a_marker_without_signing(tmp_path):
    from exo_deck.approve_hook import sweep
    p = item(tmp_path, sending={"tx_hash": TXH, "raw": "0x" + RAW.hex()})
    fresh = tmp_path / "tx-approved" / "2_fresh.json"
    fresh.write_text(json.dumps({"id": "fresh", "to": "0x" + "22" * 20, "value": "0", "data": "0x",
                                 "salt": "0x" + "00" * 32, "chain": "ethereum"}))
    rpc, br, spoken = FakeRpc(receipt={"status": "0x1"}), FakeBridge(), []
    t = [NOW]
    tried = {}
    assert sweep(tmp_path, rpc, br, MODULE, spoken.append, now=lambda: t[0], last_try=tried) == [PID]
    assert json.loads(p.read_text())["sent_tx"] == TXH and br.reports == [(PID, TXH, None)]
    assert rpc.sent == [] and "sending" not in json.loads(fresh.read_text()) and spoken == []
    assert sweep(tmp_path, rpc, br, MODULE, spoken.append, now=lambda: t[0], last_try=tried) == []   # done


def test_sweep_retries_unsure_quietly_and_throttled(tmp_path):
    from exo_deck.approve_hook import sweep

    class Down(FakeRpc):
        def call(self, m, prm):
            if m == "eth_getTransactionReceipt":
                raise RpcError("network: down")
            return super().call(m, prm)

    item(tmp_path, sending={"tx_hash": TXH, "raw": "0x" + RAW.hex()})
    t, tried, spoken = [NOW], {}, []
    assert sweep(tmp_path, Down(), FakeBridge(), MODULE, spoken.append, now=lambda: t[0], last_try=tried) == [PID]
    assert sweep(tmp_path, Down(), FakeBridge(), MODULE, spoken.append, now=lambda: t[0], last_try=tried) == []
    t[0] += 31
    assert sweep(tmp_path, Down(), FakeBridge(), MODULE, spoken.append, now=lambda: t[0], last_try=tried) == [PID]
    assert spoken == []


def test_sweep_retries_an_unsent_report(tmp_path):
    from exo_deck.approve_hook import sweep
    p = item(tmp_path, sent_tx=TXH, settled="executed")
    br = FakeBridge()
    assert sweep(tmp_path, FakeRpc(), br, MODULE, lambda s: None, now=lambda: NOW) == [PID]
    assert br.reports == [(PID, TXH, None)] and json.loads(p.read_text())["reported"] is True


# ---- settling a broadcast tx from its receipt (I2) -----------------------------------------------------------


def _settle(tmp_path, rpc, now=NOW, br=None):
    from exo_deck.approve_hook import sweep
    br, spoken = br or FakeBridge(), []
    sweep(tmp_path, rpc, br, MODULE, spoken.append, now=lambda: now)
    return br, spoken


def test_settle_status_1_reports_executed(tmp_path):
    p = item(tmp_path, sent_tx=TXH)
    br, spoken = _settle(tmp_path, FakeRpc(receipt={"status": "0x1"}))
    it = json.loads(p.read_text())
    assert br.reports == [(PID, TXH, None)] and it["settled"] == "executed" and it["reported"] is True and spoken == []
    assert not approve_hook.needs_recovery(it)


def test_settle_status_0_reports_failed_and_marks_it_failed(tmp_path):
    p = item(tmp_path, sent_tx=TXH)
    br, spoken = _settle(tmp_path, FakeRpc(receipt={"status": "0x0"}))
    it = json.loads(p.read_text())
    assert br.reports == [(PID, None, "reverted onchain")] and it["failed"] == "reverted onchain" and it["reported"] is True
    assert "settled" not in it and spoken == []            # deck-confirm announces the mined failure
    assert not approve_hook.needs_recovery(it)


def test_settle_waits_while_pending_or_unknown_before_expiry(tmp_path):
    p = item(tmp_path, sent_tx=TXH)
    for rpc in (FakeRpc(known={"hash": TXH}), FakeRpc(), FakeRpc(known={"hash": TXH}), ):
        br, spoken = _settle(tmp_path, rpc, now=NOW + 300 + approve_hook.DROP_GRACE_S)   # at the grace edge: wait
        assert br.reports == [] and spoken == []
    assert approve_hook.needs_recovery(json.loads(p.read_text()))
    # in the mempool long past expiry is still pending, not dropped
    br, _ = _settle(tmp_path, FakeRpc(known={"hash": TXH}), now=NOW + 10_000)
    assert br.reports == []


def test_settle_dropped_after_expiry_reports_failed_and_speaks(tmp_path):
    p = item(tmp_path, sent_tx=TXH)
    br, spoken = _settle(tmp_path, FakeRpc(), now=NOW + 300 + approve_hook.DROP_GRACE_S + 1)
    it = json.loads(p.read_text())
    assert br.reports == [(PID, None, "dropped")] and it["failed"] == "dropped"
    assert spoken == ["That payment failed: Send 20 USDC. It never made it onchain."]
    assert not any(s.startswith("Confirmed") for s in spoken)


def test_settle_rpc_trouble_waits(tmp_path):
    class Down(FakeRpc):
        def call(self, m, prm):
            if m == "eth_getTransactionReceipt":
                raise RpcError("network: down")
            return super().call(m, prm)
    p = item(tmp_path, sent_tx=TXH)
    br, spoken = _settle(tmp_path, Down(), now=NOW + 10_000)
    assert br.reports == [] and spoken == [] and "failed" not in json.loads(p.read_text())


def test_the_key_hook_never_settles_or_rebroadcasts_a_sent_item(tmp_path):
    p = item(tmp_path, sent_tx=TXH)
    rpc, br = FakeRpc(receipt={"status": "0x1"}), FakeBridge()
    assert run(p, rpc, br) is None and br.reports == [] and rpc.sent == []
