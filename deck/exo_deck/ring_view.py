from __future__ import annotations

import sqlite3
from pathlib import Path

# ring.db is written by ring/ring_sync.py (installed as /srv/deck/ring_sync.py, hourly timer).
# The body panel shows one scored night at a time; "more" (page) steps back through the nights.

NIGHT_COLS = ("day", "sleep_score", "total_sleep_min", "time_in_bed_min", "deep_min", "rem_min", "light_min",
              "awake_min", "sleep_efficiency_pct", "hrv_avg", "sleep_hrv", "sleep_rhr", "recovery_index",
              "temp_deviation_c", "spo2_pct", "steps_total", "bedtime_start", "bedtime_end")
TREND_DAYS = 14


def _empty(err: str) -> dict:
    return {"last": None, "trend": [], "stages": [], "sleep_hr": [], "night_index": 0, "nights_total": 0,
            "server": {"uploaded": 0, "total": 0}, "synced_at": None, "sync_error": err}


def panel(db_path, page: int = 0) -> dict:
    p = Path(db_path)
    if not p.exists():
        return _empty("ring.db not found")
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            nights = [r[0] for r in con.execute(
                "SELECT day FROM ring_daily WHERE sleep_score IS NOT NULL ORDER BY day DESC")]
            idx = page % len(nights) if nights else 0
            if nights:
                last = con.execute(f"SELECT {', '.join(NIGHT_COLS)} FROM ring_daily WHERE day = ?",
                                   (nights[idx],)).fetchone()
            else:   # no scored night yet: fall back to the newest day so the panel still shows something
                last = con.execute(f"SELECT {', '.join(NIGHT_COLS)} FROM ring_daily ORDER BY day DESC LIMIT 1").fetchone()
            stages, hr = [], []
            if last and last["bedtime_start"]:
                stages = [dict(r) for r in con.execute(
                    "SELECT start, end, stage FROM ring_sleep_stages WHERE day = ? ORDER BY start", (last["day"],))]
                hr = [[r[0], r[1]] for r in con.execute(
                    "SELECT ts, value FROM ring_samples WHERE day = ? AND metric = 'sleep_hr' AND value IS NOT NULL"
                    " AND ts BETWEEN ? AND ? ORDER BY ts", (last["day"], last["bedtime_start"], last["bedtime_end"]))]
            trend = con.execute(
                "SELECT day, sleep_score, hrv_avg, recovery_index FROM ring_daily ORDER BY day DESC LIMIT ?",
                (TREND_DAYS,)).fetchall()
            up, total = con.execute(
                "SELECT coalesce(sum(uploaded_at IS NOT NULL AND uploaded_at >= changed_at), 0), count(*) FROM ring_raw"
            ).fetchone()
            log = con.execute("SELECT ran_at, error FROM ring_sync_log ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return _empty("ring.db unreadable")
    return {"last": dict(last) if last else None, "trend": [dict(r) for r in reversed(trend)],
            "stages": stages, "sleep_hr": hr, "night_index": idx, "nights_total": len(nights),
            "server": {"uploaded": up, "total": total},
            "synced_at": log["ran_at"] if log else None, "sync_error": log["error"] if log else None}
