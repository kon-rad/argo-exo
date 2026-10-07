import json

from exo_deck.exo_module import approval_hash
from exo_deck.queue_sync import OnchainCheck, sync_once
from exo_nownodes.rpc import RpcError

MODULE = "0x" + "44" * 20
NOW = 1_800_000_000
P1, P2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"


def word(n: int) -> str:
    return "0x" + n.to_bytes(32, "big").hex()


def qitem(pid=P1, **kw):
    it = {"id": pid, "summary": "Send 20 USDC", "explanation": "You pay 20 USDC.", "chain": "ethereum",
          "to": "0x" + "22" * 20, "value": "0", "data": "0xa9059cbb", "salt": "0x" + "00" * 31 + "aa",
          "verdict_tx": "0x" + "ab" * 32, "risk": "low", "auto_eligible": False, "expires_at": NOW + 600}
    it.update(kw)
    return it


def h_of(it, chain_id=1):
    return approval_hash(chain_id, MODULE, it["to"], int(it["value"]), bytes.fromhex(it["data"][2:]),
                         bytes.fromhex(it["salt"][2:]))


def accept(it):
    return dict(it)


def test_writes_new_items_once_and_skips_known(tmp_path):
    items = [qitem(P1, summary="a"), qitem(P2, summary="b")]
    (tmp_path / "tx-approved").mkdir(parents=True)
    (tmp_path / "tx-approved" / f"100_{P2}.json").write_text("{}")
    assert sync_once(tmp_path, items, accept) == [P1]
    assert sync_once(tmp_path, items, accept) == []
    files = list((tmp_path / "tx-queue").glob(f"*_{P1}.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["summary"] == "a"
    assert (tmp_path / "verdict").read_text().strip() == "approve"


def test_skips_ids_already_expired_or_rejected(tmp_path):
    for d, pid in (("tx-expired", P1), ("tx-rejected", P2)):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / f"5_{pid}.json").write_text("{}")
    assert sync_once(tmp_path, [qitem(P1), qitem(P2)], accept) == []
    assert not (tmp_path / "verdict").exists()


def test_verify_can_veto_and_rewrite(tmp_path):
    seen = []

    def verify(it):
        seen.append(it["id"])
        return None if it["id"] == P2 else dict(it, expires_at=NOW + 60)

    assert sync_once(tmp_path, [qitem(P1), qitem(P2)], verify) == [P1]
    (f,) = (tmp_path / "tx-queue").glob("*.json")
    assert json.loads(f.read_text())["expires_at"] == NOW + 60 and seen == [P1, P2]


def test_unsafe_ids_and_non_dicts_never_reach_disk(tmp_path):
    bad = [qitem("../../hooks/approve"), qitem("x/y"), qitem(""), "nope", qitem(P1, id=None)]
    assert sync_once(tmp_path, bad, accept) == []
    assert list((tmp_path / "tx-queue").iterdir()) == []


def test_verify_exception_skips_item(tmp_path):
    def boom(it):
        raise RuntimeError("rpc down")

    assert sync_once(tmp_path, [qitem(P1)], boom) == []


class Chain:
    """Fake NOWNodes for ExoModule's three views, keyed by approval hash."""

    def __init__(self, until=None, used=0, frozen=0, chain_id="0x1"):
        self.until, self.used, self.frozen, self.chain_id, self.calls = until or {}, used, frozen, chain_id, 0

    def call(self, m, p):
        assert m == "eth_chainId"
        return self.chain_id

    def batch(self, calls):
        self.calls += 1
        out = []
        for m, p in calls:
            d = p[0]["data"]
            sel, arg = d[2:10], d[10:]
            out.append({"bfc3b08f": lambda: word(self.until.get(arg, 0)), "b07c411f": lambda: word(self.used),
                        "054f7d9c": lambda: word(self.frozen)}[sel]())
        return out


def check(chain, **kw):
    return OnchainCheck(chain, MODULE, now=lambda: NOW, **kw)


def test_onchain_check_accepts_a_real_approval_and_takes_its_expiry():
    it = qitem()
    out = check(Chain(until={h_of(it).hex(): NOW + 300}))(it)
    assert out["expires_at"] == NOW + 300 and out["id"] == P1


def test_onchain_check_refuses_forged_or_spent_rows():
    it = qitem()
    h = h_of(it).hex()
    assert check(Chain())(it) is None                                         # no approval onchain (forged row)
    assert check(Chain(until={h: NOW - 1}))(it) is None                       # expired
    assert check(Chain(until={h: NOW}))(it) is None                           # approvedUntil must be > now
    assert check(Chain(until={h: NOW + 900}))(it) is None                     # outlives the item's expires_at
    assert check(Chain(until={h: NOW + 300}, used=1))(it) is None             # already executed
    assert check(Chain(until={h: NOW + 300}, frozen=1))(it) is None           # module frozen
    assert check(Chain(until={h: NOW + 300}, chain_id="0x5"))(it) is None     # wrong network
    assert check(Chain(until={h: NOW + 300}))(qitem(salt="0x00")) is None      # malformed field
    assert check(Chain(until={h: NOW + 300}))(qitem(to=MODULE)) is None        # self call
    assert check(Chain(until={h: NOW + 300}))(dict(it, hash="0x" + "99" * 32)) is None   # stated hash lies
    assert check(Chain(until={h: NOW + 300}))(dict(it, hash="0x" + h)) is not None


def test_onchain_check_rechecks_later_but_not_every_poll():
    it = qitem()
    h = h_of(it).hex()
    t = [NOW]
    chain = Chain()
    c = OnchainCheck(chain, MODULE, now=lambda: t[0], retry_s=15)
    assert c(it) is None and chain.calls == 1
    assert c(it) is None and chain.calls == 1            # throttled: the report tx may not be mined yet
    t[0] += 16
    chain.until[h] = NOW + 300                           # report landed
    assert c(it)["expires_at"] == NOW + 300 and chain.calls == 2


def test_onchain_check_rpc_error_skips():
    class Down(Chain):
        def batch(self, calls):
            raise RpcError("network: down")

    assert check(Down())(qitem()) is None
