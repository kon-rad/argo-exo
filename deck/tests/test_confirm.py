import json
from exo_deck.confirm import Announcer


def test_announces_known_summary_and_logs(tmp_path):
    (tmp_path / "tx-approved").mkdir()
    (tmp_path / "tx-approved" / "1_a.json").write_text(json.dumps({"summary": "Send 20 USDC to mira.eth", "sent_tx": "0xaa"}))
    spoken = []
    a = Announcer(tmp_path, spoken.append, frozen_check=lambda: False, module="0xmod")
    assert a.handle({"txid": "0xaa", "confirmations": 1, "vin": [{"addresses": ["0xsafe"]}], "vout": [{"addresses": ["0xmod"]}]}) == "Confirmed: Send 20 USDC to mira.eth"
    assert spoken == ["Confirmed: Send 20 USDC to mira.eth"]
    assert json.loads((tmp_path / "confirmations.jsonl").read_text().splitlines()[0])["tx"] == "0xaa"


def test_unknown_tx_and_frozen_flag(tmp_path):
    spoken = []
    a = Announcer(tmp_path, spoken.append, frozen_check=lambda: True, module="0xmod")
    a.handle({"txid": "0xbbbbbbbbbbbbbbbbbbbb", "confirmations": 2, "vout": [{"addresses": ["0xMOD"]}]})
    assert spoken == ["Confirmed: transaction 0xbbbb…bbbb"] and (tmp_path / "frozen").exists()


def test_unfreeze_clears_flag_and_rpc_failure_keeps_it(tmp_path):
    (tmp_path / "frozen").write_text("1")
    tx = {"txid": "0xcc", "blockHeight": 7, "vout": [{"addresses": ["0xmod"]}]}
    Announcer(tmp_path, lambda t: None, frozen_check=lambda: False, module="0xmod").handle(tx)
    assert not (tmp_path / "frozen").exists()
    (tmp_path / "frozen").write_text("1")

    def boom():
        raise RuntimeError("rpc down")
    assert Announcer(tmp_path, lambda t: None, frozen_check=boom, module="0xmod").handle(tx)
    assert (tmp_path / "frozen").exists()


def test_non_module_tx_leaves_frozen_alone(tmp_path):
    a = Announcer(tmp_path, lambda t: None, frozen_check=lambda: True, module="0xmod")
    a.handle({"txid": "0xdd", "confirmations": 1, "vout": [{"addresses": ["0xother"]}]})
    assert not (tmp_path / "frozen").exists()


def test_mempool_notice_is_silent(tmp_path):
    spoken = []
    a = Announcer(tmp_path, spoken.append, frozen_check=lambda: True, module="0xmod")
    for tx in ({"txid": "0xee", "confirmations": 0, "blockHeight": -1}, {"txid": "0xee", "blockHeight": 0, "vout": [{"addresses": ["0xmod"]}]}):
        assert a.handle(tx) is None
    assert spoken == [] and not (tmp_path / "confirmations.jsonl").exists() and not (tmp_path / "frozen").exists()
