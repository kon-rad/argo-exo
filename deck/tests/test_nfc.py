import json
from datetime import datetime

import pytest

from exo_deck import nfc, oled, system_view

NOW = datetime(2026, 10, 14, 9, 41, 12).timestamp()
DAY = "2026-10-14"


class FakeTag:
    def __init__(self, uid="04a1b2c3d4e5f6", records=None, ndef=True, writeable=True, corrupt=False):
        self.uid, self.records, self.ndef, self.writeable, self.corrupt = uid, list(records or []), ndef, writeable, corrupt

    def info(self):
        return {"uid": self.uid, "tag_type": "NTAG215", "ndef": list(self.records)}

    def write(self, kind, value):
        if not self.ndef:
            raise nfc.TagError("tag is not NDEF formatted")
        if not self.writeable:
            raise nfc.TagError("tag is read only")
        self.records = [{"type": kind, "value": value + ("x" if self.corrupt else "")}]


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_tap_logs_both_files_and_flashes_oled(tmp_path):
    e = nfc.handle(tmp_path, FakeTag(records=[{"type": "url", "value": "https://a.b"}]), NOW, nfc.Debounce())
    assert e["uid"] == "04A1B2C3D4E5F6" and e["kind"] == "nfc_tap"
    tap = lines(tmp_path / "outbox" / "nfc" / f"{DAY}.jsonl")[0]
    assert tap["ts"] == NOW and tap["at"].startswith("2026-10-14T09:41:12")
    ev = lines(tmp_path / "outbox" / "lifeos" / f"{DAY}.ndjson")[0]
    assert ev["type"] == "event" and ev["source"] == "exo-edge" and ev["kind"] == "nfc_tap"
    assert ev["start"] == tap["at"] and ev["sensitivity"] == 1
    assert ev["dedupe_key"].startswith("nfc:04A1B2C3D4E5F6:")
    assert ev["payload"]["ndef"] == [{"type": "url", "value": "https://a.b"}]
    f = oled.frame(tmp_path, oled.load(tmp_path), NOW + 1, oled.Ctx(tmp_path, clock=lambda: NOW), oled.Custom(tmp_path))
    assert f.lines == ("NFC C3D4E5F6", "09:41:12")


def test_lifeos_event_matches_schema_fields(tmp_path):
    e = nfc.handle(tmp_path, FakeTag(), NOW, nfc.Debounce())
    ev = nfc.lifeos_event(e, "nfc_tap")
    allowed = {"type", "source", "kind", "start", "end", "timeframe_end", "tz", "tz_origin", "time_uncertainty_ms",
               "lat", "lon", "coord_uncertainty_m", "title", "sensitivity", "raw_ref", "dedupe_key", "payload", "typed"}
    assert set(ev) <= allowed
    assert {"type", "source", "kind", "start", "dedupe_key", "sensitivity"} <= set(ev)


def test_kiosk_still_reads_last_tap(tmp_path):
    nfc.handle(tmp_path, FakeTag(), NOW, nfc.Debounce(), oled_flash=lambda *a, **k: None)
    tiles = system_view.panel(tmp_path, now=NOW + 3)["sensors"]
    tile = next(t for t in tiles if t["name"] == "NFC tag")
    assert tile["value"] == "04A1B2C3D4E5F6" and tile["age_s"] == 3


def test_debounce(tmp_path):
    db, tag = nfc.Debounce(5), FakeTag()
    assert nfc.handle(tmp_path, tag, NOW, db)
    assert nfc.handle(tmp_path, tag, NOW + 2, db) is None
    assert nfc.handle(tmp_path, tag, NOW + 8, db)
    assert nfc.handle(tmp_path, FakeTag(uid="04FF00"), NOW + 8.1, db)
    assert len(lines(tmp_path / "outbox" / "nfc" / f"{DAY}.jsonl")) == 3


def test_labels_name_taps(tmp_path):
    nfc.set_label(tmp_path, "04A1B2C3D4E5F6", "desk")
    e = nfc.handle(tmp_path, FakeTag(), NOW, nfc.Debounce())
    assert e["label"] == "desk"
    assert lines(tmp_path / "outbox" / "lifeos" / f"{DAY}.ndjson")[0]["title"] == "NFC desk"
    nfc.set_label(tmp_path, "04A1B2C3D4E5F6", "")
    assert nfc.labels(tmp_path) == {}


