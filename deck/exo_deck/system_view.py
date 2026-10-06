"""Deck health, clip status and wearable sensor tiles (ported from the retired dashboard.py).

Hermes reachability is not read here: the kiosk already probes the bridge /health (cached) for the
HERMES chip, and the Sensors panel reuses that value. No hostnames live in this module.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

CLIP_FRESH_S = 10


def _db_latest(db_path: Path, table: str, now: float, max_age_s: int = 30):
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(f"SELECT * FROM {table} ORDER BY ts DESC LIMIT 1").fetchone()
        finally:
            con.close()
        if row and row["ts"] >= now - max_age_s:
            return dict(row)
    except sqlite3.Error:
        pass
    return None


def _last_nfc(outbox: Path):
    nfc_dir = outbox / "nfc"
    if not nfc_dir.exists():
        return None
    for f in sorted(nfc_dir.glob("*.jsonl"), reverse=True)[:3]:
        try:
            lines = f.read_text().strip().splitlines()
            if lines:
                return json.loads(lines[-1])
        except (OSError, ValueError):
            continue
    return None


def _num(read, path, fn):
    try:
        return fn(read(Path(path)))
    except (OSError, ValueError, IndexError, KeyError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def panel(deck_root, now: float | None = None, read=lambda p: Path(p).read_text()) -> dict:
    root, now = Path(deck_root), time.time() if now is None else now
    try:
        clip = json.loads((root / "clip-status.json").read_text())
        if not isinstance(clip, dict):
            clip = {}
    except (OSError, ValueError):
        clip = {}
    online = isinstance(clip.get("seen"), (int, float)) and now - clip["seen"] < CLIP_FRESH_S
    try:
        free = round(shutil.disk_usage(root).free / 1e9, 1)
    except OSError:
        free = None
    db = root / "body.db"
    sensors = []
    for table, name, col, unit, age in (("environment", "Air temp", "temperature_c", "°C", 60),
                                        ("environment", "Humidity", "humidity_pct", "%", 60),
                                        ("environment", "Pressure", "pressure_hpa", "hPa", 60),
                                        ("environment", "Air (VOC)", "voc_index", "", 60),
                                        ("pulse", "Pulse", "bpm", "bpm", 30), ("activity", "Steps", "steps", "", 60)):
        row = _db_latest(db, table, now, age)
        if row and col in row:
            sensors.append({"name": name, "value": row[col], "unit": unit, "age_s": max(0, int(now - row["ts"]))})
    nfc = _last_nfc(root / "outbox")
    if nfc:
        ts = nfc.get("ts")
        sensors.append({"name": "NFC tag", "value": nfc.get("uid") or nfc.get("id") or "?", "unit": "",
                        "age_s": max(0, int(now - ts)) if isinstance(ts, (int, float)) else None})
    return {
        "deck": {"cpu_c": _num(read, "/sys/class/thermal/thermal_zone0/temp", lambda s: round(int(s) / 1000, 1)),
                 "load1": _num(read, "/proc/loadavg", lambda s: float(s.split()[0])),
                 "uptime_h": _num(read, "/proc/uptime", lambda s: round(float(s.split()[0]) / 3600, 1)),
                 "disk_free_gb": free},
        "clip": {"online": online, "recording": online and str(clip.get("rec")) == "1",
                 "sd": online and str(clip.get("sd")) == "1", "rssi": _int(clip.get("rssi")) if online else None},
        "sensors": sensors,
    }
