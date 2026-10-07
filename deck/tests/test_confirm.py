import asyncio
import json

from exo_deck.confirm import Announcer, _poll_frozen

OK = {"ethereumSpecific": {"status": 1}}
FAILED = {"ethereumSpecific": {"status": 0}}


def test_announces_known_summary_and_logs(tmp_path):
    (tmp_path / "tx-approved").mkdir()
    (tmp_path / "tx-approved" / "1_a.json").write_text(json.dumps({"summary": "Send 20 USDC to mira.eth", "sent_tx": "0xaa"}))
    spoken = []
    a = Announcer(tmp_path, spoken.append, frozen_check=lambda: False, module="0xmod")
    assert a.handle({"txid": "0xaa", "confirmations": 1, "vin": [{"addresses": ["0xsafe"]}], "vout": [{"addresses": ["0xmod"]}], **OK}) == "Confirmed: Send 20 USDC to mira.eth"
    assert spoken == ["Confirmed: Send 20 USDC to mira.eth"]
    line = json.loads((tmp_path / "confirmations.jsonl").read_text().splitlines()[0])
    assert line["tx"] == "0xaa" and line["chain"] == "ethereum" and line["status"] == 1   # §4.2: {ts, chain, tx, summary}
    assert set(line) >= {"ts", "chain", "tx", "summary"}


def test_status_0_is_a_failure_never_confirmed(tmp_path):
    (tmp_path / "tx-approved").mkdir()
    (tmp_path / "tx-approved" / "1_a.json").write_text(json.dumps({"summary": "Send 20 USDC to mira.eth", "sent_tx": "0xaa"}))
    spoken = []
    a = Announcer(tmp_path, spoken.append, frozen_check=lambda: False, module="0xmod")
    assert a.handle({"txid": "0xaa", "confirmations": 1, **FAILED}) == "That payment failed: Send 20 USDC to mira.eth"
    assert spoken == ["That payment failed: Send 20 USDC to mira.eth"] and not any("Confirmed" in s for s in spoken)
    assert json.loads((tmp_path / "confirmations.jsonl").read_text())["status"] == 0


def test_receipt_status_is_used_when_blockbook_does_not_say(tmp_path):
    spoken, asked = [], []

    def receipt(txid):
        asked.append(txid)
        return 0
    a = Announcer(tmp_path, spoken.append, frozen_check=lambda: False, module="0xmod", receipt_status=receipt)
    a.handle({"txid": "0xbbbbbbbbbbbbbbbbbbbb", "confirmations": 1, "ethereumSpecific": {"status": -1}})
    assert asked == ["0xbbbbbbbbbbbbbbbbbbbb"] and spoken == ["That payment failed: transaction 0xbbbb…bbbb"]

    def boom(txid):
        raise RuntimeError("rpc down")
    spoken.clear()
    Announcer(tmp_path, spoken.append, frozen_check=lambda: False, module="0xmod", receipt_status=boom).handle(
        {"txid": "0xcc", "confirmations": 1})
    assert spoken == ["Mined: transaction 0xcc…0xcc. I couldn't read whether it succeeded."]
    assert not any(s.startswith("Confirmed") for s in spoken)


def test_unknown_tx_and_frozen_flag(tmp_path):
    spoken = []
    a = Announcer(tmp_path, spoken.append, frozen_check=lambda: True, module="0xmod")
    a.handle({"txid": "0xbbbbbbbbbbbbbbbbbbbb", "confirmations": 2, "vout": [{"addresses": ["0xMOD"]}], **OK})
    assert spoken == ["Confirmed: transaction 0xbbbb…bbbb"] and (tmp_path / "frozen").exists()


def test_unfreeze_clears_flag_and_rpc_failure_keeps_it(tmp_path):
    (tmp_path / "frozen").write_text("1")
    tx = {"txid": "0xcc", "blockHeight": 7, "vout": [{"addresses": ["0xmod"]}], **OK}
    Announcer(tmp_path, lambda t: None, frozen_check=lambda: False, module="0xmod").handle(tx)
    assert not (tmp_path / "frozen").exists()
    (tmp_path / "frozen").write_text("1")

    def boom():
        raise RuntimeError("rpc down")
    assert Announcer(tmp_path, lambda t: None, frozen_check=boom, module="0xmod").handle(tx)
    assert (tmp_path / "frozen").exists()


def test_any_confirmation_syncs_frozen_even_without_the_module_address(tmp_path):
    """The CRE freeze goes to the forwarder, not the module address: frozen() is read on every confirmation."""
    checks = []

    def frozen():
        checks.append(1)
        return True
    a = Announcer(tmp_path, lambda t: None, frozen_check=frozen, module="0xmod")
    a.handle({"txid": "0xdd", "confirmations": 1, "vout": [{"addresses": ["0xother"]}], **OK})
    assert checks and (tmp_path / "frozen").exists()


def test_frozen_is_polled_on_a_timer(tmp_path):
    state = {"frozen": True}
    a = Announcer(tmp_path, lambda t: None, frozen_check=lambda: state["frozen"], module="0xmod")

    async def go():
        task = asyncio.create_task(_poll_frozen(a, every_s=0.01))
        await asyncio.sleep(0.05)
        assert (tmp_path / "frozen").exists()
        state["frozen"] = False
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(go())
    assert not (tmp_path / "frozen").exists()


def test_mempool_notice_is_silent(tmp_path):
    spoken = []
    a = Announcer(tmp_path, spoken.append, frozen_check=lambda: True, module="0xmod")
    for tx in ({"txid": "0xee", "confirmations": 0, "blockHeight": -1}, {"txid": "0xee", "blockHeight": 0, "vout": [{"addresses": ["0xmod"]}]}):
        assert a.handle(tx) is None
    assert spoken == [] and not (tmp_path / "confirmations.jsonl").exists() and not (tmp_path / "frozen").exists()
