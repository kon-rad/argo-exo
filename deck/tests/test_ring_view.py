import sqlite3
import sys
from pathlib import Path

from exo_deck import ring_view

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ring"))
import ring_sync  # noqa: E402  (its SCHEMA is the source of truth)


def make_db(tmp_path):
    p = tmp_path / "ring.db"
    con = sqlite3.connect(p)
    con.executescript(ring_sync.SCHEMA)
    for i in range(9):
        con.execute("INSERT INTO ring_daily(day, sleep_score, total_sleep_min, hrv_avg, recovery_index, sleep_rhr)"
                    " VALUES (?,?,?,?,?,?)", (f"2026-10-{i + 1:02d}", 70 + i, 400 + i, 40 + i, 60 + i, 55))
    con.execute("INSERT INTO ring_sync_log(ran_at, days, changed, error) VALUES (1800000000, 2, 1, NULL)")
    con.commit()
    con.close()
    return p


def test_last_night_and_trend(tmp_path):
    p = ring_view.panel(make_db(tmp_path))
    assert p["last"]["day"] == "2026-10-09" and p["last"]["sleep_score"] == 78
    assert [t["day"] for t in p["trend"]] == [f"2026-10-{d:02d}" for d in range(3, 10)]
    assert p["synced_at"] == 1800000000 and p["sync_error"] is None


def test_missing_db(tmp_path):
    assert ring_view.panel(tmp_path / "none.db") == {"last": None, "trend": [], "synced_at": None, "sync_error": "ring.db not found"}


def test_unreadable_db_degrades(tmp_path):
    p = tmp_path / "ring.db"
    p.write_text("not a database")
    out = ring_view.panel(p)
    assert out["last"] is None and out["trend"] == [] and out["sync_error"]
