"""Transactions and CRE panels: the droplet ledger as read through exo-bridge's read-only routes.

Everything from the ledger is untrusted text (agent-built intents end up in summaries and reasons), so rows are
rebuilt field by field, cleaned and capped here, and the kiosk renders them through esc() as well."""
from __future__ import annotations

from .bridge_client import BridgeError
from .chain_view import clean

PAGE = 7
STATUSES = ("proposed", "refused", "simulated", "waiting_key", "executed", "failed")   # 00-architecture §4.6
NOT_CONNECTED, UNREACHABLE = "Ledger not connected", "Ledger unreachable"


def _err(exc: BridgeError) -> str:
    return NOT_CONNECTED if "ledger not connected" in str(exc) else UNREACHABLE      # fixed strings: no URLs, no body text


def _t(v, cap: int) -> str:
    return clean(v, cap) if isinstance(v, str) and v else ""


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _tx(r) -> dict:
    r = r if isinstance(r, dict) else {}
    status = r.get("status")
    return {"created_at": _num(r.get("created_at")), "chain": _t(r.get("chain"), 16), "summary": _t(r.get("summary"), 120),
            "status": status if status in STATUSES else "unknown", "verdict": _t(r.get("verdict"), 16), "reason": _t(r.get("reason"), 120)}


def _call(r) -> dict:
    r = r if isinstance(r, dict) else {}
    return {"created_at": _num(r.get("created_at")), "handler": _t(r.get("handler"), 24), "trigger": _t(r.get("trigger"), 12),
            "verdict": _t(r.get("verdict"), 16), "reason": _t(r.get("reason"), 120), "latency_ms": _num(r.get("latency_ms"))}


def _handler(h) -> dict:
    h = h if isinstance(h, dict) else {}
    return {k: _t(h.get(k), 24) for k in ("handler", "trigger", "priority", "status")}


def transactions_panel(bridge, page: int) -> dict:
    try:
        data = bridge.transactions(page)
        raw = data.get("counts")
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            return {"error": UNREACHABLE}
        counts = {k: int(v) for k, v in raw.items()
                  if k in STATUSES and isinstance(v, int) and not isinstance(v, bool)}
        total = sum(counts.values())
        last = max(0, (total - 1) // PAGE)
        if page > last:                                  # the list shrank: show the last page, not an empty one
            page = last
            data = bridge.transactions(page)
    except BridgeError as exc:
        return {"error": _err(exc)}
    rows = [_tx(r) for r in data["rows"]][:PAGE]
    return {"rows": rows, "counts": counts, "total": total, "page": page, "more": max(0, total - (page + 1) * PAGE)}


def cre_panel(bridge, page: int) -> dict:
    try:
        handlers = [_handler(h) for h in bridge.workflows()["handlers"]]
    except BridgeError as exc:
        return {"error": _err(exc)}
    calls, calls_error, more = [], "", False
    try:
        data = bridge.cre_calls(page, extra=1)           # one row past the page tells us whether there is more
        if page > 0 and not data["rows"]:
            page, data = 0, bridge.cre_calls(0, extra=1)
        rows = data["rows"]
        more = len(rows) > PAGE
        calls = [_call(r) for r in rows][:PAGE]
    except BridgeError as exc:                          # the manifest still shows when the ledger is down
        calls_error = _err(exc)
    return {"handlers": handlers[:7], "calls": calls, "calls_error": calls_error, "page": page, "more": more}
