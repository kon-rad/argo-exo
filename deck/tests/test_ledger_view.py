from exo_deck import ledger_view as lv
from exo_deck.bridge_client import BridgeError


def tx(i, status="executed", **kw):
    return {"id": str(i), "created_at": 1_000 + i, "chain": "base", "summary": f"send {i} USDC", "status": status,
            "verdict": "approve", "reason": "ok", **kw}


class B:
    def __init__(self, rows=(), counts=None, calls=(), handlers=(), exc=None):
        self.rows, self.counts, self.calls, self.handlers, self.exc, self.pages = list(rows), counts or {}, list(calls), list(handlers), exc, []

    def transactions(self, page):
        self.pages.append(page)
        if self.exc:
            raise self.exc
        return {"rows": self.rows[page * 7:(page + 1) * 7], "counts": self.counts}

    def cre_calls(self, page, extra=0):
        self.pages.append(page)
        if self.exc:
            raise self.exc
        return {"rows": self.calls[page * 7:(page + 1) * 7 + extra]}

    def workflows(self):
        if self.exc:
            raise self.exc
        return {"handlers": self.handlers}


def test_transactions_panel_shapes_rows_counts_and_more():
    rows = [tx(i) for i in range(10)]
    b = B(rows, {"executed": 9, "simulated": 1})
    p = lv.transactions_panel(b, 0)
    assert len(p["rows"]) == 7 and p["total"] == 10 and p["more"] == 3 and p["counts"] == {"executed": 9, "simulated": 1}
    assert lv.transactions_panel(b, 1)["more"] == 0


def test_transactions_page_is_clamped_to_the_last_page():
    b = B([tx(i) for i in range(10)], {"executed": 10})
    p = lv.transactions_panel(b, 9)
    assert len(p["rows"]) == 3 and b.pages == [9, 1]            # asked for 9, refetched the last page


def test_unknown_status_and_hostile_text_are_neutralised():
    p = lv.transactions_panel(B([tx(1, status="pwned<script>", summary="a‮b" + "x" * 500, reason=None)], {"pwned": 1}), 0)
    r = p["rows"][0]
    assert r["status"] == "unknown" and "‮" not in r["summary"] and len(r["summary"]) <= 120 and r["reason"] == ""


def test_not_connected_and_unreachable_have_fixed_text():
    assert lv.transactions_panel(B(exc=BridgeError("HTTP 503 ledger not connected")), 0) == {"error": "Ledger not connected"}
    assert lv.cre_panel(B(exc=BridgeError("HTTP 503 ledger not connected")), 0) == {"error": "Ledger not connected"}
    e = lv.transactions_panel(B(exc=BridgeError("HTTP 502 password=hunter2")), 0)
    assert e == {"error": "Ledger unreachable"}


def test_cre_panel_shows_handlers_even_when_the_ledger_is_down():
    class Half(B):
        def cre_calls(self, page, extra=0):
            raise BridgeError("HTTP 503 ledger not connected")
    h = [{"handler": "guard", "trigger": "http", "priority": "Must", "status": "simulated"}]
    p = lv.cre_panel(Half(handlers=h), 0)
    assert p["handlers"] == h and p["calls"] == [] and p["calls_error"] == "Ledger not connected"


def test_cre_panel_pages_and_shapes():
    calls = [{"id": i, "created_at": 5, "handler": "guard", "trigger": "http", "verdict": "approve", "reason": "r", "latency_ms": 12} for i in range(9)]
    b = B(calls=calls, handlers=[{"handler": "guard", "trigger": "http", "priority": "Must", "status": "planned"}])
    p = lv.cre_panel(b, 0)
    assert len(p["calls"]) == 7 and p["more"] is True and p["calls_error"] == ""
    assert lv.cre_panel(b, 1)["more"] is False
    assert lv.cre_panel(b, 5)["calls"] == [] or b.pages[-1] == 0   # an empty far page falls back to page 0


def test_cre_more_is_false_when_exactly_a_full_page():
    calls = [{"id": i, "handler": "guard"} for i in range(7)]
    p = lv.cre_panel(B(calls=calls, handlers=[{"handler": "guard"}]), 0)
    assert len(p["calls"]) == 7 and p["more"] is False


def test_counts_must_be_an_object():
    class Bad(B):
        def transactions(self, page):
            return {"rows": [], "counts": [1, 2]}
    assert lv.transactions_panel(Bad(), 0) == {"error": "Ledger unreachable"}


def test_cre_workflows_failure_is_a_panel_error():
    assert lv.cre_panel(B(exc=BridgeError("HTTP 503 ledger not connected")), 0) == {"error": "Ledger not connected"}
