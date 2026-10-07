import json
import threading
import time

from exo_deck.chain_view import Balances, clean, fmt_units, load_wallets

A = "0x" + "a" * 40
WALLETS = [{"label": "agent-safe", "chain": "ethereum", "address": A}, {"label": "agent-safe", "chain": "base", "address": A}]
ADDR_RESP = {"balance": "1500000000000000000", "tokens": [
    {"type": "ERC20", "symbol": "USDC", "decimals": 6, "balance": "20000000", "contract": "0xusdc"},
    {"type": "ERC20", "symbol": "SPAM", "decimals": 18, "balance": "0", "contract": "0xspam"}]}


class FakeBB:
    def __init__(self, chain, calls, fail=(), resp=ADDR_RESP):
        self.chain, self.calls, self.fail, self.resp = chain, calls, fail, resp

    def address(self, addr, details="basic", **kw):
        assert details == "tokenBalances"
        self.calls.append((self.chain, addr))
        if self.chain in self.fail:
            raise RuntimeError("HTTP 503 api-key=SECRET")
        return self.resp


def sync(fn):
    fn()


def client_for(calls, **kw):
    return lambda chain: FakeBB(chain, calls, **kw)


def test_fmt_units_exact_no_float():
    assert fmt_units("1500000000000000000", 18) == "1.5"
    assert fmt_units("20000000", 6) == "20"
    assert fmt_units("1", 18) == "<0.0001"
    assert fmt_units("0", 18) == "0"
    assert fmt_units("123456789012345678901234567890123456789", 18) == "123456789012345678901.2345"   # 28+ digits stay exact
    assert fmt_units("12x", 6) is None and fmt_units("5", 99) is None


def test_clean_strips_control_and_bidi_and_caps():
    assert clean("US‮DC\x07\n") == "USDC"
    assert clean("A" * 40) == "A" * 16
    assert clean("​‮") == "?"


def test_balances_per_chain_and_cache():
    calls, now = [], [1000.0]
    b = Balances(client_for(calls), WALLETS, ttl_s=60, clock=lambda: now[0], spawn=sync)
    b.panel(0)                                  # first call kicks the refresh (synchronous here) and says loading
    p = b.panel(0)
    assert [w["chain"] for w in p["wallets"]] == ["ethereum", "base"]
    assert p["wallets"][0]["native"] == "1.5 ETH" and p["wallets"][0]["tokens"] == [{"symbol": "USDC", "amount": "20"}]
    b.panel(0)
    assert len(calls) == 2                      # cached
    now[0] += 61
    b.panel(0)
    assert len(calls) == 4                      # refreshed


def test_one_chain_failing_does_not_hide_the_rest_and_hides_the_error_text():
    b = Balances(client_for([], fail=("base",)), WALLETS, spawn=sync)
    b.panel(0)
    p = b.panel(0)
    assert len(p["wallets"]) == 1 and "base" in p["errors"][0] and "SECRET" not in json.dumps(p)


def test_failed_refresh_keeps_last_good_row():
    calls, now, fail = [], [0.0], []
    b = Balances(lambda ch: FakeBB(ch, calls, fail=tuple(fail)), WALLETS[:1], ttl_s=60, clock=lambda: now[0], spawn=sync)
    b.panel(0)
    assert b.panel(0)["wallets"][0]["native"] == "1.5 ETH"
    fail.append("ethereum"); now[0] += 61
    b.panel(0)                                   # kicks the failing refresh (synchronous here)
    p = b.panel(0)
    assert p["wallets"][0]["native"] == "1.5 ETH" and p["wallets"][0]["stale"] is True and p["errors"]


def test_hostile_token_rows_are_dropped_or_cleaned():
    resp = {"balance": "0", "tokens": [
        {"type": "ERC20", "symbol": "E‮VIL<b>" + "x" * 30, "decimals": 18, "balance": "10" + "0" * 18},
        {"type": "ERC721", "symbol": "NFT", "decimals": 0, "balance": "3"},
        {"type": "ERC20", "symbol": "BAD", "decimals": 18, "balance": "-5"},
        {"type": "ERC20", "symbol": "BAD2", "decimals": "x", "balance": "5"}, "junk"]}
    b = Balances(client_for([], resp=resp), WALLETS[:1], spawn=sync)
    b.panel(0)
    p = b.panel(0)
    toks = p["wallets"][0]["tokens"]
    assert len(toks) == 1 and len(toks[0]["symbol"]) <= 16 and "‮" not in toks[0]["symbol"] and toks[0]["amount"] == "10"


