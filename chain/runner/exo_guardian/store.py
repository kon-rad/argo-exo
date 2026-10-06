"""The Guardian ledger: MemoryStore (tests) and PgStore (the droplet's Postgres, chain/ledger/*.sql).

The Guardian decides each proposal's status; the store records it. One rule is enforced here as well, so no caller
can get it wrong: a proposal only reaches `waiting_key` (the deck's approval queue) with an `approve` verdict, a real
report tx and an expiry. pending() re-checks the same, and never returns an expired item.
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from typing import Iterator, Protocol

STATUSES = ("proposed", "refused", "simulated", "waiting_key", "executed", "failed")
REAL_TX = re.compile(r"^0x(?!0{64}$)[0-9a-f]{64}$")
DAY = 86_400
GUARD_LOCK_KEY = 0x45584F4755415244   # "EXOGUARD": the one advisory lock every guard call holds
GUARD_LOCK_TIMEOUT_S = 300            # longer than one simulation (240 s); a stuck holder fails the next guard


def is_real_tx(h) -> bool:
    """A 32-byte tx hash that isn't the zero hash a dry run returns."""
    return isinstance(h, str) and bool(REAL_TX.match(h.lower()))


def queueable(result: dict) -> bool:
    return (result.get("verdict") == "approve" and is_real_tx(result.get("report_tx"))
            and isinstance(result.get("expires_at"), int) and result["expires_at"] > 0)


def _check_status(status: str, result: dict | None = None) -> None:
    if status not in STATUSES:
        raise RuntimeError(f"unknown status {status!r}")
    if status == "waiting_key" and (result is None or not queueable(result)):
        raise RuntimeError("refusing to queue a proposal without an onchain approval")


