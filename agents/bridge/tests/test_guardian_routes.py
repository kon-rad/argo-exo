import pytest
from exo_bridge.app import create_app
from exo_bridge.config import Config
from exo_bridge.guardian_routes import guardian_blueprint

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ENV = {"EXO_BRIDGE_TOKEN": TOKEN, "EXO_BRIDGE_HOST": "100.64.0.5", "API_SERVER_KEY": "k"}  # public-ok
PID = "00000000-0000-0000-0000-000000000001"
SENT = "0x" + "5" * 64


class FakeGuardian:
    def __init__(self, guard_result=None, freeze_result=None, raises=None, executed_ok=True):
        self.calls, self.raises, self.executed_ok = [], raises, executed_ok
        self.guard_result = guard_result or {"proposal_id": PID, "verdict": "approve", "queued": True}
        self.freeze_result = freeze_result or {"ok": True, "report_tx": "0x" + "ab" * 32, "simulated": False}

    def _maybe_raise(self):
        if self.raises:
            raise self.raises

    def guard(self, req):
        self.calls.append(("guard", req))
        self._maybe_raise()
        return self.guard_result

    def pending(self):
        self._maybe_raise()
        return [{"id": PID, "summary": "x"}]

    def executed(self, pid, tx_hash=None, error=None):
        self.calls.append(("executed", pid, tx_hash, error))
        self._maybe_raise()
        return self.executed_ok

    def freeze(self, reason):
        self.calls.append(("freeze", reason))
        self._maybe_raise()
        return self.freeze_result


def c(g=None):
    return create_app(Config.from_env(ENV), None, None, blueprints=(guardian_blueprint(g),)).test_client()


def test_routes():
    g = FakeGuardian()
    cl = c(g)
    r = cl.post("/guard", json={"tx": {}, "from": "0x1"}, headers=AUTH)
    assert r.status_code == 200 and r.json["verdict"] == "approve"
    assert cl.get("/approvals/pending", headers=AUTH).json == {"items": [{"id": PID, "summary": "x"}]}
    assert cl.post(f"/approvals/{PID}/executed", json={"tx_hash": SENT}, headers=AUTH).json == {"ok": True}
    assert cl.post(f"/approvals/{PID}/executed", json={"error": "reverted"}, headers=AUTH).json == {"ok": True}
    assert cl.post("/freeze", json={"reason": "panic"}, headers=AUTH).json["ok"] is True
    assert g.calls == [("guard", {"tx": {}, "from": "0x1"}), ("executed", PID, SENT, None),
                       ("executed", PID, None, "reverted"), ("freeze", "panic")]


def test_freeze_defaults_and_caps_the_reason():
    g = FakeGuardian()
    c(g).post("/freeze", headers=AUTH)
    c(g).post("/freeze", json={"reason": "x" * 500}, headers=AUTH)
    c(g).post("/freeze", json={"reason": 7}, headers=AUTH)
    assert g.calls == [("freeze", "panic"), ("freeze", "x" * 200), ("freeze", "panic")]


def test_not_configured_and_auth():
    for path, m in (("/approvals/pending", "get"), ("/guard", "post"), ("/freeze", "post"), (f"/approvals/{PID}/executed", "post")):
        assert getattr(c(None), m)(path, headers=AUTH, json={}).status_code == 503
        assert getattr(c(FakeGuardian()), m)(path, json={}).status_code == 401
        assert getattr(c(None), m)(path, json={}).status_code == 401   # the gate runs before the 503


@pytest.mark.parametrize("body", [None, [], {"tx": "x"}, {"no": "tx"}])
def test_guard_needs_a_tx_object(body):
    g = FakeGuardian()
    assert c(g).post("/guard", json=body, headers=AUTH).status_code == 400 and g.calls == []


def test_guard_invalid_request_is_400_with_a_fixed_message():
    r = c(FakeGuardian(raises=ValueError("tx.to must be a 0x address <script>"))).post("/guard", json={"tx": {}}, headers=AUTH)
    assert r.status_code == 400 and r.json == {"error": "invalid guard request"}


