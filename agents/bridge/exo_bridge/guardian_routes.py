"""The Transaction Guardian's bridge routes (00-architecture §4.1, owner 03-guardian).

Registered through create_app(blueprints=...), so every route is behind the bearer token. `guardian` is
exo_guardian.service.Guardian (or None when the ledger isn't configured: every route answers 503). The bridge does
not import exo_guardian; it only relies on the Guardian raising ValueError for bad input.
Error bodies are fixed strings: exception text (DSNs, simulator output) never leaves the droplet.
"""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, jsonify, request

log = logging.getLogger("exo-bridge")


def guardian_blueprint(guardian) -> Blueprint:
    bp = Blueprint("guardian", __name__)

    def body():
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else None

    def configured(fn):
        # A view-level check, not before_request: create_app adds the bearer gate as this blueprint's
        # before_request after we return, and an unauthenticated caller must get 401 first.
        @wraps(fn)
        def wrapper(*a, **kw):
            if guardian is None:
                return jsonify(error="guardian not configured"), 503
            return fn(*a, **kw)
        return wrapper

    @bp.post("/guard")
    @configured
    def guard():
        b = body()
        if b is None or not isinstance(b.get("tx"), dict):
            return jsonify(error="tx required"), 400
        try:
            result = guardian.guard(b)
        except ValueError as exc:
            log.info("guard: invalid request (%s)", exc)
            return jsonify(error="invalid guard request"), 400
        except Exception as exc:  # noqa: BLE001
            log.warning("guard failed: %s", type(exc).__name__)
            return jsonify(error="guardian unavailable"), 502
        if result.get("unavailable"):
            return jsonify(error="guardian unavailable", proposal_id=result.get("proposal_id")), 502
        return jsonify(result)

    @bp.get("/approvals/pending")
    @configured
    def approvals_pending():
        try:
            return jsonify(items=guardian.pending())
        except Exception as exc:  # noqa: BLE001
            log.warning("pending failed: %s", type(exc).__name__)
            return jsonify(error="ledger unavailable"), 502

    @bp.post("/approvals/<pid>/executed")
    @configured
    def approvals_executed(pid):
        b = body()
        if b is None:
            return jsonify(error="invalid execution report"), 400
        try:
            recorded = guardian.executed(pid, tx_hash=b.get("tx_hash"), error=b.get("error"))
        except ValueError as exc:
            log.info("executed: invalid report (%s)", exc)
            return jsonify(error="invalid execution report"), 400
        except Exception as exc:  # noqa: BLE001
            log.warning("executed failed: %s", type(exc).__name__)
            return jsonify(error="ledger unavailable"), 502
        if not recorded:
            return jsonify(error="not awaiting the key"), 409
        return jsonify(ok=True)

    @bp.post("/freeze")
    @configured
    def freeze():
        reason = (body() or {}).get("reason")
        reason = reason[:200] if isinstance(reason, str) and reason.strip() else "panic"
        try:
            result = guardian.freeze(reason)
        except Exception as exc:  # noqa: BLE001
            log.warning("freeze failed: %s", type(exc).__name__)
            return jsonify(error="guardian unavailable"), 502
        if result.get("unavailable"):
            return jsonify(error="guardian unavailable"), 502
        return jsonify(result)

    return bp
