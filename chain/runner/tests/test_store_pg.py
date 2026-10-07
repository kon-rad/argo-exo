"""Postgres-only checks: the role grants (00-architecture §4.6) and the views. Skipped without the test DSNs."""
import pytest
from guardian_pg import ADMIN, READER, WRITER, needs_pg

pytestmark = needs_pg


def _conn(dsn):
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


@pytest.fixture
def ledger_row(pg_reset):
    from exo_guardian.store import PgStore
    s = PgStore(WRITER)
    s.insert_proposal({"id": "00000000-0000-0000-0000-0000000000a1", "created_at": 1_800_000_000, "source": "voice",
                       "intent": {"kind": "send", "summary": "Send 1 ETH"}, "chain": "ethereum", "to_addr": "0x" + "a" * 40,
                       "value_wei": 10 ** 18, "data": "0x", "salt": "0x" + "01" * 32})
    s.record_verdict("00000000-0000-0000-0000-0000000000a1",
                     {"verdict": "refuse", "risk": "high", "auto_eligible": False, "explanation": "Refused: x.",
                      "reasons": ["from the camera", "b"], "tx_hash": "0x" + "c" * 64, "expires_at": 0,
                      "report_tx": "", "usd_out": None}, 321, "refused")


@pytest.mark.skipif(not READER, reason="EXO_TEST_PG_READER_DSN not set")
def test_reader_sees_the_two_views_only(ledger_row):
    import psycopg
    with _conn(READER) as c:
        [tx] = c.execute("SELECT * FROM exo_tx_v").fetchall()
        assert tx["status"] == "refused" and tx["verdict"] == "refuse" and tx["reason"] == "from the camera"
        assert tx["summary"] == "Send 1 ETH" and tx["value_text"].startswith("1") and tx["value_text"].endswith(" ETH")
        assert set(tx) == {"id", "created_at", "chain", "summary", "to_addr", "value_text", "status", "verdict",
                           "reason", "tx_hash", "source"}
        [call] = c.execute("SELECT * FROM exo_cre_calls_v").fetchall()
        assert call["latency_ms"] == 321 and call["tx_id"] == "00000000-0000-0000-0000-0000000000a1"
        assert set(call) == {"id", "created_at", "handler", "trigger", "verdict", "reason", "latency_ms", "tx_id"}
        for table in ("proposals", "verdicts", "executions", "cre_calls"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"SELECT * FROM {table}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO cre_calls(handler, trigger) VALUES ('x', 'y')")


def test_writer_cannot_delete_or_create(ledger_row):
    import psycopg
    with _conn(WRITER) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM proposals")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("CREATE TABLE sneaky (x int)")


def test_store_refuses_to_queue_an_unbroadcast_approval(pg_reset):
    """Defence in depth: even if a caller asks, the store won't put a dry-run approval in waiting_key."""
    from exo_guardian.store import PgStore
    s = PgStore(WRITER)
    pid = "00000000-0000-0000-0000-0000000000a2"
    s.insert_proposal({"id": pid, "created_at": 1_800_000_000, "source": "voice", "intent": {"kind": "send", "summary": "x"},
                       "chain": "ethereum", "to_addr": "0x" + "a" * 40, "value_wei": 0, "data": "0x", "salt": "0x" + "01" * 32})
    with pytest.raises(RuntimeError):
        s.record_verdict(pid, {"verdict": "approve", "risk": "low", "auto_eligible": True, "explanation": "e",
                               "reasons": [], "tx_hash": "0x" + "c" * 64, "expires_at": 1_800_000_600,
                               "report_tx": "0x" + "0" * 64, "usd_out": 1}, 1, "waiting_key")
    assert s.get_status(pid) == "proposed" and s.pending(1_800_000_000) == []
    with _conn(ADMIN) as c:   # nothing half-written
        assert c.execute("SELECT count(*) AS n FROM verdicts").fetchone()["n"] == 0


def test_guard_lock_never_waits(pg_reset):
    """A second guard while one holds the advisory lock fails at once (the bridge answers 503 "guardian busy")."""
    import time
    from exo_guardian.store import GuardianBusy, PgStore
    a, b = PgStore(WRITER), PgStore(WRITER)
    with a.guard_lock():
        t0 = time.monotonic()
        with pytest.raises(GuardianBusy):
            with b.guard_lock():
                pass
        assert time.monotonic() - t0 < 5
    with b.guard_lock():   # released: the next one gets it
        pass
