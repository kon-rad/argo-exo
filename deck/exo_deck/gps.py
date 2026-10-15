"""Exo GPS (u-blox NEO-6M on GY-GPS6MV2, UART4: GPS TX -> GPIO 9): position for the lifelog.

  deck-gps                     fix, satellites, position, last logged point
  deck-gps watch               live, once a second (Ctrl+C to stop)
  deck-gps log [-n 20]         recent logged points
  deck-gps run                 the service (deck-gps.service)

The service reads NMEA straight off the serial port (no gpsd) and keeps /srv/deck/state/gps.json current.
It logs a point to /srv/deck/outbox/gps/<day>.jsonl and, as an argo-lifeos event (sensitivity 2: GPS fixes
are private), to /srv/deck/outbox/lifeos/<day>.ndjson when you have moved LOG_MOVE_M or LOG_EVERY_S has passed.
Receive only: the module needs a view of the sky, no network.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger("deck-gps")

DECK = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
DEVICE = os.environ.get("EXO_GPS_DEVICE", "/dev/ttyAMA4")       # dtoverlay=uart4; check `ls /dev/ttyAMA*`
BAUD = int(os.environ.get("EXO_GPS_BAUD", "9600"))
LOG_EVERY_S = float(os.environ.get("EXO_GPS_LOG_EVERY_S", "300"))
LOG_MOVE_M = float(os.environ.get("EXO_GPS_LOG_MOVE_M", "50"))
STALE_S = 10.0
UERE_M = 5.0             # rough metres per unit of HDOP for a NEO-6M; coord_uncertainty_m = hdop * this
SOURCE = "exo-edge"


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _append(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def state_path(root: Path) -> Path:
    return root / "state" / "gps.json"


# ---------- NMEA ----------

def checksum_ok(line: str) -> bool:
    if not line.startswith("$") or "*" not in line:
        return False
    body, _, cs = line[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(cs[:2], 16)
    except ValueError:
        return False


def _coord(value: str, hemi: str, deg_digits: int) -> float | None:
    if not value or hemi not in ("N", "S", "E", "W"):
        return None
    try:
        deg = int(value[:deg_digits])
        mins = float(value[deg_digits:])
    except ValueError:
        return None
    x = deg + mins / 60.0
    return round(-x if hemi in ("S", "W") else x, 7)


def _num(s: str, cast=float):
    try:
        return cast(s) if s != "" else None
    except ValueError:
        return None


def parse(line: str) -> dict | None:
    """One NMEA sentence -> the fields it carries, or None. Handles RMC and GGA from any talker (GP, GN, GL)."""
    line = line.strip()
    if not checksum_ok(line):
        return None
    f = line[1:].split("*")[0].split(",")
    kind = f[0][2:]
    if kind == "RMC" and len(f) >= 10:
        valid = f[2] == "A"
        out = {"valid": valid, "utc_time": f[1], "utc_date": f[9]}
        if valid:
            knots = _num(f[7])
            out.update(lat=_coord(f[3], f[4], 2), lon=_coord(f[5], f[6], 3),
                       speed_kmh=round(knots * 1.852, 1) if knots is not None else None, course=_num(f[8]))
        return out
    if kind == "GGA" and len(f) >= 10:
        quality = _num(f[6], int) or 0
        out = {"quality": quality, "sats": _num(f[7], int) or 0, "hdop": _num(f[8])}
        if quality > 0:
            out.update(lat=_coord(f[2], f[3], 2), lon=_coord(f[4], f[5], 3), alt_m=_num(f[9]))
        return out
    return None


def utc_iso(date: str, tm: str) -> str | None:
    """ddmmyy + hhmmss(.ss) -> ISO 8601 UTC."""
    if len(date) != 6 or len(tm) < 6:
        return None
    return f"20{date[4:6]}-{date[2:4]}-{date[0:2]}T{tm[0:2]}:{tm[2:4]}:{tm[4:6]}Z"


class Fix:
    """Merges RMC and GGA sentences into the current fix."""

    def __init__(self):
        self.d: dict = {"fix": False, "sats": 0}
        self.updated = 0.0

    def feed(self, fields: dict, now: float) -> None:
        if "valid" in fields:
            self.d["fix"] = fields["valid"]
            self.d["utc"] = utc_iso(fields.get("utc_date", ""), fields.get("utc_time", ""))
            for k in ("speed_kmh", "course"):
                self.d[k] = fields.get(k)
        if "quality" in fields:
            self.d["sats"] = fields["sats"]
            self.d["hdop"] = fields.get("hdop")
            self.d["alt_m"] = fields.get("alt_m")
            if fields["quality"] == 0:
                self.d["fix"] = False
        if fields.get("lat") is not None and fields.get("lon") is not None:
            self.d["lat"], self.d["lon"] = fields["lat"], fields["lon"]
        self.updated = now

    def snapshot(self, now: float) -> dict:
        s = {**self.d, "at": round(now, 3)}
        if not s.get("fix"):
            s.pop("lat", None)
            s.pop("lon", None)
        if s.get("hdop") is not None:
            s["uncertainty_m"] = round(s["hdop"] * UERE_M, 1)
        return s


def distance_m(a: dict, b: dict) -> float:
    """Haversine distance between two {lat, lon} points."""
    r = 6371000.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def should_log(snap: dict, last: dict | None, now: float, every: float = LOG_EVERY_S,
               move: float = LOG_MOVE_M) -> bool:
    if not snap.get("fix") or snap.get("lat") is None:
        return False
    if not last:
        return True
    return now - last.get("ts", 0) >= every or distance_m(last, snap) >= move


# ---------- the log: one point line + one lifeos event ----------

def lifeos_event(entry: dict) -> dict:
    ev = {"type": "event", "source": SOURCE, "kind": "gps_fix", "start": entry["at"],
          "dedupe_key": f"gps:{int(entry['ts'] * 1000)}", "sensitivity": 2,
          "lat": entry["lat"], "lon": entry["lon"],
          "payload": {k: entry[k] for k in ("alt_m", "speed_kmh", "course", "sats", "hdop") if entry.get(k) is not None}}
    if entry.get("uncertainty_m") is not None:
        ev["coord_uncertainty_m"] = entry["uncertainty_m"]
    return ev


def record(root: Path, snap: dict, now: float) -> dict:
    entry = {"ts": round(now, 3), "at": _iso(now),
             **{k: snap[k] for k in ("lat", "lon", "alt_m", "speed_kmh", "course", "sats", "hdop", "uncertainty_m", "utc")
                if snap.get(k) is not None}}
    _append(root / "outbox" / "gps" / f"{_day(now)}.jsonl", entry)
    _append(root / "outbox" / "lifeos" / f"{_day(now)}.ndjson", lifeos_event(entry))
    return entry


def recent(root: Path, n: int = 20) -> list[dict]:
    out: list[dict] = []
    folder = root / "outbox" / "gps"
    for f in sorted(folder.glob("*.jsonl"), reverse=True) if folder.is_dir() else []:
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
            if len(out) >= n:
                return list(reversed(out))
    return list(reversed(out))


def process(root: Path, lines: Iterable[str], clock: Callable[[], float] = time.time) -> int:
    """The service loop over a stream of NMEA lines. Returns how many points it logged."""
    fix, logged = Fix(), 0
    last = (recent(root, 1) or [None])[-1]
    for line in lines:
        now = clock()
        fields = parse(line)
        if fields is None:
            continue
        fix.feed(fields, now)
        if "quality" not in fields:      # GGA closes each 1 s epoch (after RMC), so the snapshot is complete
            continue
        snap = fix.snapshot(now)
        _write_json(state_path(root), snap)
        if should_log(snap, last, now):
            last = record(root, snap, now)
            logged += 1
    return logged


def _serial_lines(device: str, baud: int):
    import serial      # pyserial: on the Pi only (requirements-sensors.txt)
    with serial.Serial(device, baud, timeout=2) as port:
        while True:
            raw = port.readline()
            if raw:
                yield raw.decode("ascii", errors="replace")


def run(root: Path = DECK, device: str = DEVICE, baud: int = BAUD) -> int:
    try:
        lines = _serial_lines(device, baud)
        first = next(lines)
    except Exception as exc:  # noqa: BLE001  (no port, permission, nothing wired)
        _write_json(state_path(root), {"fix": False, "error": f"{device}: {exc}"[:200], "at": time.time()})
        log.error("no GPS on %s: %s", device, exc)
        return 1
    log.info("deck-gps reading %s at %d baud", device, baud)

    def stream():
        yield first
        yield from lines
    process(root, stream())
    return 0


# ---------- CLI ----------

def _fmt_state(s: dict, now: float) -> str:
    if not s:
        return "no data (is deck-gps.service running?)"
    if s.get("error"):
        return f"error  {s['error']}"
    age = now - s.get("at", 0)
    stale = f"  (stale {int(age)} s)" if age > STALE_S else ""
    if not s.get("fix"):
        return f"no fix yet, {s.get('sats', 0)} satellites{stale}  (needs open sky; first fix can take minutes)"
    unc = f" ±{s['uncertainty_m']} m" if s.get("uncertainty_m") is not None else ""
    alt = f"  alt {s['alt_m']} m" if s.get("alt_m") is not None else ""
    spd = f"  {s['speed_kmh']} km/h" if s.get("speed_kmh") is not None else ""
    return f"fix  {s['lat']:.6f}, {s['lon']:.6f}{unc}  {s.get('sats', 0)} sats{alt}{spd}{stale}"


def _fmt_entry(e: dict) -> str:
    try:
        where = f"{float(e['lat']):.6f}, {float(e['lon']):.6f}"
    except (KeyError, TypeError, ValueError):
        where = "?"
    return f"{e.get('at', '')}  {where}  {e.get('sats', '-')} sats"


def main(argv: list[str], root: Path = DECK, clock: Callable[[], float] = time.time,
         sleep: Callable[[float], None] = time.sleep) -> int:
    args = list(argv)
    if args == ["run"]:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s deck-gps %(message)s")
        return run(root)
    if not args or args == ["status"]:
        print(_fmt_state(_read_json(state_path(root)) or {}, clock()))
        last = recent(root, 1)
        print(f"logged {_fmt_entry(last[-1]) if last else '-'}")
        return 0
    if args == ["watch"]:
        try:
            while True:
                print(_fmt_state(_read_json(state_path(root)) or {}, clock()), flush=True)
                sleep(1)
        except KeyboardInterrupt:
            return 0
    if args[0] == "log":
        n = 20
        if len(args) == 3 and args[1] == "-n":
            n = int(args[2])
        for e in recent(root, n):
            print(_fmt_entry(e))
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