def test_write_verifies_labels_and_logs(tmp_path):
    req = nfc.arm_write(tmp_path, "url", "https://myargoquest.com", "desk", now=NOW)
    tag = FakeTag()
    e = nfc.handle(tmp_path, tag, NOW + 1, nfc.Debounce())
    assert e["kind"] == "nfc_write" and e["wrote"]["ok"]
    res = json.loads((tmp_path / "state" / "nfc-write-result.json").read_text())
    assert res["id"] == req["id"] and res["ok"] and res["uid"] == "04A1B2C3D4E5F6"
    assert nfc.labels(tmp_path)["04A1B2C3D4E5F6"] == "desk"
    assert not (tmp_path / "state" / "nfc-write.json").exists()     # one shot
    assert lines(tmp_path / "outbox" / "lifeos" / f"{DAY}.ndjson")[0]["kind"] == "nfc_write"
    assert nfc.handle(tmp_path, tag, NOW + 20, nfc.Debounce())["kind"] == "nfc_tap"


@pytest.mark.parametrize("tag,err", [(FakeTag(ndef=False), "not NDEF"), (FakeTag(writeable=False), "read only"),
                                     (FakeTag(corrupt=True), "did not match")])
def test_write_failures_are_reported(tmp_path, tag, err):
    nfc.arm_write(tmp_path, "text", "hello", "desk", now=NOW)
    nfc.handle(tmp_path, tag, NOW + 1, nfc.Debounce())
    res = json.loads((tmp_path / "state" / "nfc-write-result.json").read_text())
    assert not res["ok"] and err in res["error"]
    assert nfc.labels(tmp_path) == {}


def test_expired_write_is_dropped(tmp_path):
    nfc.arm_write(tmp_path, "url", "https://x.y", now=NOW, ttl=10)
    e = nfc.handle(tmp_path, FakeTag(), NOW + 11, nfc.Debounce())
    assert e["kind"] == "nfc_tap"
    assert not (tmp_path / "state" / "nfc-write-result.json").exists()


def test_arm_write_validation(tmp_path):
    with pytest.raises(ValueError):
        nfc.arm_write(tmp_path, "url", "myargoquest.com")
    with pytest.raises(ValueError):
        nfc.arm_write(tmp_path, "text", "x" * 501)
    with pytest.raises(ValueError):
        nfc.arm_write(tmp_path, "sign", "tx")


def test_cli_write_waits_for_service_result(tmp_path):
    t = [NOW]

    def sleep(s):            # the "service" handles a tap while the CLI waits
        t[0] += s
        if not (tmp_path / "state" / "nfc-write-result.json").exists():
            nfc.handle(tmp_path, FakeTag(), t[0], nfc.Debounce(), oled_flash=lambda *a, **k: None)
    assert nfc.main(["write", "url", "https://myargoquest.com", "--label", "door"], tmp_path, lambda: t[0], sleep) == 0
    assert nfc.labels(tmp_path)["04A1B2C3D4E5F6"] == "door"


def test_cli_write_times_out_without_service(tmp_path, capsys):
    t = [NOW]

    def sleep(s):
        t[0] += s
    assert nfc.main(["write", "text", "hi", "--timeout", "2"], tmp_path, lambda: t[0], sleep) == 2
    assert "is deck-nfc.service running" in capsys.readouterr().err
    assert not (tmp_path / "state" / "nfc-write.json").exists()


def test_cli_read_label_log(tmp_path, capsys):
    t = [NOW]

    def sleep(s):
        t[0] += s
        nfc.handle(tmp_path, FakeTag(), t[0], nfc.Debounce(), oled_flash=lambda *a, **k: None)
    assert nfc.main(["label", "04:a1:b2:c3:d4:e5:f6", "desk"], tmp_path) == 0
    assert nfc.main(["read", "--timeout", "5"], tmp_path, lambda: t[0], sleep) == 0
    out = capsys.readouterr().out
    assert "04A1B2C3D4E5F6  desk" in out
    assert nfc.main(["log", "-n", "5"], tmp_path) == 0
    assert nfc.main(["label", "zz", "x"], tmp_path) == 1
    assert nfc.main(["sign", "tx"], tmp_path) == 1


def test_nfcpy_adapter():
    class Uri:
        iri = "https://a.b"

    class Text:
        text = "hi"

    class Ndef:
        records = [Uri(), Text()]
        is_writeable = True

    class Tag:
        identifier = bytes.fromhex("04a1b2")
        product = "NXP NTAG215"
        type = "Type2Tag"
        ndef = Ndef()

    class Mod:
        UriRecord = staticmethod(lambda v: ("uri", v))
        TextRecord = staticmethod(lambda v: ("text", v))
    a = nfc.NfcpyTag(Tag(), Mod)
    assert a.info() == {"uid": "04A1B2", "tag_type": "NXP NTAG215",
                        "ndef": [{"type": "url", "value": "https://a.b"}, {"type": "text", "value": "hi"}]}
    a.write("url", "https://c.d")
    assert Tag.ndef.records == [("uri", "https://c.d")]
