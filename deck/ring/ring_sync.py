#!/usr/bin/env python3
"""
Ultrahuman Ring AIR → /srv/deck/ring.db

Pulls daily_metrics from Ultrahuman's partner API (ring → phone → cloud → here)
and stores the raw payload per day plus parsed daily, sample and sleep-stage
tables. Re-fetches the last few days each run because sleep lands after the
phone syncs. Days changed since their last upload are due for the future push
to the secondbrain server (uploaded_at IS NULL OR uploaded_at < changed_at).

Run:    python3 /srv/deck/ring_sync.py            # sync
        python3 /srv/deck/ring_sync.py --status   # counts + pending uploads
        python3 /srv/deck/ring_sync.py --from 2026-09-24
Timer:  ring-sync.timer (hourly)
Env:    ULTRAHUMAN_API_TOKEN (required), DECK_ROOT, RING_DB,
        RING_BACKFILL_FROM (default 2026-09-24), RING_REFETCH_DAYS (default 3)
Design: Projects/wearable-body-track/ring-sync-design.md
"""
import argparse, hashlib, json, os, sqlite3, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

API_URL       = "https://partner.ultrahuman.com/api/v1/partner/daily_metrics"
DECK_ROOT     = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
DB_PATH       = Path(os.environ.get("RING_DB", DECK_ROOT / "ring.db"))
BACKFILL_FROM = os.environ.get("RING_BACKFILL_FROM", "2026-09-24")
REFETCH_DAYS  = int(os.environ.get("RING_REFETCH_DAYS", "3"))
CHUNK_DAYS    = 5           # 5 days + 12 h padding each side = 6-day window, under the 7-day cap
PAD_S         = 12 * 3600

SAMPLE_TYPES = ("hr", "hrv", "temp", "steps", "spo2")
SLEEP_GRAPHS = {"hr_graph": "sleep_hr", "hrv_graph": "sleep_hrv", "temp_graph": "sleep_temp"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ring_raw (
  day TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  fetched_at INTEGER NOT NULL, changed_at INTEGER NOT NULL, uploaded_at INTEGER
);
CREATE TABLE IF NOT EXISTS ring_daily (
  day TEXT PRIMARY KEY,
  sleep_score INTEGER, total_sleep_min INTEGER, time_in_bed_min INTEGER,
  deep_min INTEGER, rem_min INTEGER, light_min INTEGER, awake_min INTEGER,
  sleep_efficiency_pct REAL, restorative_pct REAL, temp_deviation_c REAL,
  avg_body_temp_c REAL, spo2_pct REAL, tosses_turns INTEGER, full_cycles INTEGER,
  bedtime_start INTEGER, bedtime_end INTEGER, morning_alertness_min INTEGER,
  steps_total REAL, hrv_avg REAL, hr_last REAL, sleep_hrv REAL, sleep_rhr REAL,
  recovery_index REAL, movement_index REAL, active_min REAL, inactive_min REAL,
  vo2_max REAL
);
CREATE TABLE IF NOT EXISTS ring_samples (
  metric TEXT NOT NULL, ts INTEGER NOT NULL, day TEXT NOT NULL, value REAL,
  PRIMARY KEY (metric, ts)
);
CREATE INDEX IF NOT EXISTS ring_samples_day ON ring_samples(day);
CREATE TABLE IF NOT EXISTS ring_sleep_stages (
  day TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL, stage TEXT NOT NULL,
  PRIMARY KEY (day, start)
);
CREATE TABLE IF NOT EXISTS ring_sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ran_at INTEGER NOT NULL,
  days INTEGER NOT NULL, changed INTEGER NOT NULL, error TEXT
);
"""

DAILY_COLS = [
    "sleep_score", "total_sleep_min", "time_in_bed_min", "deep_min", "rem_min", "light_min",
    "awake_min", "sleep_efficiency_pct", "restorative_pct", "temp_deviation_c", "avg_body_temp_c",
    "spo2_pct", "tosses_turns", "full_cycles", "bedtime_start", "bedtime_end",
    "morning_alertness_min", "steps_total", "hrv_avg", "hr_last", "sleep_hrv", "sleep_rhr",
    "recovery_index", "movement_index", "active_min", "inactive_min", "vo2_max",
]


class TokenRejected(Exception):
    pass


class ApiError(Exception):
    pass


# ─── parsing ───────────────────────────────────────────────────────────────────

def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def parse_day(items):
    """One day's [{type, object}, …] → (daily row dict, samples, sleep stages)."""
    by = {x.get("type"): x.get("object") or {} for x in items}
    sl = by.get("sleep") or {}
    has_sleep = "sleep_score" in sl

    awake = None
    for st in sl.get("sleep_stages") or []:
        if st.get("type") == "awake" and st.get("stage_time") is not None:
            awake = round(st["stage_time"] / 60)

    row = {c: None for c in DAILY_COLS}
    if has_sleep:
        row.update(
            sleep_score=_get(sl, "sleep_score", "score"),
            total_sleep_min=_get(sl, "total_sleep", "minutes"),
            time_in_bed_min=_get(sl, "time_in_bed", "minutes"),
            deep_min=_get(sl, "deep_sleep", "minutes"),
            rem_min=_get(sl, "rem_sleep", "minutes"),
            light_min=_get(sl, "light_sleep", "minutes"),
            awake_min=awake,
            sleep_efficiency_pct=_get(sl, "sleep_efficiency", "percentage"),
            restorative_pct=_get(sl, "restorative_sleep", "percentage"),
            temp_deviation_c=_get(sl, "temperature_deviation", "celsius"),
            avg_body_temp_c=_get(sl, "average_body_temperature", "celsius"),
            spo2_pct=_get(sl, "spo2", "value"),
            tosses_turns=_get(sl, "tosses_and_turns", "count"),
            full_cycles=_get(sl, "full_sleep_cycles", "cycles"),
            bedtime_start=sl.get("bedtime_start"),
            bedtime_end=sl.get("bedtime_end"),
        )
    row.update(
        morning_alertness_min=_get(by, "morning_alertness", "value") or _get(sl, "morning_alertness", "minutes"),
        steps_total=_get(by, "steps", "total"),
        hrv_avg=_get(by, "hrv", "avg"),
        hr_last=_get(by, "hr", "last_reading"),
        sleep_hrv=_get(by, "avg_sleep_hrv", "value"),
        sleep_rhr=_get(by, "sleep_rhr", "value"),
        recovery_index=_get(by, "recovery_index", "value"),
        movement_index=_get(by, "movement_index", "value"),
        active_min=_get(by, "active_minutes", "value"),
        inactive_min=_get(by, "inactive_time", "value"),
        vo2_max=_get(by, "vo2_max", "value"),
    )

    samples = []
    for t in SAMPLE_TYPES:
        for v in _get(by, t, "values") or []:
            if v.get("timestamp") is not None:
                samples.append((t, int(v["timestamp"]), v.get("value")))
    for key, metric in SLEEP_GRAPHS.items():
        for v in _get(sl, key, "data") or []:
            if v.get("timestamp") is not None:
                samples.append((metric, int(v["timestamp"]), v.get("value")))

    stages = [
        (int(s["start"]), int(s["end"]), s.get("type") or "unknown")
        for s in _get(sl, "sleep_graph", "data") or []
        if s.get("start") is not None and s.get("end") is not None
    ]
    return row, samples, stages


