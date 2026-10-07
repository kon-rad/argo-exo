import json
from pathlib import Path

import pytest
from exo_deck.exo_module import (EXECUTE, FREEZE, ItemError, approval_hash, execute_calldata, freeze_calldata,
                                 parse_item, read_state)

VECTOR = Path(__file__).resolve().parents[2] / "chain" / "test-vectors" / "approval-hash.json"
MODULE = "0x" + "44" * 20


def word(n: int) -> str:
    return "0x" + n.to_bytes(32, "big").hex()


def test_hash_matches_shared_vector():
    v = json.loads(VECTOR.read_text())
    h = approval_hash(v["chain_id"], v["module"], v["to"], int(v["value"]), bytes.fromhex(v["data"][2:]),
                      bytes.fromhex(v["salt"][2:]))
    assert "0x" + h.hex() == v["hash"]


def test_selectors_match_cast_and_forge():
    # cast sig 'execute(address,uint256,bytes,bytes32)' = 0x88aa4c12 ; cast sig 'freeze()' = 0x62a5af3b
    assert EXECUTE.hex() == "88aa4c12" and FREEZE.hex() == "62a5af3b"


def test_execute_calldata_matches_cast():
    # cast calldata "execute(address,uint256,bytes,bytes32)" 0x2222…2222 0 0xa9059cbb 0x00…aa
    want = ("88aa4c12" + "00" * 12 + "22" * 20 + "00" * 32 + "%064x" % 0x80 + "00" * 31 + "aa"
            + "%064x" % 4 + "a9059cbb" + "00" * 28)
    got = execute_calldata("0x" + "22" * 20, 0, bytes.fromhex("a9059cbb"), bytes.fromhex("00" * 31 + "aa"))
    assert got.hex() == want
    assert freeze_calldata().hex() == "62a5af3b"


def good_item(**kw):
    it = {"id": "p1", "chain": "ethereum", "to": "0x" + "22" * 20, "value": "5", "data": "0xa9059cbb",
          "salt": "0x" + "00" * 31 + "aa"}
    it.update(kw)
    return it


def test_parse_item_accepts_a_well_formed_item():
    c = parse_item(good_item(), MODULE)
    assert c.value == 5 and c.data == bytes.fromhex("a9059cbb") and len(c.salt) == 32


@pytest.mark.parametrize("bad", [
    {"to": "0x1234"}, {"to": MODULE}, {"value": "-1"}, {"value": True}, {"value": "1.5"}, {"value": None},
    {"data": "a9059cbb"}, {"data": "0xabc"}, {"data": "0xzz"}, {"salt": "0x" + "00" * 31},
    {"salt": None}, {"chain": "base"}, {"id": ""}, {"id": None},
])
def test_parse_item_rejects_malformed_fields(bad):
    with pytest.raises(ItemError):
        parse_item(good_item(**bad), MODULE)


class Views:
    def __init__(self, until=0, used=0, frozen=0):
        self.r = {"bfc3b08f": word(until), "b07c411f": word(used), "054f7d9c": word(frozen)}

    def batch(self, calls):
        assert all(m == "eth_call" and p[1] == "latest" and p[0]["to"] == MODULE for m, p in calls)
        return [self.r[p[0]["data"][2:10]] for _, p in calls]


def test_read_state_parses_the_three_views():
    s = read_state(Views(until=1800000000, used=1, frozen=0), MODULE, b"\x11" * 32)
    assert (s.approved_until, s.used, s.frozen) == (1800000000, True, False)


def test_read_state_fails_closed_on_odd_words():
    v = Views()
    v.r["b07c411f"] = word(2)                      # a bool view returning 2 is not a bool
    with pytest.raises(ValueError):
        read_state(v, MODULE, b"\x11" * 32)
    v = Views()
    v.r["054f7d9c"] = "0x"                         # empty return (no contract at the address)
    with pytest.raises(ValueError):
        read_state(v, MODULE, b"\x11" * 32)
