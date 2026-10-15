import json
from pathlib import Path

import pytest
from eth_account import Account

from fund_trade import safe as sf

KEYS = ["0x" + f"{i:064x}" for i in (1, 2, 3)]
VECTOR = Path(__file__).parent / "vectors" / "safe-tx.json"


def test_hash_matches_the_shared_vector():
    """The deck checks the same vector (deck/tests/test_fund_sign.py), so both sides hash identically."""
    v = json.loads(VECTOR.read_text())
    h = sf.safe_tx_hash(v["chain_id"], v["safe"], sf.SafeTx.from_json(v["safe_tx"]))
    assert "0x" + h.hex() == v["hash"]


def test_sign_recover_roundtrip_and_ordering():
    h = bytes(range(32))
    sigs = {Account.from_key(k).address: sf.sign_hash(k, h) for k in KEYS[:2]}
    for addr, sig in sigs.items():
        assert sf.recover(h, sig) == addr and sig[64] in (27, 28)
    packed = sf.pack_signatures(sigs)
    first = min(sigs, key=lambda a: int(a, 16))
    assert packed[:65] == sigs[first] and len(packed) == 130


def test_recover_rejects_malformed():
    with pytest.raises(ValueError):
        sf.recover(b"\0" * 32, b"\0" * 64)


def test_batch_shapes():
    one = sf.batch([{"to": "0x" + "22" * 20, "value": 5, "data": "0x"}], 7)
    assert one.operation == sf.CALL and one.value == 5 and one.nonce == 7
    two = sf.batch([{"to": "0x" + "22" * 20, "value": 0, "data": "0xaabb"},
                    {"to": "0x" + "33" * 20, "value": 0, "data": "0x"}], 0)
    assert two.to == sf.MULTI_SEND_CALL_ONLY and two.operation == sf.DELEGATECALL
    with pytest.raises(ValueError):
        sf.batch([{"to": "0x" + "22" * 20, "value": 0, "data": "0x"}, {"to": "0x" + "33" * 20, "value": 1, "data": "0x"}], 0)


def test_multisend_packing():
    data = sf.multisend([{"to": "0x" + "22" * 20, "value": 1, "data": "0xaabb"}])
    raw = bytes.fromhex(data[2:])
    assert raw[:4] == sf.selector("multiSend(bytes)")
    packed = raw[4 + 64:]                                      # offset + length words
    assert packed[0] == 0 and packed[1:21] == b"\x22" * 20
    assert int.from_bytes(packed[21:53], "big") == 1 and int.from_bytes(packed[53:85], "big") == 2
    assert packed[85:87] == b"\xaa\xbb"


def test_setup_rejects_bad_owner_sets():
    with pytest.raises(ValueError):
        sf.setup_calldata(["0x" + "11" * 20, "0x" + "11" * 20], 2)
    with pytest.raises(ValueError):
        sf.setup_calldata(["0x" + "11" * 20], 2)
