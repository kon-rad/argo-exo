import subprocess
import shutil

import pytest
from exo_nownodes.rpc import RpcError
from exo_presale.chain import SEL, Sale, SaleUnavailable

CONTRACT = "0x" + "aa" * 20
ZERO = "0x" + "00" * 20


class FakeRpc:
    def __init__(self, prices=(499_000_000, 0), paused=False, minted=41, max_supply=500, fail=None):
        self.prices, self.paused, self.minted, self.max_supply, self.calls = prices, paused, minted, max_supply, 0
        self.fail, self.seen = fail, []

    def batch(self, calls):
        self.calls += 1
        self.seen.append(calls)
        if self.fail:
            raise RpcError(self.fail)
        word = lambda n: "0x" + hex(n)[2:].rjust(64, "0")
        out = [word(int(self.paused)), word(self.minted), word(self.max_supply)]
        return out + [word(p) for p in self.prices]

    def call(self, method, params):
        self.calls += 1
        if self.fail:
            raise RpcError(self.fail)
        if params[0]["data"].startswith("0x6352211e"):        # ownerOf(uint256)
            if params[0]["data"].endswith(hex(99)[2:].rjust(64, "0")):
                raise RpcError("eth_call: execution reverted")
            return "0x" + "00" * 12 + "bb" * 20


def test_sale_state_open_and_cached():
    rpc, now = FakeRpc(), [1000.0]
    s = Sale(rpc, CONTRACT, [1, 2], ttl_s=30, clock=lambda: now[0])
    st = s.state()
    assert st == {"open": True, "paused": False, "minted": 41, "max_supply": 500,
                  "tiers": [{"tier": 1, "price_units": 499_000_000, "price": "499.00"}, {"tier": 2, "price_units": 0, "price": None}]}
    s.state(); assert rpc.calls == 1
    now[0] += 31; s.state(); assert rpc.calls == 2


def test_sale_state_closed():
    assert Sale(FakeRpc(prices=(0, 0)), CONTRACT, [1, 2]).state()["open"] is False
    assert Sale(FakeRpc(paused=True), CONTRACT, [1, 2]).state()["open"] is False
    assert Sale(FakeRpc(minted=500), CONTRACT, [1, 2]).state()["open"] is False
    # Not deployed yet (zero address in preorder.json): closed, no RPC call, no error.
    rpc = FakeRpc()
    st = Sale(rpc, ZERO, [1, 2]).state()
    assert st["open"] is False and rpc.calls == 0
    assert [t["price"] for t in st["tiers"]] == [None, None]


def test_sale_state_rpc_failure_raises_unavailable_not_cached():
    rpc = FakeRpc(fail="network: boom")
    s = Sale(rpc, CONTRACT, [1, 2])
    with pytest.raises(SaleUnavailable):
        s.state()
    rpc.fail = None
    assert s.state()["open"] is True


def test_price_formatting():
    st = Sale(FakeRpc(prices=(1_234_567, 5)), CONTRACT, [1, 2]).state()
    assert [t["price"] for t in st["tiers"]] == ["1.23", "0.00"]


def test_batch_calls_target_contract_with_tier_arg():
    rpc = FakeRpc()
    Sale(rpc, CONTRACT, [1, 2]).state()
    calls = rpc.seen[0]
    assert all(m == "eth_call" and p[0]["to"] == CONTRACT and p[1] == "latest" for m, p in calls)
    assert calls[3][1][0]["data"] == SEL["price"] + "00" * 31 + "01"


def test_owner_of():
    s = Sale(FakeRpc(), CONTRACT, [1])
    assert s.owner_of(1) == "0x" + "bb" * 20 and s.owner_of(99) is None
    assert Sale(FakeRpc(), ZERO, [1]).owner_of(1) is None


def test_owner_of_is_fresh_not_cached():
    rpc = FakeRpc()
    s = Sale(rpc, CONTRACT, [1])
    s.owner_of(1); s.owner_of(1)
    assert rpc.calls == 2


def test_owner_of_network_failure_is_unavailable_not_none():
    with pytest.raises(SaleUnavailable):
        Sale(FakeRpc(fail="network: down"), CONTRACT, [1]).owner_of(1)


def test_selectors():
    assert SEL == {"paused": "0x5c975abb", "totalMinted": "0xa2309ff8", "maxSupply": "0xd5abeb01",
                   "price": "0xb7fafcd7", "ownerOf": "0x6352211e"}
    if shutil.which("cast"):
        for name, sig in {"paused": "paused()", "totalMinted": "totalMinted()", "maxSupply": "maxSupply()",
                          "price": "price(uint8)", "ownerOf": "ownerOf(uint256)"}.items():
            assert subprocess.run(["cast", "sig", sig], capture_output=True, text=True).stdout.strip() == SEL[name]
