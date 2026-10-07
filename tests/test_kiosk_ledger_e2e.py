"""The deck's Bridge client against the real bridge blueprint (Flask test client as the transport): the two sides agree."""
from pathlib import Path

from exo_bridge.app import create_app
from exo_bridge.config import Config
from exo_bridge.ledger_routes import ledger_blueprint
from exo_deck import ledger_view
from exo_deck.bridge_client import Bridge

TOKEN = "t" * 40
ENV = {"EXO_BRIDGE_TOKEN": TOKEN, "EXO_BRIDGE_HOST": "100.64.0.5", "API_SERVER_KEY": "k"}  # public-ok
MANIFEST = Path(__file__).resolve().parents[1] / "chain/cre/workflow-manifest.json"


class FakeLedger:
    def transactions(self, limit, offset):
        rows = [{"id": str(i), "created_at": 1000 + i, "chain": "base", "summary": f"tx {i}", "status": "simulated",
                 "verdict": "approve", "reason": "ok"} for i in range(9)]
        return rows[offset:offset + limit], {"simulated": 9}

    def cre_calls(self, limit, offset):
        return [{"id": 1, "created_at": 5, "handler": "guard", "trigger": "http", "verdict": "approve", "reason": "ok", "latency_ms": 9}][offset:offset + limit]


class Resp:   # the shape Bridge expects: .status_code and .json()
    def __init__(self, r):
        self.status_code, self._r = r.status_code, r

    def json(self):
        return self._r.get_json()


def deck_bridge(ledger):
    cl = create_app(Config.from_env(ENV), None, None, blueprints=(ledger_blueprint(ledger, str(MANIFEST)),)).test_client()
    return Bridge("http://bridge", TOKEN, get=lambda url, headers=None, params=None, timeout=None: Resp(cl.get(
        url.replace("http://bridge", ""), headers=headers, query_string=params)))


def test_panels_render_from_the_real_routes():
    b = deck_bridge(FakeLedger())
    tx = ledger_view.transactions_panel(b, 1)
    assert tx["total"] == 9 and len(tx["rows"]) == 2 and tx["rows"][0]["status"] == "simulated"
    cre = ledger_view.cre_panel(b, 0)
    assert [h["handler"] for h in cre["handlers"]][:2] == ["guard", "freeze"] and cre["calls"][0]["handler"] == "guard"


def test_not_connected_end_to_end():
    b = deck_bridge(None)
    assert ledger_view.transactions_panel(b, 0) == {"error": "Ledger not connected"}
    assert ledger_view.cre_panel(b, 0)["calls_error"] == "Ledger not connected"      # manifest still served
