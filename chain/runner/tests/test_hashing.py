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