def _day_start(now: float) -> int:
    return int(now // DAY * DAY)   # UTC midnight


class Store(Protocol):
    def guard_lock(self) -> Iterator[None]: ...   # a context manager: one guard at a time, across processes
    def insert_proposal(self, p: dict) -> None: ...
    def record_verdict(self, pid: str, result: dict, latency_ms: int, status: str) -> None: ...
    def record_call(self, handler: str, verdict: str, reason: str, latency_ms: int, pid: str | None = None) -> None: ...
    def pending(self, now: float) -> list[dict]: ...
    def mark_executed(self, pid: str, tx_hash: str) -> bool: ...
    def mark_failed(self, pid: str, error: str) -> bool: ...
    def spent_today_usd(self, now: float) -> float: ...
    def get_status(self, pid: str) -> str | None: ...
    def recent_calls(self, limit: int) -> list[dict]: ...


def _queue_item(pid, p, v) -> dict:
    """00-architecture §4.4, exactly."""
    return {"id": pid, "summary": p["intent"].get("summary", ""), "explanation": v["explanation"], "chain": p["chain"],
            "to": p["to_addr"], "value": str(p["value_wei"]), "data": p["data"], "salt": p["salt"],
            "verdict_tx": v["report_tx"], "risk": v["risk"], "auto_eligible": bool(v["auto_eligible"]),
            "expires_at": int(v["expires_at"])}


class MemoryStore:
    def __init__(self):
        self.proposals, self.verdicts, self.executions, self.calls = {}, {}, {}, []
        self._guard = threading.Lock()

    @contextmanager
    def guard_lock(self):
        if not self._guard.acquire(timeout=GUARD_LOCK_TIMEOUT_S):
            raise RuntimeError("timed out waiting for the guard lock")
        try:
            yield
        finally:
            self._guard.release()

    def insert_proposal(self, p):
        self.proposals[p["id"]] = dict(p, status="proposed")

    def record_verdict(self, pid, result, latency_ms, status):
        _check_status(status, result)
        if pid in self.verdicts:   # verdicts.proposal_id is the primary key
            raise RuntimeError(f"proposal {pid} already has a verdict")
        self.verdicts[pid] = dict(result)
        if self.proposals[pid]["status"] == "proposed":
            self.proposals[pid]["status"] = status
        self.record_call("guard", result["verdict"], (result.get("reasons") or [""])[0], latency_ms, pid)

    def record_call(self, handler, verdict, reason, latency_ms, pid=None):
        self.calls.append({"handler": handler, "trigger": "http", "verdict": verdict, "reason": reason,
                           "latency_ms": int(latency_ms), "tx_id": pid})

    def pending(self, now):
        out = []
        for pid, p in sorted(self.proposals.items(), key=lambda kv: kv[1]["created_at"]):
            v = self.verdicts.get(pid)
            if p["status"] == "waiting_key" and v and queueable(v) and v["expires_at"] > now:
                out.append(_queue_item(pid, p, v))
        return out

    def _finish(self, pid, tx_hash, error):
        p = self.proposals.get(pid)
        if p is None:
            return False
        if p["status"] != "waiting_key":
            e = self.executions.get(pid)
            return bool(tx_hash) and p["status"] == "executed" and e is not None and e["tx_hash"] == tx_hash
        self.executions[pid] = {"tx_hash": tx_hash, "error": error}
        p["status"] = "executed" if tx_hash else "failed"
        return True

    def mark_executed(self, pid, tx_hash):
        return self._finish(pid, tx_hash, None)

    def mark_failed(self, pid, error):
        return self._finish(pid, None, error)

    def spent_today_usd(self, now):
        start, total = _day_start(now), 0.0
        for pid, p in self.proposals.items():
            v = self.verdicts.get(pid)
            if not v or p["created_at"] < start or v.get("usd_out") is None:
                continue
            if p["status"] == "executed" or (p["status"] == "waiting_key" and v["expires_at"] > now):
                total += float(v["usd_out"])
        return total

    def get_status(self, pid):
        p = self.proposals.get(pid)
        return p and p["status"]

    def recent_calls(self, limit):
        return list(reversed(self.calls))[:limit]


class PgStore:
    """One short connection per call: waitress serves the bridge from 4 threads, and a dropped connection
    never wedges the next request."""

    def __init__(self, dsn: str):
        import psycopg  # noqa: F401  (fail at startup if the driver is missing)
        self.dsn = dsn

    def _conn(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.dsn, row_factory=dict_row)   # `with` commits on success, rolls back on error

    @contextmanager
    def guard_lock(self):
        """A session-level advisory lock on its own connection, held for the whole guard call (spent_today read →
        simulation → verdict). Serialises guards across bridge threads and the CLI's processes; if this process dies,
        Postgres drops the connection and the lock with it."""
        import psycopg
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute(f"SET lock_timeout = '{GUARD_LOCK_TIMEOUT_S}s'")
            c.execute("SELECT pg_advisory_lock(%s)", (GUARD_LOCK_KEY,))
            try:
                yield
            finally:
                try:
                    c.execute("SELECT pg_advisory_unlock(%s)", (GUARD_LOCK_KEY,))
                except psycopg.Error:
                    pass   # closing the connection releases it anyway

    def insert_proposal(self, p):
        with self._conn() as c:
            c.execute("INSERT INTO proposals(id, created_at, source, intent, chain, to_addr, value_wei, data, salt)"
                      " VALUES (%s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s)",
                      (p["id"], p["created_at"], p["source"], json.dumps(p["intent"]), p["chain"], p["to_addr"],
                       p["value_wei"], p["data"], p["salt"]))

    def record_verdict(self, pid, r, latency_ms, status):
        _check_status(status, r)
        with self._conn() as c:
            c.execute("INSERT INTO verdicts(proposal_id, verdict, risk, auto_eligible, explanation, reasons, tx_hash,"
                      " expires_at, usd_out, report_tx) VALUES (%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s),%s,%s)",
                      (pid, r["verdict"], r["risk"], r["auto_eligible"], r["explanation"], json.dumps(r["reasons"]),
                       r["tx_hash"], r["expires_at"], r.get("usd_out"), r.get("report_tx") or ""))
            c.execute("UPDATE proposals SET status=%s WHERE id=%s AND status='proposed'", (status, pid))
            c.execute("INSERT INTO cre_calls(handler, trigger, verdict, reason, latency_ms, proposal_id)"
                      " VALUES ('guard', 'http', %s, %s, %s, %s)",
                      (r["verdict"], (r["reasons"] or [""])[0], int(latency_ms), pid))

    def record_call(self, handler, verdict, reason, latency_ms, pid=None):
        with self._conn() as c:
            c.execute("INSERT INTO cre_calls(handler, trigger, verdict, reason, latency_ms, proposal_id)"
                      " VALUES (%s, 'http', %s, %s, %s, %s)", (handler, verdict, reason, int(latency_ms), pid))

    def pending(self, now):
        with self._conn() as c:
            rows = c.execute(
                "SELECT p.id::text AS id, p.intent, p.chain, p.to_addr, p.value_wei, p.data, p.salt, v.explanation,"
                " v.report_tx, v.risk, v.auto_eligible, v.verdict, extract(epoch FROM v.expires_at)::bigint AS expires_at"
                " FROM proposals p JOIN verdicts v ON v.proposal_id = p.id"
                " WHERE p.status = 'waiting_key' AND v.verdict = 'approve' AND v.expires_at > to_timestamp(%s)"
                " ORDER BY p.created_at", (now,)).fetchall()
        out = []
        for r in rows:
            v = dict(r, expires_at=int(r["expires_at"]))
            if queueable(v) and v["expires_at"] > now:
                out.append(_queue_item(r["id"], dict(r, value_wei=int(r["value_wei"])), v))
        return out

    def _finish(self, pid, tx_hash, error):
        with self._conn() as c:
            row = c.execute("UPDATE proposals SET status=%s WHERE id=%s AND status='waiting_key' RETURNING id",
                            ("executed" if tx_hash else "failed", pid)).fetchone()
            if row:
                c.execute("INSERT INTO executions(proposal_id, tx_hash, error) VALUES (%s, %s, %s)", (pid, tx_hash, error))
                return True
            if not tx_hash:
                return False
            again = c.execute("SELECT 1 FROM proposals p JOIN executions e ON e.proposal_id = p.id"
                              " WHERE p.id=%s AND p.status='executed' AND e.tx_hash=%s", (pid, tx_hash)).fetchone()
            return again is not None

    def mark_executed(self, pid, tx_hash):
        return self._finish(pid, tx_hash, None)

    def mark_failed(self, pid, error):
        return self._finish(pid, None, error)

    def spent_today_usd(self, now):
        with self._conn() as c:
            row = c.execute(
                "SELECT coalesce(sum(v.usd_out), 0) AS s FROM verdicts v JOIN proposals p ON p.id = v.proposal_id"
                " WHERE p.created_at >= to_timestamp(%s) AND v.usd_out IS NOT NULL"
                " AND (p.status = 'executed' OR (p.status = 'waiting_key' AND v.expires_at > to_timestamp(%s)))",
                (_day_start(now), now)).fetchone()
        return float(row["s"])

    def get_status(self, pid):
        with self._conn() as c:
            row = c.execute("SELECT status FROM proposals WHERE id=%s", (pid,)).fetchone()
        return row and row["status"]

    def recent_calls(self, limit):
        with self._conn() as c:
            rows = c.execute("SELECT handler, trigger, verdict, reason, latency_ms, proposal_id::text AS tx_id"
                             " FROM cre_calls ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
        return [dict(r) for r in rows]
