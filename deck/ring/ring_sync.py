#!/usr/bin/env python3
"""
Ultrahuman Ring AIR → the master Postgres on the droplet, mirrored to the Pi's SQLite.

One module, two modes:
  droplet   python3 ring_sync.py           pull daily_metrics from Ultrahuman into Postgres (the master)
  Pi        python3 ring_sync.py --pull    copy changed days from the droplet's ring_serve.py into ring.db
  both      python3 ring_sync.py --status
            python3 ring_sync.py --from 2026-09-24          (re-fetch / re-pull from that day)
Timers:     ring-sync.timer on both machines (droplet :07, Pi :17)
Env:        ULTRAHUMAN_API_TOKEN (droplet only), RING_TZ (account timezone, default Asia/Tokyo),
            RING_BACKEND postgres|sqlite (default: postgres when DATABASE_URL is set), DATABASE_URL,
            DECK_ROOT / RING_DB (sqlite path), RING_BACKFILL_FROM (2026-09-24), RING_REFETCH_DAYS (3),
            RING_SERVER_URL + RING_INGEST_TOKEN (the Pi's pull source)
Design:     Projects/wearable-body-track/ring-sync-design.md
"""
import argparse, hashlib, json, os, sqlite3, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

API_URL       = "https://partner.ultrahuman.com/api/v1/partner/daily_metrics"
DECK_ROOT     = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
DB_PATH       = Path(os.environ.get("RING_DB", DECK_ROOT / "ring.db"))
DB_URL        = os.environ.get("DATABASE_URL", "")
BACKEND       = os.environ.get("RING_BACKEND") or ("postgres" if DB_URL else "sqlite")
TZ            = os.environ.get("RING_TZ", "Asia/Tokyo")
BACKFILL_FROM = os.environ.get("RING_BACKFILL_FROM", "2026-09-24")
REFETCH_DAYS  = int(os.environ.get("RING_REFETCH_DAYS", "3"))
CHUNK_DAYS    = 5           # 5 days + 12 h padding each side = 6-day window, under the 7-day cap
PAD_S         = 12 * 3600
PULL_PAGE     = 50          # days per GET /ring/days

SAMPLE_TYPES = ("hr", "hrv", "temp", "steps", "spo2")
SLEEP_GRAPHS = {"hr_graph": "sleep_hr", "hrv_graph": "sleep_hrv", "temp_graph": "sleep_temp"}

# The Pi mirror (SQLite). Epoch integers; changed_at copies the droplet's value on a pull.
SCHEMA = """
CREATE TABLE IF NOT EXISTS ring_raw (
  day TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
  fetched_at INTEGER NOT NULL, changed_at INTEGER NOT NULL
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

# The master (Postgres). Same columns as the app's migrations 1756000006000_ring.sql and
# 1756000012800_ring-sync-log.sql; change both together. received_at = last fetched from Ultrahuman.
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS ring_raw (
  day DATE PRIMARY KEY, payload JSONB NOT NULL, payload_sha256 TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(), changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ring_daily (
  day DATE PRIMARY KEY,
  sleep_score INTEGER, total_sleep_min INTEGER, time_in_bed_min INTEGER,
  deep_min INTEGER, rem_min INTEGER, light_min INTEGER, awake_min INTEGER,
  sleep_efficiency_pct REAL, restorative_pct REAL, temp_deviation_c REAL,
  avg_body_temp_c REAL, spo2_pct REAL, tosses_turns INTEGER, full_cycles INTEGER,
  bedtime_start BIGINT, bedtime_end BIGINT, morning_alertness_min INTEGER,
  steps_total REAL, hrv_avg REAL, hr_last REAL, sleep_hrv REAL, sleep_rhr REAL,
  recovery_index REAL, movement_index REAL, active_min REAL, inactive_min REAL,
  vo2_max REAL
);
CREATE TABLE IF NOT EXISTS ring_samples (
  metric TEXT NOT NULL, ts BIGINT NOT NULL, day DATE NOT NULL, value REAL,
  PRIMARY KEY (metric, ts)
);
CREATE INDEX IF NOT EXISTS ring_samples_day ON ring_samples(day);
CREATE TABLE IF NOT EXISTS ring_sleep_stages (
  day DATE NOT NULL, start BIGINT NOT NULL, "end" BIGINT NOT NULL, stage TEXT NOT NULL,
  PRIMARY KEY (day, start)
);
CREATE TABLE IF NOT EXISTS ring_sync_log (
  id BIGSERIAL PRIMARY KEY, ran_at TIMESTAMPTZ NOT NULL DEFAULT now(),
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


def today_in(tz=TZ):
    """Ultrahuman files days in the account's timezone; the droplet's clock is UTC."""
    return datetime.now(ZoneInfo(tz)).date()


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


def canonical(items):
    """The stored payload text and its sha256. The same bytes on both machines."""
    text = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return text, hashlib.sha256(text.encode()).hexdigest()


# ─── storage: SQLite (Pi mirror) or Postgres (master) behind the same functions ─

def is_pg(con):
    return not isinstance(con, sqlite3.Connection)


def connect(path=None):
    """SQLite. The default path is the Pi's ring.db."""
    con = sqlite3.connect(str(path if path is not None else DB_PATH))
    con.executescript(SCHEMA)
    return con


