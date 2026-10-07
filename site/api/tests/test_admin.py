import csv
import io

import pytest
from exo_presale.admin import TOPIC, csv_cell, fetch_logs, logs_to_rows, main, summarize, export_rows
from exo_presale.claims import ClaimStore

BUYER = "0x" + "bb" * 20
OTHER = "0x" + "cc" * 20


def log(n, tier, units, buyer=BUYER, tx="0xtx"):
    return {"topics": [TOPIC, "0x" + hex(n)[2:].rjust(64, "0"), "0x" + "0" * 24 + buyer[2:]],
            "data": "0x" + hex(tier)[2:].rjust(64, "0") + hex(units)[2:].rjust(64, "0"), "transactionHash": tx}


def test_logs_to_rows():
    log_ = {"topics": ["0xtopic", "0x" + hex(42)[2:].rjust(64, "0"), "0x" + "0" * 24 + "bb" * 20],
            "data": "0x" + hex(2)[2:].rjust(64, "0") + hex(499_000_000)[2:].rjust(64, "0"), "transactionHash": "0xtx"}
    assert logs_to_rows([log_]) == [{"device_number": 42, "buyer": "0x" + "bb" * 20, "tier": 2, "price": "499.00", "tx": "0xtx"}]


def test_topic_is_preordered_event():
    assert TOPIC == "0xb6fd44e81134743ff29142842b07b87d5c935df16feae05d0a5002478124438d"
    import shutil, subprocess
    if shutil.which("cast"):
        out = subprocess.run(["cast", "keccak", "Preordered(uint256,address,uint8,uint256)"], capture_output=True, text=True)
        assert out.stdout.strip() == TOPIC


class FakeRpc:
    def __init__(self):
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        return []


def test_fetch_logs_in_5000_block_chunks():
    rpc = FakeRpc()
    fetch_logs(rpc, "0x" + "aa" * 20, 100, 10_100)
    ranges = [(int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16)) for _, p in rpc.calls]
    assert ranges == [(100, 5099), (5100, 10_099), (10_100, 10_100)]
    assert all(m == "eth_getLogs" and p[0]["topics"] == [TOPIC] and p[0]["address"] == "0x" + "aa" * 20 for m, p in rpc.calls)


@pytest.mark.parametrize("v", ["=1+1", "+1", "-1", "@SUM(A1)", "\t=1", "\r=1"])
def test_csv_formula_cells_are_escaped(v):
    assert csv_cell(v) == "'" + v


def test_csv_plain_cells_untouched():
    assert csv_cell("Ada Lovelace") == "Ada Lovelace" and csv_cell(42) == 42 and csv_cell("") == ""


def _store(tmp_path):
    s = ClaimStore(tmp_path / "c.db")
    s.save(1, BUYER, "Ada", "ada@example.com", "Japan", 100)  # public-ok
    s.save(2, BUYER, "=HYPERLINK(\"x\")", "bob@example.com", "Japan", 100)  # public-ok
    s.save(3, BUYER, "Old Holder", "old@example.com", "Japan", 100)  # public-ok
    return s


def test_export_drops_claims_whose_owner_moved_on(tmp_path):
    rows = logs_to_rows([log(1, 1, 1_000_000), log(2, 2, 2_000_000), log(3, 1, 1_000_000), log(4, 1, 1_000_000)])
    holders = {1: BUYER, 2: BUYER.upper().replace("0X", "0x"), 3: OTHER, 4: None}   # 3 sold on, 4 refunded/burned
    out = {r["device_number"]: r for r in export_rows(rows, _store(tmp_path).all(), holders.get)}
    assert out[1]["name"] == "Ada" and out[1]["holder"] == BUYER and out[1]["claim"] == "ok"
    assert out[2]["name"].startswith("'=")                       # escaped, case-insensitive owner match
    assert out[3]["name"] == "" and out[3]["email"] == "" and out[3]["claim"] == "stale-owner"
    assert out[4]["holder"] == "" and out[4]["claim"] == "burned"


def test_summary_uses_integer_units(tmp_path):
    rows = logs_to_rows([log(1, 1, 1_000_000), log(2, 2, 2_000_000), log(3, 1, 333_333)])
    s = summarize(rows, export_rows(rows, _store(tmp_path).all(), {1: BUYER, 2: BUYER, 3: OTHER}.get))
    assert s == "minted 3 · revenue 3.33 USDC · shipping claimed 2 (1 stale)"


