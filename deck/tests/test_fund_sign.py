import json
from pathlib import Path

import pytest
from eth_account import Account

from exo_deck import fund_sign, fund_view
from exo_deck.bridge_client import BridgeError

VECTOR = json.loads((Path(__file__).resolve().parents[2] / "fund" / "tests" / "vectors" / "safe-tx.json").read_text())
KEY = "0x" + "07" * 32


def test_hash_matches_the_shared_vector():
    assert "0x" + fund_sign.safe_tx_hash(VECTOR["chain_id"], VECTOR["safe"], VECTOR["safe_tx"]).hex() == VECTOR["hash"]


def prep(**kw):
    p = {"id": "2026-10-14-1", "chain_id": 8453, "safe": VECTOR["safe"], "safe_tx": dict(VECTOR["safe_tx"]),
         "safe_tx_hash": VECTOR["hash"], "summary": "Sell 30 USDC"}
    p.update(kw)
    return p


def test_check_prepared_accepts_only_this_safe_on_base():
    assert fund_sign.check_prepared(prep(), VECTOR["safe"]).hex() == VECTOR["hash"][2:]
    with pytest.raises(fund_sign.FundError, match="another chain"):
        fund_sign.check_prepared(prep(chain_id=1), VECTOR["safe"])
    with pytest.raises(fund_sign.FundError, match="another chain"):
        fund_sign.check_prepared(prep(), "0x" + "99" * 20)
    with pytest.raises(fund_sign.FundError, match="hash mismatch"):        # bridge claims a hash for other fields
        fund_sign.check_prepared(prep(safe_tx=dict(VECTOR["safe_tx"], nonce="4")), VECTOR["safe"])
    with pytest.raises(fund_sign.FundError, match="refund"):
        fund_sign.check_prepared(prep(safe_tx=dict(VECTOR["safe_tx"], gas_price="1")), VECTOR["safe"])


class Signer:
    def __init__(self):
        self.acct = Account.from_key(KEY)

    def sign_hash(self, h):
        s = self.acct.unsafe_sign_hash(h)
        return s.r.to_bytes(32, "big") + s.s.to_bytes(32, "big") + bytes([s.v])


class FakeBridge:
    def __init__(self, props, prepared):
        self.props, self.prepared, self.approved = props, prepared, None

    def fund_proposals(self):
        return self.props

    def fund_prepare(self, pid):
        return self.prepared

    def fund_approve(self, pid, sig):
        self.approved = (pid, sig)
        return {"tx_hash": "0xabc", "headline": "Swapped 30 USDC for 0.008 WETH"}


def test_approve_top_signs_the_recomputed_hash():
    said = []
    b = FakeBridge([{"id": "2026-10-14-1", "status": "proposed"}], prep())
    assert fund_sign.approve_top(b, Signer(), VECTOR["safe"], said.append) == "0xabc"
    pid, sig = b.approved
    assert Account._recover_hash(bytes.fromhex(VECTOR["hash"][2:]), signature=bytes.fromhex(sig[2:])) == Signer().acct.address
    assert said == ["Done. Swapped 30 USDC for 0.008 WETH."]


def test_approve_top_with_nothing_waiting_signs_nothing():
    said = []
    b = FakeBridge([], prep())
    assert fund_sign.approve_top(b, Signer(), VECTOR["safe"], said.append) is None and b.approved is None


class ViewBridge:
    def __init__(self, props=None, exc=None):
        self.props, self.exc = props or [], exc

    def fund_proposals(self):
        if self.exc:
            raise self.exc
        return self.props

    def fund_log(self):
        return {"nav": "**NAV:** $100.00", "entries": ["### 2026-10-13 · Fund opened\n**Why:** start"], "thesis": "Core first."}


def test_panel_shapes_and_cleans():
    p = {"id": "2026-10-14-1", "motion": "Open ‮ETH", "tally": "3–1", "status": "proposed",
         "intent": {"sell": "USDC", "buy": "WETH", "amount": "30"},
         "votes": [{"partner": "Wren Hollis", "vote": "yes", "reason": "tide"}, {"partner": "X", "vote": "lol", "reason": ""}]}
    out = fund_view.panel(ViewBridge([p]), 0)
    assert out["proposal"]["trade"] == "Sell 30 USDC for WETH" and "‮" not in out["proposal"]["motion"]
    assert [v["vote"] for v in out["proposal"]["votes"]] == ["yes", "?"]
    assert out["log"] == ["2026-10-13 · Fund opened"] and out["thesis"] == "Core first."
    assert fund_view.panel(ViewBridge(exc=BridgeError("down")), 0) == {"error": "Fund unreachable"}


SAFE = "0x" + "12" * 20
SAVINGS = "0x" + "5a" * 20


def prepared(to, value, data, kind="withdraw", op=0):
    t = {"to": to, "value": str(value), "data": data, "operation": op, "nonce": "0"}
    return {"chain_id": 8453, "safe": SAFE, "kind": kind, "safe_tx": t,
            "safe_tx_hash": "0x" + fund_sign.safe_tx_hash(8453, SAFE, t).hex()}


def transfer(to):
    return "0xa9059cbb" + to[2:].rjust(64, "0") + f"{10**6:064x}"


def test_withdrawals_only_to_the_decks_savings_address():
    usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    fund_sign.check_prepared(prepared(usdc, 0, transfer(SAVINGS)), SAFE, SAVINGS)        # token to savings: ok
    fund_sign.check_prepared(prepared(SAVINGS, 10**15, "0x"), SAFE, SAVINGS)             # ETH to savings: ok
    for bad in (prepared(usdc, 0, transfer("0x" + "66" * 20)),                          # token to someone else
                prepared("0x" + "66" * 20, 10**15, "0x"),                               # ETH to someone else
                prepared(usdc, 0, transfer("0x" + "66" * 20), kind="swap")):            # mislabelled as a swap
        with pytest.raises(fund_sign.FundError, match="savings"):
            fund_sign.check_prepared(bad, SAFE, SAVINGS)
    with pytest.raises(fund_sign.FundError, match="savings"):                          # deck has no savings set
        fund_sign.check_prepared(prepared(usdc, 0, transfer(SAVINGS)), SAFE, None)


def test_lone_approvals_and_fake_withdrawals_are_refused():
    usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    approve = "0x095ea7b3" + ("66" * 20).rjust(64, "0") + f"{10**6:064x}"
    with pytest.raises(fund_sign.FundError, match="approval"):
        fund_sign.check_prepared(prepared(usdc, 0, approve, kind="swap"), SAFE, SAVINGS)
    with pytest.raises(fund_sign.FundError, match="single transfer"):                  # "withdraw" that's a batch
        fund_sign.check_prepared(prepared("0x9641d764fc13c8B624c04430C7356C1C7C8102e2", 0, "0x8d80ff0a", op=1), SAFE, SAVINGS)