def connect_pg(url=None):
    import psycopg2
    con = psycopg2.connect(url or DB_URL)
    with con, con.cursor() as cur:
        cur.execute(PG_SCHEMA)
    return con


def rows(con, sql, params=()):
    """Run one statement on either backend ('?' placeholders) and return all rows."""
    if is_pg(con):
        with con.cursor() as cur:
            cur.execute(sql.replace("?", "%s"), params)
            return cur.fetchall() if cur.description else []
    return con.execute(sql, params).fetchall()


def one(con, sql, params=()):
    r = rows(con, sql, params)
    return r[0] if r else None


def max_day(con):
    r = one(con, "SELECT max(day)::text FROM ring_raw" if is_pg(con) else "SELECT max(day) FROM ring_raw")
    return r[0] if r else None


def store_day(con, day, items, now=None, changed_at=None):
    """Upsert one day in one transaction. Returns True if the payload is new or changed.
    changed_at (epoch) is taken from the droplet on a pull; otherwise it is `now` (SQLite) or now() (Postgres)."""
    now = int(now if now is not None else time.time())
    payload, sha = canonical(items)
    row, samples, stages = parse_day(items)
    cols = ["day"] + DAILY_COLS

    if is_pg(con):
        with con, con.cursor() as cur:
            cur.execute("SELECT payload_sha256 FROM ring_raw WHERE day = %s", (day,))
            prev = cur.fetchone()
            if prev and prev[0] == sha:
                cur.execute("UPDATE ring_raw SET received_at = now() WHERE day = %s", (day,))
                return False
            cur.execute(
                """INSERT INTO ring_raw (day, payload, payload_sha256) VALUES (%s, %s, %s)
                   ON CONFLICT (day) DO UPDATE SET payload = EXCLUDED.payload,
                     payload_sha256 = EXCLUDED.payload_sha256, received_at = now(), changed_at = now()""",
                (day, payload, sha),
            )
            cur.execute(
                f"""INSERT INTO ring_daily ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})
                    ON CONFLICT (day) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in DAILY_COLS)}""",
                [day] + [row[c] for c in DAILY_COLS],
            )
            cur.execute("DELETE FROM ring_samples WHERE day = %s", (day,))
            if samples:
                cur.executemany(
                    """INSERT INTO ring_samples (metric, ts, day, value) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (metric, ts) DO UPDATE SET day = EXCLUDED.day, value = EXCLUDED.value""",
                    [(m, ts, day, v) for m, ts, v in samples],
                )
            cur.execute("DELETE FROM ring_sleep_stages WHERE day = %s", (day,))
            if stages:
                cur.executemany(
                    'INSERT INTO ring_sleep_stages (day, start, "end", stage) VALUES (%s, %s, %s, %s) '
                    "ON CONFLICT (day, start) DO NOTHING",
                    [(day, s, e, st) for s, e, st in stages],
                )
        return True

    prev = con.execute("SELECT payload_sha256 FROM ring_raw WHERE day = ?", (day,)).fetchone()
    with con:
        if prev and prev[0] == sha:
            con.execute("UPDATE ring_raw SET fetched_at = ? WHERE day = ?", (now, day))
            return False
        con.execute(
            """INSERT INTO ring_raw (day, payload, payload_sha256, fetched_at, changed_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET payload = excluded.payload,
                 payload_sha256 = excluded.payload_sha256,
                 fetched_at = excluded.fetched_at, changed_at = excluded.changed_at""",
            (day, payload, sha, now, changed_at if changed_at is not None else now),
        )
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


def drop_from(con, day):
    """Forget every day >= day so a --from re-fetch or re-pull rewrites it."""
    with con:
        for t in ("ring_raw", "ring_daily", "ring_samples", "ring_sleep_stages"):
            rows(con, f"DELETE FROM {t} WHERE day >= ?", (day,))


def log_run(con, days, changed, error, now=None):
    if is_pg(con):
        with con:
            rows(con, "INSERT INTO ring_sync_log (days, changed, error) VALUES (?, ?, ?)", (days, changed, error))
    else:
        with con:
            con.execute("INSERT INTO ring_sync_log (ran_at, days, changed, error) VALUES (?, ?, ?, ?)",
                        (int(now if now is not None else time.time()), days, changed, error))


def last_run(con):
    """(ran_at epoch, days, changed, error) of the newest log row, or None."""
    sql = ("SELECT extract(epoch FROM ran_at)::bigint, days, changed, error FROM ring_sync_log ORDER BY id DESC LIMIT 1"
           if is_pg(con) else "SELECT ran_at, days, changed, error FROM ring_sync_log ORDER BY id DESC LIMIT 1")
    return one(con, sql)


