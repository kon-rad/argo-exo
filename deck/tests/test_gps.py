import json
from datetime import datetime

import pytest

from exo_deck import gps, oled

NOW = datetime(2026, 10, 15, 9, 41, 12).timestamp()
DAY = "2026-10-15"


def nmea(body):
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


# Kuala Lumpur-ish: 3°08.5000' N, 101°41.2000' E
RMC = nmea("GPRMC,014112.00,A,0308.5000,N,10141.2000,E,0.5,90.0,151026,,,A")
GGA = nmea("GPGGA,014112.00,0308.5000,N,10141.2000,E,1,08,1.2,56.3,M,0.0,M,,")
RMC_NOFIX = nmea("GPRMC,014112.00,V,,,,,,,151026,,,N")
GGA_NOFIX = nmea("GPGGA,014112.00,,,,,0,03,,,M,,M,,")


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def test_checksum():
    assert gps.checksum_ok(RMC)
    assert not gps.checksum_ok(RMC[:-2] + "00")
    assert not gps.checksum_ok("GPRMC,no,dollar")


def test_parse_rmc_and_gga():
    r = gps.parse(RMC)
    assert r["valid"] and r["lat"] == pytest.approx(3.141667, abs=1e-5) and r["lon"] == pytest.approx(101.686667, abs=1e-5)
    assert r["speed_kmh"] == pytest.approx(0.9)
    g = gps.parse(GGA)
    assert g["quality"] == 1 and g["sats"] == 8 and g["hdop"] == 1.2 and g["alt_m"] == 56.3


def test_parse_south_west_and_gn_talker():
    r = gps.parse(nmea("GNRMC,000000.00,A,3352.0000,S,15112.0000,W,0,0,010126,,,A"))
    assert r["lat"] == pytest.approx(-33.866667, abs=1e-5) and r["lon"] == pytest.approx(-151.2, abs=1e-5)


def test_parse_ignores_other_sentences_and_garbage():
    assert gps.parse(nmea("GPGSV,1,1,00")) is None
    assert gps.parse("garbage") is None
    assert gps.parse(RMC.replace("A,0308", "A,03X8")) is None      # checksum no longer matches


def test_no_fix_hides_position():
    f = gps.Fix()
    f.feed(gps.parse(RMC_NOFIX), NOW)
    f.feed(gps.parse(GGA_NOFIX), NOW)
    s = f.snapshot(NOW)
    assert s["fix"] is False and s["sats"] == 3 and "lat" not in s


def test_snapshot_has_uncertainty_and_utc():
    f = gps.Fix()
    f.feed(gps.parse(RMC), NOW)
    f.feed(gps.parse(GGA), NOW)
    s = f.snapshot(NOW)
    assert s["fix"] and s["uncertainty_m"] == 6.0 and s["utc"] == "2026-10-15T01:41:12Z"


def test_should_log_rules():
    here = {"fix": True, "lat": 3.0, "lon": 101.0}
    assert gps.should_log(here, None, NOW)
    last = {"ts": NOW - 10, "lat": 3.0, "lon": 101.0}
    assert not gps.should_log(here, last, NOW)                             # same place, too soon
    assert gps.should_log(here, {**last, "ts": NOW - 301}, NOW)            # time passed
    assert gps.should_log({**here, "lat": 3.001}, last, NOW)               # moved ~111 m
    assert not gps.should_log({"fix": False}, None, NOW)


def test_process_writes_state_log_and_lifeos(tmp_path):
    n = gps.process(tmp_path, [RMC, GGA, RMC, GGA], Clock())
    assert n == 1
    st = json.loads((tmp_path / "state" / "gps.json").read_text())
    assert st["fix"] and st["lat"] == pytest.approx(3.141667, abs=1e-5)
    point = lines(tmp_path / "outbox" / "gps" / f"{DAY}.jsonl")[0]
    assert point["lat"] == pytest.approx(3.141667, abs=1e-5) and point["sats"] == 8 and point["alt_m"] == 56.3
    ev = lines(tmp_path / "outbox" / "lifeos" / f"{DAY}.ndjson")[0]
    assert ev["kind"] == "gps_fix" and ev["sensitivity"] == 2 and ev["source"] == "exo-edge"
    assert ev["lat"] == point["lat"] and ev["lon"] == point["lon"] and ev["dedupe_key"].startswith("gps:")


def test_process_no_fix_logs_nothing(tmp_path):
    assert gps.process(tmp_path, [RMC_NOFIX, GGA_NOFIX], Clock()) == 0
    assert not (tmp_path / "outbox" / "gps").exists()
    assert json.loads((tmp_path / "state" / "gps.json").read_text())["fix"] is False


def test_process_resumes_from_last_logged_point(tmp_path):
    gps.process(tmp_path, [RMC, GGA], Clock())
    assert gps.process(tmp_path, [RMC, GGA], Clock(NOW + 20)) == 0          # restart nearby, soon after
    assert gps.process(tmp_path, [RMC, GGA], Clock(NOW + 400)) == 1


def test_cli_status_and_log(tmp_path, capsys):
    assert gps.main([], tmp_path, Clock()) == 0
    assert "no data" in capsys.readouterr().out
    gps.process(tmp_path, [RMC, GGA], Clock())
    gps.main([], tmp_path, Clock(NOW + 1))
    out = capsys.readouterr().out
    assert "fix  3.141667, 101.686667" in out and "8 sats" in out
    gps.main([], tmp_path, Clock(NOW + 60))
    assert "stale" in capsys.readouterr().out
    gps.main(["log", "-n", "5"], tmp_path, Clock())
    assert "3.141667" in capsys.readouterr().out


def test_oled_gps_screen(tmp_path):
    ctx = oled.Ctx(tmp_path, clock=Clock(), get=lambda url: {})
    assert oled.render("gps", ctx, oled.Custom(tmp_path)) == ["GPS", "no data"]
    gps.process(tmp_path, [RMC, GGA], Clock())
    lines_ = oled.render("gps", ctx, oled.Custom(tmp_path))
    assert lines_[0] == "GPS 8 sats" and lines_[1] == "3.14167" and lines_[2] == "101.68667"
    assert all(len(x) <= oled.WIDTH for x in lines_)
