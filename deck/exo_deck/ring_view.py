from __future__ import annotations

import sqlite3
from pathlib import Path

LAST_COLS = ("day", "sleep_score", "total_sleep_min", "deep_min", "rem_min", "hrv_avg", "sleep_hrv",
             "sleep_rhr", "recovery_index", "temp_deviation_c", "spo2_pct", "steps_total")


def _empty(err: str) -> dict:
    return {"last": None, "trend": [], "synced_at": None, "sync_error": err}


def panel(db_path) -> dict:
    p = Path(db_path)
    if not p.exists():
        return _empty("ring.db not found")
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            last = con.execute(f"SELECT {', '.join(LAST_COLS)} FROM ring_daily ORDER BY day DESC LIMIT 1").fetchone()
            trend = con.execute("SELECT day, sleep_score, hrv_avg, recovery_index FROM ring_daily ORDER BY day DESC LIMIT 7").fetchall()
            log = con.execute("SELECT ran_at, error FROM ring_sync_log ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return _empty("ring.db unreadable")
    return {"last": dict(last) if last else None, "trend": [dict(r) for r in reversed(trend)],
            "synced_at": log["ran_at"] if log else None, "sync_error": log["error"] if log else None}
