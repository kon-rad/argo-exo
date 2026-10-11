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
    assert [t["day"] for t in p["trend"]] == [f"2026-10-{d:02d}" for d in range(1, 10)]   # up to 14 days, oldest first
    assert p["synced_at"] == 1800000000 and p["sync_error"] is None
    assert (p["night_index"], p["nights_total"]) == (0, 9)


def test_more_steps_back_through_scored_nights(tmp_path):
    db = make_db(tmp_path)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ring_daily(day) VALUES ('2026-10-10')")     # today: no sleep yet
    con.commit()
    con.close()
    assert ring_view.panel(db, 0)["last"]["day"] == "2026-10-09"        # newest *scored* night, not today
    assert ring_view.panel(db, 2)["last"]["day"] == "2026-10-07"
    assert ring_view.panel(db, 9)["last"]["day"] == "2026-10-09"        # wraps


def test_stages_hr_and_server_count(tmp_path):
    db = make_db(tmp_path)
    con = sqlite3.connect(db)
    con.execute("UPDATE ring_daily SET bedtime_start = 1000, bedtime_end = 5000 WHERE day = '2026-10-09'")
    con.executemany("INSERT INTO ring_sleep_stages VALUES ('2026-10-09', ?, ?, ?)",
                    [(1000, 2000, "light_sleep"), (2000, 5000, "deep_sleep")])
    con.executemany("INSERT INTO ring_samples VALUES ('sleep_hr', ?, '2026-10-09', ?)",
                    [(900, 70), (1500, 60), (4000, 55), (6000, 80)])                 # 900 and 6000 fall outside bed
    con.executemany("INSERT INTO ring_raw(day, payload, payload_sha256, fetched_at, changed_at, uploaded_at)"
                    " VALUES (?, '[]', 'x', 10, 10, ?)", [("2026-10-08", 20), ("2026-10-09", None)])
    con.commit()
    con.close()
    p = ring_view.panel(db)
    assert [s["stage"] for s in p["stages"]] == ["light_sleep", "deep_sleep"]
    assert p["sleep_hr"] == [[1500, 60.0], [4000, 55.0]]
    assert p["server"] == {"uploaded": 1, "total": 2}


def test_missing_db(tmp_path):
    p = ring_view.panel(tmp_path / "none.db")
    assert p["last"] is None and p["trend"] == [] and p["sync_error"] == "ring.db not found"


def test_unreadable_db_degrades(tmp_path):
    p = tmp_path / "ring.db"
    p.write_text("not a database")
    out = ring_view.panel(p)
    assert out["last"] is None and out["trend"] == [] and out["sync_error"]