def test_paging_and_clamp():
    ws = [{"label": f"w{i}", "chain": "base", "address": A} for i in range(12)]
    b = Balances(client_for([]), ws, spawn=sync)
    b.panel(0)
    p0, p1 = b.panel(0), b.panel(1)
    assert len(p0["wallets"]) == 5 and p0["more"] == 7 and p1["wallets"][0]["label"] == "w5"
    assert b.panel(99)["wallets"][0]["label"] == "w10"          # list shrank: clamp to the last page


def test_unknown_chain_is_an_error_row_not_a_crash():
    b = Balances(client_for([]), [{"label": "x", "chain": "dogechain", "address": A}], spawn=sync)
    b.panel(0)
    p = b.panel(0)
    assert p["wallets"] == [] and p["errors"]


def test_load_wallets_matches_skill_schema(tmp_path):
    f = tmp_path / "w.json"
    f.write_text(json.dumps({"agent-safe": {"ethereum": A, "base": A}, "cold": {"arbitrum": A}}))
    got = load_wallets(f)
    assert [(w["label"], w["chain"]) for w in got] == [("agent-safe", "ethereum"), ("agent-safe", "base"), ("cold", "arbitrum")]
    assert got[0]["address"] == A


def test_load_wallets_missing_or_bad_is_empty_or_skips(tmp_path):
    assert load_wallets(tmp_path / "nope.json") == []
    f = tmp_path / "w.json"
    f.write_text(json.dumps({"ok": {"base": A}, "bad": {"base": "0xnothex"}, "worse": "str"}))
    assert [w["label"] for w in load_wallets(f)] == ["ok"]
    f.write_text("[1,2")
    assert load_wallets(f) == []


def test_example_file_uses_the_skill_schema():
    from pathlib import Path
    d = Path(__file__).resolve().parents[1]
    raw = json.loads((d / "wallets.example.json").read_text())
    assert isinstance(raw, dict) and all(isinstance(m, dict) for m in raw.values())      # {"<label>": {"<chain>": "0x..."}}
    assert len(load_wallets(d / "wallets.example.json")) == sum(len(m) for m in raw.values())


class SlowBB:
    """address() blocks until released, like a hung NOWNodes."""
    gate = None
    started = 0

    def __init__(self, chain):
        self.chain = chain

    def address(self, addr, details="basic", **kw):
        SlowBB.started += 1
        SlowBB.gate.wait(5)
        return ADDR_RESP


def test_panel_never_blocks_and_only_one_refresh_runs():
    SlowBB.gate, SlowBB.started = threading.Event(), 0
    now = [0.0]
    b = Balances(SlowBB, WALLETS, ttl_s=60, clock=lambda: now[0])
    t0 = time.monotonic()
    first = [b.panel(0) for _ in range(20)]
    assert time.monotonic() - t0 < 0.5
    assert all(p["loading"] and p["wallets"] == [] for p in first)       # nothing cached yet: a loading state
    time.sleep(0.1)
    assert SlowBB.started == 1                                           # 20 polls, one refresh in flight
    SlowBB.gate.set()
    for _ in range(100):
        if not b.panel(0).get("loading"):
            break
        time.sleep(0.02)
    assert [w["chain"] for w in b.panel(0)["wallets"]] == ["ethereum", "base"]


def test_cached_rows_stay_visible_and_stale_while_a_slow_refresh_runs():
    SlowBB.gate, SlowBB.started = threading.Event(), 0
    SlowBB.gate.set()
    now = [0.0]
    b = Balances(SlowBB, WALLETS, ttl_s=60, clock=lambda: now[0])
    b.panel(0)
    for _ in range(100):
        if not b.panel(0).get("loading"):
            break
        time.sleep(0.02)
    assert b.panel(0)["wallets"][0]["stale"] is False
    SlowBB.gate = threading.Event()                                      # the next refresh hangs
    now[0] = 500.0
    t0 = time.monotonic()
    p = b.panel(0)
    assert time.monotonic() - t0 < 0.2
    assert len(p["wallets"]) == 2 and all(w["stale"] for w in p["wallets"])
    time.sleep(0.1)
    before = SlowBB.started
    for _ in range(10):
        b.panel(0)
    assert SlowBB.started == before                                      # still the one refresh
    SlowBB.gate.set()


def test_age_starts_when_the_refresh_finishes():
    now = [0.0]

    def slow_spawn(fn):                       # the refresh "takes" 100 s of clock time
        def run():
            now[0] += 100
            fn()
        run()
    b = Balances(client_for([]), WALLETS, ttl_s=60, clock=lambda: now[0], spawn=slow_spawn)
    b.panel(0)
    p = b.panel(0)
    assert p["age_s"] == 0 and not any(w["stale"] for w in p["wallets"])   # not already stale, no immediate re-refresh
