import json

from exo_deck import m4_approve
from exo_deck.fund_sign import safe_tx_hash

SAFE = "0x" + "11" * 20
SAVINGS = "0x" + "22" * 20
TX = {"to": "0x" + "33" * 20, "value": "0", "data": "0x1234", "operation": 0, "nonce": 5}


class FakeBridge:
    def __init__(self, safe_tx_hash_hex):
        self.h, self.approved, self.refused = safe_tx_hash_hex, None, None

    def fund_proposals(self):
        return [{"id": "2026-10-28-1", "status": "proposed", "tally": "3-1", "motion": "Buy ETH"}]

    def fund_prepare(self, pid):
        return {"chain_id": 8453, "safe": SAFE, "safe_tx": TX, "safe_tx_hash": self.h, "summary": "Sell 10 USDC for WETH"}

    def fund_approve(self, pid, sig):
        self.approved = sig
        return {"headline": "Swapped"}

    def fund_refuse(self, pid):
        self.refused = pid


def run(monkeypatch, tmp_path, bridge, answer, argv=()):
    from eth_account import Account
    pw = tmp_path / "pw"; pw.write_text("pw"); pw.chmod(0o600)
    ks = tmp_path / "hot.json"; ks.write_text(json.dumps(Account.encrypt(Account.create().key, "pw")))
    monkeypatch.setenv("EXO_FUND_SAFE", SAFE); monkeypatch.setenv("EXO_FUND_SAVINGS", SAVINGS)
    monkeypatch.setenv("EXO_HOT_KEYSTORE", str(ks)); monkeypatch.setenv("EXO_HOT_PASSFILE", str(pw))
    monkeypatch.setattr(m4_approve, "Bridge", lambda *a: bridge)
    lines = []
    return m4_approve.main(list(argv), ask=lambda _: answer, out=lines.append), "\n".join(lines)


def test_signs_only_after_typed_yes(monkeypatch, tmp_path):
    good = "0x" + safe_tx_hash(8453, SAFE, TX).hex()
    b = FakeBridge(good)
    assert run(monkeypatch, tmp_path, b, "no")[0] == 1 and b.approved is None
    code, _ = run(monkeypatch, tmp_path, b, "yes")
    assert code == 0 and len(b.approved) == 2 + 130


def test_wrong_hash_is_never_signed(monkeypatch, tmp_path):
    b = FakeBridge("0x" + "00" * 32)
    code, out = run(monkeypatch, tmp_path, b, "yes")
    assert code == 1 and b.approved is None and "hash mismatch" in out


def test_refuse(monkeypatch, tmp_path):
    b = FakeBridge("0x00")
    assert run(monkeypatch, tmp_path, b, "refuse", ["--refuse"])[0] == 0 and b.refused == "2026-10-28-1"
