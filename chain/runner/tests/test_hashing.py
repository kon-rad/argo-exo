import pytest
import json
from pathlib import Path
from exo_guardian.hashing import approval_hash

V = json.loads((Path(__file__).resolve().parents[2] / "test-vectors" / "approval-hash.json").read_text())
ARGS = (V["chain_id"], V["module"], V["to"], int(V["value"]), bytes.fromhex(V["data"][2:]), bytes.fromhex(V["salt"][2:]))


def test_matches_cast_vector():
    assert "0x" + approval_hash(*ARGS).hex() == V["hash"]


def test_any_field_change_changes_the_hash():
    h0 = approval_hash(*ARGS)
    for i, alt in enumerate([8453, "0x" + "3" * 40, "0x" + "4" * 40, ARGS[3] + 1, b"\x00", b"\x01" * 32]):
        args = list(ARGS); args[i] = alt
        assert approval_hash(*args) != h0


def _with(i, v):
    a = list(ARGS); a[i] = v
    return a


def test_rejects_bad_salt_length():
    for s in (b"\x01" * 31, b"\x01" * 33, b""):
        with pytest.raises(ValueError):
            approval_hash(*_with(5, s))
    with pytest.raises(ValueError):
        approval_hash(*_with(5, "0x" + "11" * 32))


def test_rejects_bad_value_and_data():
    for v in (1.0, True, "1", None):
        with pytest.raises(TypeError):
            approval_hash(*_with(3, v))
    with pytest.raises(ValueError):
        approval_hash(*_with(3, -1))
    with pytest.raises(TypeError):
        approval_hash(*_with(4, "0xdead"))


def test_address_case_is_irrelevant():
    assert approval_hash(*_with(1, V["module"].lower())) == approval_hash(*ARGS)
    assert approval_hash(*_with(2, V["to"].lower())) == approval_hash(*ARGS)
