import json
import sqlite3

from exo_deck import system_view as sv

NOW = 1_800_000_000


def test_clip_and_deck(tmp_path):
    (tmp_path / "clip-status.json").write_text(json.dumps({"seen": NOW - 3, "rec": "1", "sd": "1", "rssi": -61}))
    files = {"/sys/class/thermal/thermal_zone0/temp": "52300\n", "/proc/loadavg": "0.42 0.30 0.20 1/200 999\n",
             "/proc/uptime": "7200.5 100.0\n"}
    p = sv.panel(tmp_path, NOW, read=lambda path: files[str(path)])
    assert p["deck"]["cpu_c"] == 52.3 and p["deck"]["load1"] == 0.42 and p["deck"]["uptime_h"] == 2.0
    assert p["clip"] == {"online": True, "recording": True, "sd": True, "rssi": -61}


def test_stale_clip_is_offline(tmp_path):
    (tmp_path / "clip-status.json").write_text(json.dumps({"seen": NOW - 60, "rec": "1"}))
    p = sv.panel(tmp_path, NOW, read=lambda path: (_ for _ in ()).throw(OSError()))
    assert p["clip"]["online"] is False and p["clip"]["recording"] is False and p["deck"]["cpu_c"] is None


def test_collector_writes_strings(tmp_path):
    # lifelog-collector stores query-string values, so rssi arrives as "-61"
    (tmp_path / "clip-status.json").write_text(json.dumps({"seen": NOW - 1, "rec": "0", "sd": "0", "rssi": "-61"}))
    p = sv.panel(tmp_path, NOW, read=lambda path: "")
    assert p["clip"] == {"online": True, "recording": False, "sd": False, "rssi": -61}


def test_no_files_degrades(tmp_path):
    p = sv.panel(tmp_path, NOW)
    assert p["clip"]["online"] is False and p["sensors"] == []


def test_sensor_tiles_and_nfc(tmp_path):
    con = sqlite3.connect(tmp_path / "body.db")
    con.execute("CREATE TABLE environment(ts REAL, temperature_c REAL, humidity_pct REAL, pressure_hpa REAL, voc_index REAL)")
    con.execute("CREATE TABLE pulse(ts REAL, bpm REAL)")
    con.execute("INSERT INTO environment VALUES (?,?,?,?,?)", (NOW - 5, 24.5, 48.0, 1011.0, 80))
    con.execute("INSERT INTO pulse VALUES (?,?)", (NOW - 500, 70))   # stale, hidden
    con.commit()
    con.close()
    (tmp_path / "outbox" / "nfc").mkdir(parents=True)
    (tmp_path / "outbox" / "nfc" / "a.jsonl").write_text(json.dumps({"uid": "04AB"}) + "\n")
    names = {s["name"]: s for s in sv.panel(tmp_path, NOW, read=lambda p: "")["sensors"]}
    assert names["Air temp"]["value"] == 24.5 and names["Air temp"]["age_s"] == 5
    assert "Pulse" not in names and names["NFC tag"]["value"] == "04AB"
