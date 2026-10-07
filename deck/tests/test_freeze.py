from exo_deck.bridge_client import BridgeError
from exo_deck.chord import ChordState
from exo_deck.freeze import run_freeze
from exo_nownodes.rpc import RpcError

MODULE = "0x" + "44" * 20


def word(n):
    return "0x" + n.to_bytes(32, "big").hex()


class Rpc:
    def __init__(self, frozen=0):
        self.frozen, self.sent = frozen, []

    def call(self, m, p):
        if m == "eth_call":
            assert p[0]["data"] == "0x054f7d9c"
            return word(self.frozen)
        if m == "eth_sendRawTransaction":
            self.sent.append(p[0])
            return "0x" + "ab" * 32
        return {"eth_getTransactionCount": "0x3", "eth_estimateGas": "0x7530", "eth_maxPriorityFeePerGas": "0x1",
                "eth_getBlockByNumber": {"baseFeePerGas": "0x1"}, "eth_chainId": "0x1"}[m]


class Signer:
    address = "0x" + "dd" * 20

    def __init__(self):
        self.signed = []

    def sign(self, tx):
        self.signed.append(tx)
        return b"\x02f"


class Bridge:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def freeze(self, reason):
        self.calls.append(reason)
        if self.fail:
            raise BridgeError("down")
        return {"ok": True}


def test_freeze_sends_freeze_and_marks_state(tmp_path):
    rpc, s, b, spoken = Rpc(), Signer(), Bridge(), []
    assert run_freeze(tmp_path, s, rpc, b, MODULE, spoken.append) is True
    (tx,) = s.signed
    assert tx["data"] == bytes.fromhex("62a5af3b") and tx["to"].lower() == MODULE and tx["value"] == 0
    assert rpc.sent == ["0x" + b"\x02f".hex()] and (tmp_path / "frozen").exists()
    assert b.calls == ["panic"] and "Frozen" in spoken[0]


def test_bridge_outage_never_blocks_the_local_freeze(tmp_path):
    rpc, spoken = Rpc(), []
    assert run_freeze(tmp_path, Signer(), rpc, Bridge(fail=True), MODULE, spoken.append) is True
    assert len(rpc.sent) == 1 and (tmp_path / "frozen").exists()


def test_already_frozen_sends_nothing(tmp_path):
    rpc, s, spoken = Rpc(frozen=1), Signer(), []
    assert run_freeze(tmp_path, s, rpc, Bridge(), MODULE, spoken.append) is True
    assert s.signed == [] and rpc.sent == [] and "already" in spoken[0].lower()


def test_frozen_check_failure_still_tries_to_freeze(tmp_path):
    class R(Rpc):
        def call(self, m, p):
            if m == "eth_call":
                raise RpcError("network: down")
            return super().call(m, p)

    rpc = R()
    assert run_freeze(tmp_path, Signer(), rpc, Bridge(), MODULE, lambda s: None) is True and len(rpc.sent) == 1


def test_send_failure_says_use_the_cold_key(tmp_path):
    class R(Rpc):
        def call(self, m, p):
            if m == "eth_estimateGas":
                raise RpcError("network: down")
            return super().call(m, p)

    spoken, b = [], Bridge(fail=True)
    assert run_freeze(tmp_path, Signer(), R(), b, MODULE, spoken.append) is False
    assert not (tmp_path / "frozen").exists() and "cold" in spoken[-1].lower()
    assert b.calls == ["panic"]                    # the CRE freeze is still asked for


def test_failed_local_freeze_speaks_the_bridge_outcome(tmp_path):
    class R(Rpc):
        def call(self, m, p):
            if m == "eth_estimateGas":
                raise RpcError("network: down")
            return super().call(m, p)

    spoken = []
    assert run_freeze(tmp_path, Signer(), R(), Bridge(), MODULE, spoken.append) is True   # CRE froze it
    assert "Guardian froze" in spoken[-1] and "cold key unfreezes" in spoken[-1]

    class DryBridge(Bridge):
        def freeze(self, reason):
            self.calls.append(reason)
            return {"ok": False}                   # answered, but nothing written onchain (dry run)
    spoken = []
    assert run_freeze(tmp_path, None, Rpc(), DryBridge(), MODULE, spoken.append) is False
    assert "couldn't freeze it either" in spoken[-1] and "cold key" in spoken[-1]


def test_freeze_without_signer_still_asks_the_bridge(tmp_path):
    spoken, b = [], Bridge(fail=True)
    assert run_freeze(tmp_path, None, Rpc(), b, MODULE, spoken.append) is False
    assert b.calls == ["panic"] and "cold" in spoken[-1].lower() and "locked" in spoken[0]
    spoken, b = [], Bridge()
    assert run_freeze(tmp_path, None, Rpc(), b, MODULE, spoken.append) is True and "Guardian froze" in spoken[-1]


def test_freeze_moves_every_queued_approval_to_expired(tmp_path):
    import json
    (tmp_path / "tx-queue").mkdir()
    for n in ("001_a.json", "002_b.json"):
        (tmp_path / "tx-queue" / n).write_text(json.dumps({"id": n}))
    assert run_freeze(tmp_path, Signer(), Rpc(), Bridge(), MODULE, lambda t: None) is True
    assert sorted(p.name for p in (tmp_path / "tx-expired").iterdir()) == ["001_a.json", "002_b.json"]
    assert list((tmp_path / "tx-queue").glob("*.json")) == []
    (tmp_path / "tx-queue" / "003_c.json").write_text("{}")   # even when nothing could be frozen
    run_freeze(tmp_path, None, Rpc(), Bridge(fail=True), MODULE, lambda t: None)
    assert (tmp_path / "tx-expired" / "003_c.json").exists()


