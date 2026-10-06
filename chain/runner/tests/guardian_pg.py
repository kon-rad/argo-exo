"""Store fixtures. MemoryStore always runs; PgStore runs only against a throwaway database named by env vars:

  EXO_TEST_PG_ADMIN_DSN   the database owner (applies schema.sql + views.sql, truncates between tests)
  EXO_TEST_PG_WRITER_DSN  role exo_writer (roles.sql already applied): what PgStore connects as, so the grants are tested
  EXO_TEST_PG_READER_DSN  role exo_reader (only for the role test)

Never point these at a database you care about: every test truncates the ledger tables.
"""
import os
from pathlib import Path

import pytest

LEDGER = Path(__file__).resolve().parents[2] / "ledger"
ADMIN, WRITER, READER = (os.environ.get(f"EXO_TEST_PG_{k}_DSN") for k in ("ADMIN", "WRITER", "READER"))
needs_pg = pytest.mark.skipif(not (ADMIN and WRITER), reason="EXO_TEST_PG_ADMIN_DSN / EXO_TEST_PG_WRITER_DSN not set")


def reset_pg():
    import psycopg
    with psycopg.connect(ADMIN) as c:
        c.execute((LEDGER / "schema.sql").read_text())
        c.execute((LEDGER / "views.sql").read_text())
        c.execute("TRUNCATE executions, verdicts, cre_calls, proposals RESTART IDENTITY")