class ChainRpc:
    """eth_blockNumber + eth_getLogs + ownerOf eth_call, enough for main()."""
    def __init__(self, logs, holders):
        self.logs, self.holders = logs, holders

    def call(self, method, params):
        if method == "eth_blockNumber":
            return hex(20)
        if method == "eth_getLogs":
            lo, hi = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            return [l for b, l in self.logs if lo <= b <= hi]
        if method == "eth_call":
            n = int(params[0]["data"][10:], 16)
            h = self.holders.get(n)
            if h is None:
                from exo_nownodes.rpc import RpcError
                raise RpcError("eth_call: execution reverted")
            return "0x" + "0" * 24 + h[2:]
        raise AssertionError(method)


def test_main_export_and_summary(tmp_path, capsys):
    _store(tmp_path)
    env = {"EXO_PREORDER": "0x" + "aa" * 20, "EXO_PREORDER_FROM_BLOCK": "10", "EXO_CLAIMS_DB": str(tmp_path / "c.db")}
    rpc = ChainRpc([(9, log(9, 1, 1)), (11, log(1, 1, 1_000_000, tx="0xa")), (12, log(2, 2, 2_000_000, tx="0xb"))],
                   {1: BUYER, 2: OTHER})
    assert main(["export"], env=env, rpc=rpc) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert [r["device_number"] for r in rows] == ["1", "2"]          # block 9 is before FROM_BLOCK
    assert rows[0]["name"] == "Ada" and rows[1]["name"] == "" and rows[1]["claim"] == "stale-owner"
    assert main(["summary"], env=env, rpc=rpc) == 0
    assert capsys.readouterr().out.strip() == "minted 2 · revenue 3.00 USDC · shipping claimed 1 (1 stale)"


def test_main_refuses_missing_config(tmp_path):
    with pytest.raises(SystemExit):
        main(["summary"], env={"EXO_CLAIMS_DB": str(tmp_path / "c.db")}, rpc=ChainRpc([], {}))
    with pytest.raises(SystemExit):
        main(["bogus"], env={}, rpc=ChainRpc([], {}))


def test_fetch_logs_drops_reorged_out_logs():
    class R:
        def call(self, method, params):
            return [log(1, 1, 1, tx="0xkeep"), {**log(2, 1, 1, tx="0xgone"), "removed": True}, {**log(3, 1, 1, tx="0xk2"), "removed": False}]
    assert [l["transactionHash"] for l in fetch_logs(R(), "0x" + "aa" * 20, 1, 1)] == ["0xkeep", "0xk2"]


def test_env_file_read_literally(tmp_path, capsys):
    from exo_presale.config import read_env_file
    f = tmp_path / "env"
    f.write_text("# comment\n\nEXO_PREORDER=0x" + "aa" * 20 + "\nNOWNODES_API_KEY=a$b `c` $(touch pwned) d\n"
                 "export EXO_X='q v'\nQUOTED=\"x y\"\nbad line\n1BAD=x\nEXO_PREORDER_FROM_BLOCK = 10\n")
    env = read_env_file(f)
    assert env["EXO_PREORDER"] == "0x" + "aa" * 20
    assert env["NOWNODES_API_KEY"] == "a$b `c` $(touch pwned) d"
    assert env["EXO_X"] == "q v" and env["QUOTED"] == "x y" and env["EXO_PREORDER_FROM_BLOCK"] == "10"
    assert "1BAD" not in env and "bad line" not in env
    assert not (tmp_path / "pwned").exists()


def test_main_env_file_flag(tmp_path, capsys):
    _store(tmp_path)
    f = tmp_path / "env"
    f.write_text(f"EXO_PREORDER=0x{'aa' * 20}\nEXO_PREORDER_FROM_BLOCK=10\nEXO_CLAIMS_DB={tmp_path / 'c.db'}\n")
    rpc = ChainRpc([(11, log(1, 1, 1_000_000, tx="0xa"))], {1: BUYER})
    assert main(["--env-file", str(f), "summary"], env={}, rpc=rpc) == 0
    assert capsys.readouterr().out.strip() == "minted 1 · revenue 1.00 USDC · shipping claimed 1 (0 stale)"