# ─── storage ───────────────────────────────────────────────────────────────────

def connect(path=DB_PATH):
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    return con


def store_day(con, day, items, now=None):
    """Upsert one day. Returns True if the payload changed (or is new)."""
    now = int(now if now is not None else time.time())
    payload = json.dumps(items, sort_keys=True, separators=(",", ":"))
    sha = hashlib.sha256(payload.encode()).hexdigest()
    prev = con.execute("SELECT payload_sha256 FROM ring_raw WHERE day = ?", (day,)).fetchone()
    changed = prev is None or prev[0] != sha

    with con:
        if not changed:
            con.execute("UPDATE ring_raw SET fetched_at = ? WHERE day = ?", (now, day))
            return False
        con.execute(
            """INSERT INTO ring_raw (day, payload, payload_sha256, fetched_at, changed_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET payload = excluded.payload,
                 payload_sha256 = excluded.payload_sha256,
                 fetched_at = excluded.fetched_at, changed_at = excluded.changed_at""",
            (day, payload, sha, now, now),
        )
        row, samples, stages = parse_day(items)
        cols = ["day"] + DAILY_COLS
        con.execute(
            f"INSERT OR REPLACE INTO ring_daily ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [day] + [row[c] for c in DAILY_COLS],
        )
        con.execute("DELETE FROM ring_samples WHERE day = ?", (day,))
        con.executemany(
            "INSERT OR REPLACE INTO ring_samples (metric, ts, day, value) VALUES (?, ?, ?, ?)",
            [(m, ts, day, v) for m, ts, v in samples],
        )
        con.execute("DELETE FROM ring_sleep_stages WHERE day = ?", (day,))
        con.executemany(
            "INSERT OR REPLACE INTO ring_sleep_stages (day, start, end, stage) VALUES (?, ?, ?, ?)",
            [(day, s, e, st) for s, e, st in stages],
        )
    return True


def pending_upload(con):
    return [r[0] for r in con.execute(
        "SELECT day FROM ring_raw WHERE uploaded_at IS NULL OR uploaded_at < changed_at ORDER BY day"
    )]


# ─── planning + fetching ───────────────────────────────────────────────────────

