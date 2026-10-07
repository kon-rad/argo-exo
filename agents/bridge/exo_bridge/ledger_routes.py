"""Read-only ledger and CRE routes for the kiosk, registered through create_app(blueprints=...) so every route is
behind the full bearer token (the narrow guard token reaches POST /guard only).

`ledger` is exo_bridge.ledger.Ledger, or None when EXO_LEDGER_DSN is unset (those routes answer 503).
`manifest_path` is chain/cre/workflow-manifest.json. Error bodies are fixed strings: DSNs and driver text never leave."""
from __future__ import annotations

import json
import logging
from functools import wraps

from flask import Blueprint, jsonify, request

log = logging.getLogger("exo-bridge")
NOT_CONNECTED = "ledger not connected"
NO_MANIFEST = "workflow manifest missing"
MAX_LIMIT, MAX_OFFSET, DEFAULT_LIMIT = 50, 100_000, 7
TEXT_CAP = 300     # summaries and reasons come from agent-built intents: never trust their size
MANIFEST_FIELDS = ("handler", "trigger", "priority", "status")


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(request.args.get(name, default)), hi))
    except (TypeError, ValueError):
        return default


def _paging() -> tuple[int, int]:
    return _int("limit", DEFAULT_LIMIT, 1, MAX_LIMIT), _int("offset", 0, 0, MAX_OFFSET)


def _cap(rows) -> list:
    return [{k: (v[:TEXT_CAP] if isinstance(v, str) else v) for k, v in r.items()} for r in rows]


def load_manifest(path: str):
    """The manifest entries (whitelisted fields, capped), or None when missing, unreadable or empty."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out = [{k: str(e.get(k, ""))[:40] for k in MANIFEST_FIELDS} for e in data if isinstance(e, dict) and e.get("handler")]
    return out or None


def ledger_blueprint(ledger, manifest_path: str) -> Blueprint:
    bp = Blueprint("ledger", __name__)

    def needs_ledger(fn):
        # View-level, not before_request: create_app adds the bearer gate as before_request after we return,
        # and an unauthenticated caller must get 401 before learning whether a ledger exists.
        @wraps(fn)
        def wrapper(*a, **kw):
            if ledger is None:
                return jsonify(error=NOT_CONNECTED), 503
            try:
                return fn(*a, **kw)
            except Exception as exc:  # noqa: BLE001
                log.warning("ledger read failed: %s", type(exc).__name__)
                return jsonify(error="ledger unavailable"), 502
        return wrapper

    @bp.get("/ledger/transactions")
    @needs_ledger
    def transactions():
        rows, counts = ledger.transactions(*_paging())
        return jsonify(rows=_cap(rows), counts=counts)

    @bp.get("/ledger/cre-calls")
    @needs_ledger
    def cre_calls():
        return jsonify(rows=_cap(ledger.cre_calls(*_paging())))

    @bp.get("/cre/workflows")
    def workflows():
        handlers = load_manifest(manifest_path)
        if handlers is None:
            return jsonify(error=NO_MANIFEST), 503
        return jsonify(handlers=handlers)

    return bp