def test_guard_unavailable_is_502_with_a_fixed_message():
    g = FakeGuardian(guard_result={"proposal_id": PID, "verdict": "refuse", "unavailable": True,
                                   "explanation": "Refused: Guardian unavailable."})
    r = c(g).post("/guard", json={"tx": {}}, headers=AUTH)
    assert r.status_code == 502 and r.json == {"error": "guardian unavailable", "proposal_id": PID}


@pytest.mark.parametrize("path,method", [("/guard", "post"), ("/approvals/pending", "get"), ("/freeze", "post"),
                                         (f"/approvals/{PID}/executed", "post")])
def test_exceptions_are_502_without_their_text(path, method):
    cl = c(FakeGuardian(raises=RuntimeError("password=hunter2 connection refused")))
    r = getattr(cl, method)(path, json={"tx": {}, "tx_hash": SENT}, headers=AUTH)
    assert r.status_code == 502 and "hunter2" not in r.get_data(as_text=True)


def test_freeze_unavailable_is_502():
    g = FakeGuardian(freeze_result={"ok": False, "report_tx": "", "simulated": False, "unavailable": True})
    r = c(g).post("/freeze", json={"reason": "panic"}, headers=AUTH)
    assert r.status_code == 502 and r.json == {"error": "guardian unavailable"}


def test_freeze_dry_run_is_200_but_not_ok():
    g = FakeGuardian(freeze_result={"ok": False, "report_tx": "0x" + "0" * 64, "simulated": True})
    r = c(g).post("/freeze", headers=AUTH)
    assert r.status_code == 200 and r.json["ok"] is False and r.json["simulated"] is True


def test_executed_bad_input_is_400():
    r = c(FakeGuardian(raises=ValueError("send exactly one"))).post(f"/approvals/{PID}/executed", json={}, headers=AUTH)
    assert r.status_code == 400 and r.json == {"error": "invalid execution report"}
    assert c(FakeGuardian()).post(f"/approvals/{PID}/executed", json=["x"], headers=AUTH).status_code == 400


def test_executed_not_waiting_is_409():
    r = c(FakeGuardian(executed_ok=False)).post(f"/approvals/{PID}/executed", json={"tx_hash": SENT}, headers=AUTH)
    assert r.status_code == 409 and r.json == {"error": "not awaiting the key"}


def test_existing_routes_still_work_without_blueprints():
    cl = create_app(Config.from_env(ENV), None, None).test_client()
    assert cl.get("/health").json == {"ok": True}
    assert cl.get("/approvals/pending", headers=AUTH).status_code == 404


GUARD_TOKEN = "g" * 40


def _guard_client(g=None):
    from flask import Blueprint
    cfg = Config.from_env(dict(ENV, EXO_GUARD_TOKEN=GUARD_TOKEN))
    bp = Blueprint("ext", __name__)

    @bp.get("/ext")
    def ext():
        return {"ok": True}

    class K:
        def list(self):
            return []

        def create(self, *a):
            return {"id": "t"}

    class T:
        def ask(self, *a):
            return "hi"
    return create_app(cfg, K(), T(), blueprints=(guardian_blueprint(g or FakeGuardian()), bp)).test_client()


def test_guard_token_opens_guard_only():
    cl, narrow = _guard_client(), {"Authorization": f"Bearer {GUARD_TOKEN}"}
    assert cl.post("/guard", json={"tx": {}, "from": "0x1", "source": "agent:wallet"}, headers=narrow).status_code == 200
    assert cl.post("/freeze", json={}, headers=narrow).status_code == 401
    assert cl.post(f"/approvals/{PID}/executed", json={}, headers=narrow).status_code == 401
    assert cl.get("/approvals/pending", headers=narrow).status_code == 401
    assert cl.post("/talk", json={"text": "x"}, headers=narrow).status_code == 401
    assert cl.post("/tasks", json={"text": "x", "agent": "wallet"}, headers=narrow).status_code == 401
    assert cl.get("/board", headers=narrow).status_code == 401
    assert cl.get("/ext", headers=narrow).status_code == 401
    assert cl.get("/guard", headers=narrow).status_code in (401, 405)  # wrong method never reaches a handler


