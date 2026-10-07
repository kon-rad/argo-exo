"""Read-only view of the Guardian ledger for the kiosk (00-architecture §4.6).

Connects with EXO_LEDGER_DSN, a separate DSN for the exo_reader role (chain/ledger/roles.sql): it can SELECT the two
views and nothing else, so even a bug here cannot write. The session is also set read-only and given a statement
timeout. (Ledger text is capped in ledger_routes and escaped by the kiosk.)"""
from __future__ import annotations

from typing import Callable


class Ledger:
    def __init__(self, dsn: str, connect: Callable | None = None):
        if connect is None:
            import psycopg
            from psycopg.rows import dict_row
            connect = lambda d: psycopg.connect(d, row_factory=dict_row, autocommit=True, connect_timeout=5)  # noqa: E731
        self.dsn, self.connect = dsn, connect

    @staticmethod
    def _q(conn, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def _open(self):
        return self.connect(self.dsn)

    def transactions(self, limit: int, offset: int):
        with self._open() as conn:
            conn.execute("SET default_transaction_read_only = on")
            conn.execute("SET statement_timeout = '5s'")
            rows = self._q(conn, "SELECT id, extract(epoch from created_at)::bigint AS created_at, chain, summary, to_addr,"
                                 " value_text, status, verdict, reason, tx_hash, source FROM exo_tx_v"
                                 " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", (limit, offset))
            counts = {r["status"]: int(r["n"]) for r in self._q(
                conn, "SELECT status, count(*) AS n FROM exo_tx_v GROUP BY status")}
        return rows, counts

    def cre_calls(self, limit: int, offset: int):
        with self._open() as conn:
            conn.execute("SET default_transaction_read_only = on")
            conn.execute("SET statement_timeout = '5s'")
            rows = self._q(conn, "SELECT id, extract(epoch from created_at)::bigint AS created_at, handler, trigger, verdict,"
                                 " reason, latency_ms, tx_id FROM exo_cre_calls_v"
                                 " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", (limit, offset))
        return rows