# ─── droplet: planning + fetching from Ultrahuman ──────────────────────────────

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
        "Authorization": token, "Accept": "application/json", "User-Agent": "secondbrain-ring-sync/2",
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
    today = today or today_in()
    if force_from:
        drop_from(con, force_from)
    days = plan_days(max_day(con), today, force_from=force_from)
    n_days = n_changed = 0
    for wanted, s, e in chunk_windows(days):
        metrics = fetch(token, s, e)
        for day in sorted(wanted & metrics.keys()):   # drop clipped edge days
            n_days += 1
            n_changed += store_day(con, day, metrics[day])
    log(f"ring-sync: fetched {n_days} days ({days[0]} → {days[-1]}), {n_changed} changed")
    return n_days, n_changed


# ─── Pi: pull the mirror from the droplet ──────────────────────────────────────

def get_days(url, token, since, limit=PULL_PAGE, timeout=60):
    qs = urllib.parse.urlencode({"since": repr(float(since)), "limit": limit})
    req = urllib.request.Request(f"{url.rstrip('/')}/ring/days?{qs}", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise ApiError(f"ring-serve HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")


def mirror_since(con):
    r = one(con, "SELECT max(changed_at) FROM ring_raw")
    return float(r[0]) if r and r[0] is not None else 0.0


def pull(con, url, token, get=get_days, force_from=None, now=None, log=print):
    """Copy every day the droplet changed since the mirror's newest change. Returns (days, changed)."""
    if force_from:
        drop_from(con, force_from)
    since = 0.0 if force_from else mirror_since(con)
    n_days = n_changed = 0
    while True:
        days = (get(url, token, since) or {}).get("days") or []
        before = since
        for d in days:
            text = d["payload"]
            if hashlib.sha256(text.encode()).hexdigest() != d["sha256"]:
                raise ValueError(f"{d['day']}: sha256 mismatch from the server")
            n_days += 1
            n_changed += store_day(con, d["day"], json.loads(text), now=now, changed_at=float(d["changed_at"]))
            since = max(since, float(d["changed_at"]))
        if len(days) < PULL_PAGE or since <= before:
            break
    log(f"ring-sync: pulled {n_days} days from {url}, {n_changed} changed")
    return n_days, n_changed


# ─── CLI ───────────────────────────────────────────────────────────────────────

def status(con):
    first, last, n = one(con, "SELECT min(day)::text, max(day)::text, count(*) FROM ring_raw" if is_pg(con)
                         else "SELECT min(day), max(day), count(*) FROM ring_raw")
    nights = one(con, "SELECT count(*) FROM ring_daily WHERE sleep_score IS NOT NULL")[0]
    print(f"backend:  {'postgres' if is_pg(con) else 'sqlite ' + str(DB_PATH)}")
    print(f"days:     {n}  ({first} → {last})")
    print(f"nights:   {nights} with a sleep score")
    for metric, cnt in rows(con, "SELECT metric, count(*) FROM ring_samples GROUP BY metric ORDER BY metric"):
        print(f"samples:  {metric:<11} {cnt}")
    lr = last_run(con)
    if lr:
        when = datetime.fromtimestamp(lr[0]).strftime("%Y-%m-%d %H:%M")
        print(f"last run: {when}  days={lr[1]} changed={lr[2]} error={lr[3] or '-'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--status", action="store_true", help="print counts and the last run")
    ap.add_argument("--from", dest="force_from", metavar="YYYY-MM-DD", help="re-fetch (or re-pull) from this date")
    ap.add_argument("--pull", action="store_true", help="Pi: copy changed days from the droplet instead of Ultrahuman")
    args = ap.parse_args(argv)

    con = connect_pg() if BACKEND == "postgres" else connect()
    if args.status:
        status(con)
        return 0

    error, code, days, changed = None, 0, 0, 0
    try:
        if args.pull:
            url = os.environ.get("RING_SERVER_URL", "").strip()
            token = os.environ.get("RING_INGEST_TOKEN", "").strip()
            if not (url and token):
                print("ring-sync: RING_SERVER_URL / RING_INGEST_TOKEN not set (expected in /srv/deck/.env)", file=sys.stderr)
                return 2
            days, changed = pull(con, url, token, force_from=args.force_from)
        else:
            token = os.environ.get("ULTRAHUMAN_API_TOKEN", "").strip()
            if not token:
                print("ring-sync: ULTRAHUMAN_API_TOKEN not set (expected in /etc/ring-sync/env)", file=sys.stderr)
                return 2
            days, changed = sync(con, token, force_from=args.force_from)
    except TokenRejected as e:
        error, code = f"token rejected - regenerate in the Ultrahuman developer portal ({e})", 2
    except (ApiError, urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        error, code = f"{'pull' if args.pull else 'fetch'} {type(e).__name__}: {e}", 1

    log_run(con, days, changed, error)
    if error:
        print(f"ring-sync: {error}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