def test_full_token_still_works_everywhere_and_junk_is_rejected():
    cl = _guard_client()
    assert cl.post("/guard", json={"tx": {}, "from": "0x1"}, headers=AUTH).status_code == 200
    assert cl.post("/freeze", json={}, headers=AUTH).status_code == 200
    assert cl.post("/guard", json={"tx": {}}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert cl.post("/guard", json={"tx": {}}).status_code == 401


def test_guard_token_is_optional_and_validated():
    assert Config.from_env(ENV).guard_token == ""
    cl = c(FakeGuardian())  # no guard token configured: it is simply not accepted
    assert cl.post("/guard", json={"tx": {}}, headers={"Authorization": f"Bearer {GUARD_TOKEN}"}).status_code == 401
    for bad in ("short", TOKEN):
        with pytest.raises(ValueError):
            Config.from_env(dict(ENV, EXO_GUARD_TOKEN=bad))


GTOKEN = "g" * 40
GAUTH = {"Authorization": f"Bearer {GTOKEN}"}


def gc(g):
    return create_app(Config.from_env(dict(ENV, EXO_GUARD_TOKEN=GTOKEN)), None, None,
                      blueprints=(guardian_blueprint(g),)).test_client()


@pytest.mark.parametrize("source", ["agent:wallet", "agent:trader-2", "agent:" + "a" * 32, "camera", "dashboard"])
def test_guard_token_may_claim_agent_camera_or_dashboard(source):
    g = FakeGuardian()
    r = gc(g).post("/guard", json={"tx": {}, "source": source}, headers=GAUTH)
    assert r.status_code == 200 and g.calls == [("guard", {"tx": {}, "source": source})]


@pytest.mark.parametrize("source", [None, "", "voice", "agent", "agent:", "agent:Wallet", "agent:" + "a" * 33,
                                    "agent:wallet ", "agent:wallet\n", "agent:a/b", "camera2", 7, ["camera"]])
def test_guard_token_cannot_claim_voice_or_anything_else(source):
    g = FakeGuardian()
    body = {"tx": {}} if source is None else {"tx": {}, "source": source}
    r = gc(g).post("/guard", json=body, headers=GAUTH)
    assert r.status_code == 400 and g.calls == []


def test_full_token_may_send_voice():
    g = FakeGuardian()
    assert gc(g).post("/guard", json={"tx": {}, "source": "voice"}, headers=AUTH).status_code == 200


def test_guard_token_still_cannot_reach_the_deck_routes():
    g = FakeGuardian()
    cl = gc(g)
    assert cl.get("/approvals/pending", headers=GAUTH).status_code == 401
    assert cl.post("/freeze", json={}, headers=GAUTH).status_code == 401
    assert cl.post(f"/approvals/{PID}/executed", json={"tx_hash": SENT}, headers=GAUTH).status_code == 401


def test_busy_guardian_answers_503_at_once():
    class Busy(RuntimeError):
        busy = True
    g = FakeGuardian(raises=Busy("another guard is running"))
    r = c(g).post("/guard", json={"tx": {}}, headers=AUTH)
    assert r.status_code == 503 and r.json == {"error": "guardian busy"}


def test_busy_end_to_end_with_the_real_lock():
    """The real Guardian on a MemoryStore whose lock is held: 503 straight away, nothing recorded."""
    import time
    from exo_guardian.service import Guardian
    from exo_guardian.store import MemoryStore
    store = MemoryStore()
    guardian = Guardian(store, simulate=lambda *a: ({}, 0), module="0x" + "1" * 40, chain_id=1)
    req = {"tx": {"to": "0x" + "2" * 40, "value": "0", "data": "0x", "chain_id": 1}, "from": "0x" + "3" * 40,
           "intent": {"kind": "send", "summary": "x"}, "source": "agent:wallet"}
    with store.guard_lock():
        t0 = time.monotonic()
        r = gc(guardian).post("/guard", json=req, headers=GAUTH)
        assert time.monotonic() - t0 < 2
    assert r.status_code == 503 and r.json == {"error": "guardian busy"} and store.proposals == {}
