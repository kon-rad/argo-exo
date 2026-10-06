"""Run: python3 -m unittest discover -s tests   (from the scripts/ directory)"""
import json, os, sqlite3, sys, unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("RING_DB", ":memory:")
import ring_sync as rs  # noqa: E402

FIXTURE = json.loads((HERE / "fixtures" / "ultrahuman-sample.json").read_text())
METRICS = FIXTURE["data"]["metrics"]


class ParseDay(unittest.TestCase):
    def test_night_with_sleep(self):
        row, samples, stages = rs.parse_day(METRICS["2026-10-04"])
        self.assertEqual(row["sleep_score"], 60)
        self.assertEqual(row["total_sleep_min"], 305)
        self.assertEqual(row["time_in_bed_min"], 440)
        self.assertEqual((row["deep_min"], row["rem_min"], row["light_min"]), (65, 30, 215))
        self.assertEqual(row["awake_min"], 131)
        self.assertEqual(row["sleep_efficiency_pct"], 70)
        self.assertEqual(row["sleep_rhr"], 55)
        self.assertEqual(row["recovery_index"], 58)
        self.assertEqual(row["bedtime_start"], 1791038700)
        metrics = {m for m, _, _ in samples}
        self.assertTrue({"hr", "hrv", "temp", "steps", "sleep_hr", "sleep_hrv", "sleep_temp"} <= metrics)
        self.assertTrue(stages)
        self.assertTrue(all(e > s for s, e, _ in stages))

    def test_day_without_sleep(self):
        row, samples, stages = rs.parse_day(METRICS["2026-10-03"])
        self.assertIsNone(row["sleep_score"])
        self.assertIsNone(row["total_sleep_min"])
        self.assertEqual(row["steps_total"], 13183.0)
        self.assertEqual(row["vo2_max"], 43)
        self.assertEqual(stages, [])
        self.assertNotIn("sleep_hr", {m for m, _, _ in samples})

    def test_empty_day(self):
        row, samples, stages = rs.parse_day([])
        self.assertTrue(all(v is None for v in row.values()))
        self.assertEqual((samples, stages), ([], []))


class Planning(unittest.TestCase):
    def test_empty_store_backfills(self):
        days = rs.plan_days(None, date(2026, 10, 4), backfill_from="2026-09-24")
        self.assertEqual((days[0], days[-1], len(days)), (date(2026, 9, 24), date(2026, 10, 4), 11))

    def test_refetch_window(self):
        days = rs.plan_days("2026-10-04", date(2026, 10, 5), refetch=3)
        self.assertEqual(days, [date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5)])

    def test_force_from(self):
        days = rs.plan_days("2026-10-04", date(2026, 10, 4), force_from="2026-10-01")
        self.assertEqual(days[0], date(2026, 10, 1))

    def test_windows_under_seven_days_and_padded(self):
        days = rs.plan_days(None, date(2026, 10, 4), backfill_from="2026-09-24")
        wins = rs.chunk_windows(days)
        self.assertEqual(sum(len(w) for w, _, _ in wins), 11)
        for wanted, s, e in wins:
            self.assertLessEqual(len(wanted), rs.CHUNK_DAYS)
            self.assertLess(e - s, 7 * 86400)
            self.assertGreaterEqual(e - s, len(wanted) * 86400 + 2 * rs.PAD_S - 1)


class Store(unittest.TestCase):
    def setUp(self):
        self.con = rs.connect(":memory:")

    def counts(self):
        q = lambda t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        return {t: q(t) for t in ("ring_raw", "ring_daily", "ring_samples", "ring_sleep_stages")}

    def test_restore_is_idempotent(self):
        self.assertTrue(rs.store_day(self.con, "2026-10-04", METRICS["2026-10-04"], now=100))
        first = self.counts()
        self.assertFalse(rs.store_day(self.con, "2026-10-04", METRICS["2026-10-04"], now=200))
        self.assertEqual(self.counts(), first)
        fetched, changed = self.con.execute("SELECT fetched_at, changed_at FROM ring_raw").fetchone()
        self.assertEqual((fetched, changed), (200, 100))

    def test_changed_payload_is_due_again(self):
        rs.store_day(self.con, "2026-10-03", METRICS["2026-10-03"], now=100)
        self.con.execute("UPDATE ring_raw SET uploaded_at = 150")
        self.assertEqual(rs.pending_upload(self.con), [])
        rs.store_day(self.con, "2026-10-03", METRICS["2026-10-03"], now=200)   # unchanged
        self.assertEqual(rs.pending_upload(self.con), [])
        later = json.loads(json.dumps(METRICS["2026-10-03"]))
        next(x for x in later if x["type"] == "steps")["object"]["total"] = 14000.0
        self.assertTrue(rs.store_day(self.con, "2026-10-03", later, now=300))
        self.assertEqual(rs.pending_upload(self.con), ["2026-10-03"])
        self.assertEqual(self.con.execute("SELECT steps_total FROM ring_daily").fetchone()[0], 14000.0)

    def test_sync_drops_clipped_edge_days(self):
        calls = []

        def fake_fetch(token, s, e):
            calls.append((s, e))
            return {"2026-10-02": [], **METRICS, "2026-10-05": []}   # edges the API clipped

        n_days, n_changed = rs.sync(self.con, "tok", today=date(2026, 10, 4),
                                    force_from="2026-10-03", fetch=fake_fetch, log=lambda *_: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual((n_days, n_changed), (2, 2))
        days = [r[0] for r in self.con.execute("SELECT day FROM ring_raw ORDER BY day")]
        self.assertEqual(days, ["2026-10-03", "2026-10-04"])


if __name__ == "__main__":
    unittest.main()
