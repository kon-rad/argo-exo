import json

import pytest
from exo_bridge.app import create_app
from exo_bridge.config import Config
from exo_bridge.ledger import Ledger
from exo_bridge.ledger_routes import ledger_blueprint

TOKEN = "t" * 40
GUARD = "g" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ENV = {"EXO_BRIDGE_TOKEN": TOKEN, "EXO_BRIDGE_HOST": "100.64.0.5", "API_SERVER_KEY": "k"}  # public-ok


class FakeLedger:
    def __init__(self, raises=None):
        self.raises, self.seen = raises, []

    def transactions(self, limit, offset):
        self.seen.append(("tx", limit, offset))
        if self.raises:
            raise self.raises
        return [{"id": "1", "summary": "send 20 USDC", "status": "executed", "reason": "r" * 900, "tx_hash": None}][offset:offset + limit], {"executed": 1}

    def cre_calls(self, limit, offset):
        self.seen.append(("cre", limit, offset))
        if self.raises:
            raise self.raises
        return [{"id": 9, "handler": "guard", "verdict": "approve"}]


def manifest(tmp_path, data=None):
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps(data if data is not None else
                            [{"handler": "guard", "trigger": "http", "priority": "Must", "status": "simulated"}]))
    return str(m)


def c(tmp_path, ledger=None, mpath="auto", extra=None):
    env = dict(ENV, **(extra or {}))
    env["EXO_CRE_MANIFEST"] = manifest(tmp_path) if mpath == "auto" else mpath
    cfg = Config.from_env(env)
    return create_app(cfg, None, None, blueprints=(ledger_blueprint(ledger, cfg.cre_manifest),)).test_client()


def test_config_defaults_and_overrides():
    cfg = Config.from_env(ENV)
    assert cfg.ledger_dsn == "" and cfg.cre_manifest.endswith("chain/cre/workflow-manifest.json")
    assert Config.from_env(dict(ENV, EXO_LEDGER_DSN="postgresql://exo_reader@h/exo")).ledger_dsn.startswith("postgresql://")


def test_transactions_and_calls(tmp_path):
    fl = FakeLedger()
    cl = c(tmp_path, fl)
    j = cl.get("/ledger/transactions?limit=7", headers=AUTH).json
    assert j["rows"][0]["summary"] == "send 20 USDC" and j["counts"] == {"executed": 1}
    assert len(j["rows"][0]["reason"]) <= 300                       # untrusted text is capped server side too
    assert cl.get("/ledger/cre-calls", headers=AUTH).json["rows"][0]["handler"] == "guard"
    assert fl.seen == [("tx", 7, 0), ("cre", 7, 0)]


def test_not_connected_is_503(tmp_path):
    cl = c(tmp_path, None, mpath=str(tmp_path / "missing.json"))
    for path in ("/ledger/transactions", "/ledger/cre-calls", "/cre/workflows"):
        r = cl.get(path, headers=AUTH)
        assert r.status_code == 503 and r.json == {"error": "ledger not connected"}


def test_workflows_from_manifest(tmp_path):
    assert c(tmp_path).get("/cre/workflows", headers=AUTH).json["handlers"][0]["status"] == "simulated"


def test_workflows_work_without_a_ledger_and_the_real_manifest_is_served():
    from pathlib import Path
    real = Path(__file__).resolve().parents[3] / "chain/cre/workflow-manifest.json"
    r = c(None, None, mpath=str(real)).get("/cre/workflows", headers=AUTH)
    assert r.status_code == 200 and {h["handler"] for h in r.json["handlers"]} >= {"guard", "freeze"}


@pytest.mark.parametrize("bad", ["not json", {"a": 1}, [1, "x"], []])
def test_bad_manifest_is_503(tmp_path, bad):
    p = tmp_path / "m.json"
    p.write_text(bad if isinstance(bad, str) else json.dumps(bad))
    assert c(tmp_path, None, mpath=str(p)).get("/cre/workflows", headers=AUTH).status_code == 503