# ---- the two-button chord (pure; deck-buttons is a thin caller) ---------------------------------------------


def test_chord_fires_once_after_two_seconds_of_both():
    c = ChordState(hold_s=2.0)
    c.key_down(0.0, mic=True)
    assert c.poll(1.9, key=True, mic=True) is False
    assert c.poll(2.0, key=True, mic=True) is True
    assert c.poll(3.0, key=True, mic=True) is False            # once per hold
    assert c.key_up(3.1, mic=True) is False                    # never approves after a chord


def test_chord_resets_when_either_button_lets_go():
    c = ChordState(hold_s=2.0)
    c.key_down(0.0, mic=True)
    assert c.poll(1.5, key=True, mic=False) is False
    assert c.poll(1.6, key=True, mic=True) is False
    assert c.poll(3.5, key=True, mic=True) is False             # timer restarted at 1.6
    assert c.poll(3.6, key=True, mic=True) is True


def test_plain_press_approves_on_release():
    c = ChordState()
    c.key_down(0.0, mic=False)
    assert c.poll(0.1, key=True, mic=False) is False
    assert c.key_up(0.2, mic=False) is True


def test_mic_touched_during_the_press_cancels_the_approval():
    c = ChordState()
    c.key_down(0.0, mic=False)
    c.poll(0.3, key=True, mic=True)                             # Mic joined: this is a panic attempt, not an approve
    assert c.key_up(0.5, mic=False) is False
    c.key_down(1.0, mic=False)
    assert c.key_up(1.2, mic=True) is False                     # Mic down at release
    c.key_down(2.0, mic=False)
    assert c.key_up(2.1, mic=False) is True                     # next clean press approves again


def test_key_up_without_key_down_does_nothing():
    assert ChordState().key_up(1.0, mic=False) is False


def test_freeze_bids_twice_the_normal_fees(tmp_path):
    from exo_deck.signer import build_tx
    s = Signer()
    run_freeze(tmp_path, s, Rpc(), Bridge(), MODULE, lambda t: None)
    normal = build_tx(Rpc(), s.address, MODULE, bytes.fromhex("62a5af3b"))
    (tx,) = s.signed
    assert tx["maxPriorityFeePerGas"] >= 2 * normal["maxPriorityFeePerGas"]
    assert tx["maxFeePerGas"] >= 2 * normal["maxFeePerGas"] and tx["nonce"] == normal["nonce"]


def test_freeze_may_exceed_the_normal_ceiling_up_to_its_own(tmp_path):
    from exo_deck import signer
    class Pricey(Rpc):
        def call(self, m, p):
            if m == "eth_getBlockByNumber":
                return {"baseFeePerGas": hex(100 * 10**9)}      # normal: 200 gwei (<= 300), freeze: 400 gwei
            return super().call(m, p)
    s = Signer()
    assert run_freeze(tmp_path, s, Pricey(), Bridge(), MODULE, lambda t: None) is True
    assert s.signed[0]["maxFeePerGas"] > signer.MAX_FEE_WEI

    class Absurd(Rpc):
        def call(self, m, p):
            if m == "eth_getBlockByNumber":
                return {"baseFeePerGas": hex(10**13)}
            return super().call(m, p)
    s2 = Signer()                                               # past its own ceiling: clamped to it, never refused
    assert run_freeze(tmp_path / "b", s2, Absurd(), Bridge(fail=True), MODULE, lambda t: None) is True
    assert s2.signed[0]["maxFeePerGas"] == signer.FREEZE_MAX_FEE_WEI
    assert s2.signed[0]["maxPriorityFeePerGas"] <= s2.signed[0]["maxFeePerGas"]


def test_freeze_takes_the_latest_nonce_and_outbids_an_execute_in_flight(tmp_path):
    """An execute is pending at nonce 3 (pending count 4, latest count 3): the freeze must reuse nonce 3 with at
    least 2x its fees so it replaces the execute, rather than queue at nonce 4 behind it."""
    from exo_deck.signer import build_tx
    calls = []

    class InFlight(Rpc):
        def call(self, m, p):
            calls.append((m, p))
            if m == "eth_getTransactionCount":
                return {"pending": "0x4", "latest": "0x3"}[p[1]]
            return super().call(m, p)
    normal = build_tx(InFlight(), Signer.address, MODULE, b"\x88")       # an approve takes the next free nonce
    assert normal["nonce"] == 4 and ("eth_getTransactionCount", [Signer.address, "pending"]) in calls
    in_flight = dict(normal, nonce=3)                                   # the execute already in the mempool
    s = Signer()
    assert run_freeze(tmp_path, s, InFlight(), Bridge(), MODULE, lambda t: None) is True
    (tx,) = s.signed
    assert tx["nonce"] == 3 and ("eth_getTransactionCount", [Signer.address, "latest"]) in calls
    assert tx["maxFeePerGas"] >= 2 * in_flight["maxFeePerGas"]
    assert tx["maxPriorityFeePerGas"] >= 2 * in_flight["maxPriorityFeePerGas"]


def test_mic_tap_shorter_than_a_tick_cancels_the_approve():
    c = ChordState()
    c.key_down(0.0, mic=False)
    c.poll(0.1, key=True, mic=False)
    assert c.mic_down(0.12, key=True) is False                 # press callback; released before the next tick
    c.poll(0.2, key=True, mic=False)
    assert c.key_up(0.4, mic=False) is False


def test_mic_down_can_complete_the_chord_timer():
    c = ChordState(hold_s=2.0)
    c.key_down(0.0, mic=False)
    c.mic_down(0.5, key=True)
    assert c.poll(2.4, key=True, mic=True) is False and c.poll(2.5, key=True, mic=True) is True
