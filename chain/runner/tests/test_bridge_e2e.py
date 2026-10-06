"""The real Guardian (MemoryStore, fake simulator) behind the real bridge: the deck's view of one approval."""
from exo_bridge.app import create_app
from exo_bridge.config import Config
from exo_bridge.guardian_routes import guardian_blueprint
from exo_guardian.store import MemoryStore
from test_service import NOW, REQ, Sim, guardian

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ENV = {"EXO_BRIDGE_TOKEN": TOKEN, "EXO_BRIDGE_HOST": "100.64.0.5", "API_SERVER_KEY": "k"}  # public-ok


def client(g):
    return create_app(Config.from_env(ENV), None, None, blueprints=(guardian_blueprint(g),)).test_client()


def test_guard_pending_executed_over_http():
    cl = client(guardian(MemoryStore(), Sim()))
    r = cl.post("/guard", json=REQ, headers=AUTH)
    assert r.status_code == 200 and r.json["queued"] is True
    pid = r.json["proposal_id"]
    [item] = cl.get("/approvals/pending", headers=AUTH).json["items"]
    assert item["id"] == pid and item["expires_at"] == NOW + 600
    assert cl.post(f"/approvals/{pid}/executed", json={"tx_hash": "0x" + "5" * 64}, headers=AUTH).json == {"ok": True}
    assert cl.get("/approvals/pending", headers=AUTH).json == {"items": []}
    assert cl.post(f"/approvals/{pid}/executed", json={"tx_hash": "0x" + "6" * 64}, headers=AUTH).status_code == 409


def test_invalid_and_unavailable_over_http():
    def boom(payload, trigger_index, broadcast):
        raise OSError("cre: not found")
    cl = client(guardian(MemoryStore(), boom))
    assert cl.post("/guard", json=dict(REQ, tx=dict(REQ["tx"], to="0x1")), headers=AUTH).json == {"error": "invalid guard request"}
    r = cl.post("/guard", json=REQ, headers=AUTH)
    assert r.status_code == 502 and r.json["error"] == "guardian unavailable"
    assert cl.post("/approvals/not-a-uuid/executed", json={"tx_hash": "0x" + "5" * 64}, headers=AUTH).status_code == 400
    assert cl.post("/freeze", json={"reason": "panic"}, headers=AUTH).status_code == 502


def test_build_guardian_without_dsn_is_none():
    from exo_bridge.__main__ import build_guardian
    assert build_guardian({}) is None