def test_manifest_fields_are_whitelisted_and_capped(tmp_path):
    m = manifest(tmp_path, [{"handler": "g" * 99, "trigger": "http", "priority": "Must", "status": "deployed", "secret": "x"}, "junk"])
    h = c(tmp_path, None, mpath=m).get("/cre/workflows", headers=AUTH).json["handlers"]
    assert len(h) == 1 and set(h[0]) == {"handler", "trigger", "priority", "status"} and len(h[0]["handler"]) <= 40


@pytest.mark.parametrize("qs,expected", [("limit=5000", 50), ("limit=0", 1), ("limit=-3", 1), ("limit=abc", 7), ("", 7), ("limit=3", 3)])
def test_limit_is_capped(tmp_path, qs, expected):
    fl = FakeLedger()
    assert c(tmp_path, fl).get(f"/ledger/transactions?{qs}", headers=AUTH).status_code == 200
    assert fl.seen[0][1] == expected


@pytest.mark.parametrize("qs,expected", [("offset=14", 14), ("offset=-1", 0), ("offset=zzz", 0), ("offset=99999999999", 100000)])
def test_offset_is_sane(tmp_path, qs, expected):
    fl = FakeLedger()
    c(tmp_path, fl).get(f"/ledger/cre-calls?{qs}", headers=AUTH)
    assert fl.seen[0][2] == expected


def test_auth_gate_runs_first_and_the_guard_token_does_not_reach_it(tmp_path):
    cl = c(tmp_path, FakeLedger(), extra={"EXO_GUARD_TOKEN": GUARD})
    for path in ("/ledger/transactions", "/ledger/cre-calls", "/cre/workflows"):
        assert cl.get(path).status_code == 401
        assert cl.get(path, headers={"Authorization": f"Bearer {GUARD}"}).status_code == 401
    assert c(tmp_path, None).get("/ledger/transactions").status_code == 401        # 401 before 503


def test_ledger_errors_are_502_without_their_text(tmp_path):
    cl = c(tmp_path, FakeLedger(raises=RuntimeError("password=hunter2 refused")))
    for path in ("/ledger/transactions", "/ledger/cre-calls"):
        r = cl.get(path, headers=AUTH)
        assert r.status_code == 502 and r.json == {"error": "ledger unavailable"} and "hunter2" not in r.get_data(as_text=True)


def test_routes_are_read_only(tmp_path):
    cl = c(tmp_path, FakeLedger())
    for path in ("/ledger/transactions", "/ledger/cre-calls", "/cre/workflows"):
        assert cl.post(path, headers=AUTH, json={}).status_code == 405


# --- the SQL reader ---------------------------------------------------------------------------------------
class FakeConn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.log.append("close")

    def execute(self, sql, args=()):
        self.log.append((" ".join(sql.split()), args))
        return self

    def fetchall(self):
        sql = self.log[-1][0]
        if "GROUP BY status" in sql:
            return [{"status": "executed", "n": 3}, {"status": "simulated", "n": 1}]
        return [{"id": "1", "created_at": 5, "summary": "x" * 500, "status": "executed"}]


def test_ledger_reads_only_the_two_views_read_only_and_parameterised():
    log = []
    led = Ledger("postgresql://exo_reader@h/exo", connect=lambda dsn: FakeConn(log))
    rows, counts = led.transactions(7, 14)
    led.cre_calls(7, 0)
    stmts = [e for e in log if isinstance(e, tuple)]
    assert any("default_transaction_read_only" in s for s, _ in stmts)
    selects = [(s, a) for s, a in stmts if s.startswith("SELECT")]
    assert all(" FROM exo_tx_v" in s or " FROM exo_cre_calls_v" in s for s, _ in selects)
    assert selects[0][1] == (7, 14) and "LIMIT %s OFFSET %s" in selects[0][0]
    assert counts == {"executed": 3, "simulated": 1}
    assert log.count("close") == 2                                    # one connection per call, always closed