def plan_days(last_stored, today, backfill_from=BACKFILL_FROM, refetch=REFETCH_DAYS, force_from=None):
    """Days to fetch, oldest first, as date objects."""
    if force_from:
        start = date.fromisoformat(force_from)
    elif last_stored:
        start = min(date.fromisoformat(last_stored), today) - timedelta(days=refetch - 1)
    else:
        start = date.fromisoformat(backfill_from)
    return [start + timedelta(days=i) for i in range((today - start).days + 1)]


def chunk_windows(days, chunk=CHUNK_DAYS):
    """[(set of 'YYYY-MM-DD', start_epoch, end_epoch)] with 12 h padding each side."""
    out = []
    for i in range(0, len(days), chunk):
        group = days[i:i + chunk]
        first = datetime.combine(group[0], datetime.min.time()).timestamp()
        last_end = datetime.combine(group[-1] + timedelta(days=1), datetime.min.time()).timestamp()
        out.append(({d.isoformat() for d in group}, int(first - PAD_S), int(last_end + PAD_S - 1)))
    return out


def fetch_range(token, start_epoch, end_epoch, timeout=30):
    qs = urllib.parse.urlencode({"start_epoch": start_epoch, "end_epoch": end_epoch})
    req = urllib.request.Request(f"{API_URL}?{qs}", headers={
        "Authorization": token, "Accept": "application/json", "User-Agent": "cyberdeck-ring-sync/1",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        snippet = e.read().decode(errors="replace")[:200]
        if e.code in (401, 403):
            raise TokenRejected(f"HTTP {e.code}: {snippet}")
        raise ApiError(f"HTTP {e.code}: {snippet}")
    try:
        doc = json.loads(body)
    except ValueError:
        raise ApiError(f"bad JSON: {body[:200]}")
    if doc.get("error") or doc.get("status") not in (200, None):
        raise ApiError(f"API error: {str(doc.get('error'))[:200]}")
    return (doc.get("data") or {}).get("metrics") or {}


def sync(con, token, today=None, force_from=None, fetch=fetch_range, log=print):
    today = today or date.today()
    last = con.execute("SELECT max(day) FROM ring_raw").fetchone()[0]
    days = plan_days(last, today, force_from=force_from)
    n_days = n_changed = 0
    for wanted, s, e in chunk_windows(days):
        metrics = fetch(token, s, e)
        for day in sorted(wanted & metrics.keys()):   # drop clipped edge days
            n_days += 1
            n_changed += store_day(con, day, metrics[day])
    log(f"ring-sync: fetched {n_days} days ({days[0]} → {days[-1]}), {n_changed} changed, "
        f"{len(pending_upload(con))} pending upload")
    return n_days, n_changed


def status(con):
    q = lambda sql: con.execute(sql).fetchone()
    first, last, n = q("SELECT min(day), max(day), count(*) FROM ring_raw")
    nights = q("SELECT count(*) FROM ring_daily WHERE sleep_score IS NOT NULL")[0]
    print(f"days:     {n}  ({first} → {last})")
    print(f"nights:   {nights} with a sleep score")
    for metric, cnt in con.execute("SELECT metric, count(*) FROM ring_samples GROUP BY metric ORDER BY metric"):
        print(f"samples:  {metric:<11} {cnt}")
    print(f"pending upload: {len(pending_upload(con))} days")
    lr = q("SELECT ran_at, days, changed, error FROM ring_sync_log ORDER BY id DESC LIMIT 1")
    if lr:
        when = datetime.fromtimestamp(lr[0]).strftime("%Y-%m-%d %H:%M")
        print(f"last run: {when}  days={lr[1]} changed={lr[2]} error={lr[3] or '-'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--status", action="store_true", help="print counts and pending uploads")
    ap.add_argument("--from", dest="force_from", metavar="YYYY-MM-DD", help="re-fetch from this date")
    args = ap.parse_args(argv)

    con = connect()
    if args.status:
        status(con)
        return 0

    token = os.environ.get("ULTRAHUMAN_API_TOKEN", "").strip()
    if not token:
        print("ring-sync: ULTRAHUMAN_API_TOKEN not set (expected in /srv/deck/.env)", file=sys.stderr)
        return 2

    days = changed = 0
    err, code = None, 0
    try:
        days, changed = sync(con, token, force_from=args.force_from)
    except TokenRejected as e:
        err, code = f"token rejected — regenerate in the Ultrahuman developer portal ({e})", 2
    except (ApiError, urllib.error.URLError, TimeoutError, OSError) as e:
        err, code = f"{type(e).__name__}: {e}", 1
    with con:
        con.execute("INSERT INTO ring_sync_log (ran_at, days, changed, error) VALUES (?, ?, ?, ?)",
                    (int(time.time()), days, changed, err))
    if err:
        print(f"ring-sync: {err}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
