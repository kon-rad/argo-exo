import importlib.util
import json
from pathlib import Path

import pytest
import requests

SKILL = Path(__file__).resolve().parents[2] / "skills" / "exo-wallet"
spec = importlib.util.spec_from_file_location("wallet", SKILL / "wallet.py")
wallet = importlib.util.module_from_spec(spec); spec.loader.exec_module(wallet)

TOKENS = {"USDC": {"address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "decimals": 6}}
BOOK = {"0x000000000000000000000000000000000000bbbb": "mira.eth"}
SAFE = "0x" + "aa" * 20
ENV = {"EXO_BRIDGE_URL": "http://bridge.invalid", "EXO_BRIDGE_TOKEN": "tok-secret", "EXO_SAFE": SAFE}


def test_send_request_resolves_label_and_amount():
    req = wallet.send_request("USDC", "20", "mira.eth", "lunch", "voice", TOKENS, BOOK, safe=SAFE)
    assert req["tx"]["to"] == TOKENS["USDC"]["address"] and req["tx"]["value"] == "0"
    assert req["tx"]["data"].startswith("0xa9059cbb") and req["tx"]["data"].endswith(hex(20_000_000)[2:].rjust(64, "0"))
    assert req["intent"] == {"kind": "send", "summary": "lunch", "token": TOKENS["USDC"]["address"], "amount": "20",
                             "to": "0x000000000000000000000000000000000000bbbb"}
    assert "proposal_id" not in req and "salt" not in req["tx"] and req["source"] == "voice"


def test_eth_send_and_unknown_label():
    req = wallet.send_request("ETH", "0.01", "0x" + "cc" * 20, "x", "voice", TOKENS, BOOK, safe=SAFE)
    assert req["tx"]["value"] == str(10**16) and req["tx"]["data"] == "0x"
    with pytest.raises(ValueError, match="address book"):
        wallet.send_request("USDC", "1", "bob.eth", "x", "voice", TOKENS, BOOK, safe=SAFE)


@pytest.mark.parametrize("amt", ["1.0000001", "0", "-1", "1e3", "abc", "", "0.0000000"])
def test_bad_amounts_rejected(amt):
    with pytest.raises(ValueError):
        wallet.send_request("USDC", amt, "mira.eth", "x", "voice", TOKENS, BOOK, safe=SAFE)


def test_exact_decimal_no_float_drift():
    req = wallet.send_request("USDC", "0.1", "mira.eth", "x", "voice", TOKENS, BOOK, safe=SAFE)
    assert int(req["tx"]["data"][-64:], 16) == 100_000
    big = wallet.send_request("ETH", "9007199254.740993", "0x" + "cc" * 20, "x", "voice", TOKENS, BOOK, safe=SAFE)
    assert big["tx"]["value"] == "9007199254740993000000000000"


def test_address_and_source_validation():
    with pytest.raises(ValueError):
        wallet.send_request("USDC", "1", "0x1234", "x", "voice", TOKENS, BOOK, safe=SAFE)
    with pytest.raises(ValueError, match="source"):
        wallet.send_request("USDC", "1", "mira.eth", "x", "camera", TOKENS, BOOK, safe=SAFE)
    wallet.send_request("USDC", "1", "mira.eth", "x", "agent:trader", TOKENS, BOOK, safe=SAFE)
    good = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    with pytest.raises(ValueError, match="checksum"):  # one letter's case flipped
        wallet.send_request("ETH", "1", good.replace("F3E", "f3E"), "x", "voice", TOKENS, BOOK, safe=SAFE)
    ok = wallet.send_request("ETH", "1", good, "x", "voice", TOKENS, BOOK, safe=SAFE)
    assert ok["tx"]["to"] == good.lower()


def test_raw_request():
    req = wallet.raw_request("0x" + "dd" * 20, "5", "0xABcd", "poke", "agent:trader", SAFE)
    assert req["intent"] == {"kind": "raw", "summary": "poke"}
    assert req["tx"] == {"chain_id": 1, "to": "0x" + "dd" * 20, "value": "5", "data": "0xabcd"}
    for bad in (("0x" + "dd" * 20, "1.5", "0x"), ("0x" + "dd" * 20, "1", "0xabc"), ("nope", "1", "0x")):
        with pytest.raises(ValueError):
            wallet.raw_request(*bad, "x", "voice", SAFE)


def test_spoken_result():
    assert wallet.spoken({"verdict": "approve", "explanation": "You pay 20 USDC to mira.eth. Nothing else changes."}) \
        == "You pay 20 USDC to mira.eth. Nothing else changes. Press the approve key to send it."
    assert wallet.spoken({"verdict": "refuse", "explanation": "Refused: came from the camera."}) == "Refused: came from the camera."
    assert "dry run" in wallet.spoken({"verdict": "approve", "explanation": "x.", "queued": False})


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        if self._b is None:
            raise ValueError
        return self._b


def _run(tmp_path, capsys, post, extra=()):
    book = tmp_path / "book.json"
    book.write_text(json.dumps(BOOK))
    env = dict(ENV, EXO_ADDRESS_BOOK=str(book))
    args = ["send", "--token", "USDC", "--amount", "20", "--to", "mira.eth", "--summary", "lunch", "--source", "voice"]
    code = wallet.main(args + list(extra), env=env, post=post)
    return code, capsys.readouterr()


def test_approve_through_fake_bridge(tmp_path, capsys):
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return FakeResp(200, {"verdict": "approve", "queued": True, "proposal_id": "p1",
                              "explanation": "You pay 20 USDC to mira.eth."})
    code, cap = _run(tmp_path, capsys, post)
    assert code == 0 and "Press the approve key" in cap.out
    assert seen["url"] == "http://bridge.invalid/guard" and seen["json"]["intent"]["kind"] == "send"
    assert "tok-secret" not in cap.out + cap.err


def test_refusal_prints_explanation(tmp_path, capsys):
    code, cap = _run(tmp_path, capsys, lambda *a, **k: FakeResp(200, {"verdict": "refuse", "explanation": "Refused: unknown recipient."}))
    assert code == 0 and cap.out.strip() == "Refused: unknown recipient."


def test_timeout_is_unknown_not_refused_and_not_retried(tmp_path, capsys):
    calls = []

    def post(*a, **k):
        calls.append(1)
        raise requests.ReadTimeout("boom tok-secret")
    code, cap = _run(tmp_path, capsys, post)
    assert code == 1 and len(calls) == 1
    assert "Outcome unknown" in cap.out and "approvals panel" in cap.out and "Do not retry" in cap.out
    assert "tok-secret" not in cap.out + cap.err


def test_unreachable_and_garbage_are_unknown(tmp_path, capsys):
    def boom(*a, **k):
        raise requests.ConnectionError("x")
    code, cap = _run(tmp_path, capsys, boom)
    assert code == 1 and "Outcome unknown" in cap.out
    code, cap = _run(tmp_path, capsys, lambda *a, **k: FakeResp(502, None))
    assert code == 1 and "Outcome unknown" in cap.out


def test_recorded_unavailable_is_a_refusal(tmp_path, capsys):
    code, cap = _run(tmp_path, capsys, lambda *a, **k: FakeResp(502, {"error": "guardian unavailable", "proposal_id": "p9"}))
    assert code == 1 and cap.out.startswith("Refused") and "do not retry" in cap.out


def test_bad_input_sends_nothing(tmp_path, capsys):
    def post(*a, **k):
        raise AssertionError("must not call the bridge")
    code, cap = _run(tmp_path, capsys, post, extra=["--to", "bob.eth"])
    assert code == 2 and "address book" in cap.out


def test_skill_holds_no_dsn_or_store_import():
    text = (SKILL / "wallet.py").read_text() + (SKILL / "SKILL.md").read_text()
    for needle in ("postgres", "exo_guardian.store", "import store", "DSN", "private key"):
        assert needle not in text
    assert json.loads((SKILL / "tokens.json").read_text())["USDC"]["decimals"] == 6
