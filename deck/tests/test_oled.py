import json
import os
from datetime import datetime

from exo_deck import oled

NOW = datetime(2026, 10, 14, 9, 41).timestamp()


def ctx(tmp_path, panels=None, hide=False, now=NOW):
    def get(url):
        name = url.rsplit("/", 1)[1]
        if panels is None or name not in panels:
            raise ConnectionError("down")
        return panels[name]
    return oled.Ctx(tmp_path, clock=lambda: now, get=get, hide_amounts=hide)


def frame(tmp_path, conf=None, now=NOW, panels=None, mtime=None):
    c = {**oled.DEFAULT, **(conf or {})}
    return oled.frame(tmp_path, c, now, ctx(tmp_path, panels, now=now), oled.Custom(tmp_path),
                      now if mtime is None else mtime)


def test_defaults_and_status_screen(tmp_path):
    assert oled.load(tmp_path)["screen"] == "status"
    (tmp_path / "state" / "tx-queue").mkdir(parents=True)
    (tmp_path / "state" / "tx-queue" / "a.json").write_text("{}")
    (tmp_path / "state" / "pi-mic").write_text("")
    f = frame(tmp_path)
    assert f.lines[0].startswith("09:41")
    assert f.lines[1:] == ("idle", "mic ok", "1 waiting")
    assert not f.rec and not f.blank
    assert (tmp_path / "state" / "tx-queue" / "a.json").exists()     # counted, never moved


def test_recording_dot_on_every_screen(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "rec-video").write_text("")
    assert frame(tmp_path).rec
    assert frame(tmp_path, {"screen": "lifelog"}).rec
    assert frame(tmp_path).lines[1] == "REC VIDEO"


def test_wallet_screen_and_hidden_amounts(tmp_path):
    panels = {"wallets": {"wallets": [{"label": "hot", "native": "0.0421 ETH"},
                                      {"label": "savings", "native": "1.5 ETH", "stale": True}]}}
    c = ctx(tmp_path, panels)
    assert oled.screen_wallet(c) == ["WALLET", "hot 0.0421 ETH", "savings 1.5 ETH~"]
    c.hide_amounts = True
    assert oled.screen_wallet(c)[1] == "hot *** ETH"


def test_kiosk_down_shows_offline_not_crash(tmp_path):
    f = frame(tmp_path, {"screen": "wallet"})
    assert f.lines == ("WALLET", "kiosk offline")
    assert oled.screen_qs(ctx(tmp_path))[1] == "kiosk offline"


def test_panel_cache(tmp_path):
    calls = []
    t = [NOW]

    def get(url):
        calls.append(url)
        return {"wallets": []}
    c = oled.Ctx(tmp_path, clock=lambda: t[0], get=get)
    c.panel("wallets"); c.panel("wallets")
    assert len(calls) == 1
    t[0] += oled.PANEL_TTL_S + 1
    c.panel("wallets")
    assert len(calls) == 2


def test_qs_portfolio_env(tmp_path):
    panels = {"body": {"last": {"day": "2026-10-13", "sleep_score": 82, "total_sleep_min": 431, "hrv_avg": 48.6,
                                "sleep_rhr": 52, "steps_total": 9120}},
              "fund": {"nav": "$512.40", "pending_total": 2,
                       "proposal": {"tally": "3-2 yes", "trade": "Sell 10 USDC for ETH"}},
              "sensors": {"sensors": [{"name": "Air temp", "value": 24.1, "unit": "°C"},
                                      {"name": "NFC tag", "value": "04AB", "unit": ""}]}}
    c = ctx(tmp_path, panels)
    assert oled.screen_qs(c) == ["BODY 10-13", "sleep 82 7h11", "HRV 49 RHR 52", "steps 9120"]
    assert oled.screen_portfolio(c) == ["PORTFOLIO", "NAV $512.40", "2 open: 3-2 yes", "Sell 10 USDC for ETH"]
    assert oled.screen_env(c) == ["AIR", "Air temp 24.1°C"]
    c.hide_amounts = True
    assert oled.screen_portfolio(c)[1] == "NAV ***" and oled.screen_portfolio(c)[3] == "***"


def test_lifelog_counts_today(tmp_path):
    for sub, n in (("outbox/photos", 2), ("outbox/audio", 1), ("media/video", 0)):
        d = tmp_path / sub
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"{i}.jpg").write_text("x")
            os.utime(d / f"{i}.jpg", (NOW, NOW))
    old = tmp_path / "outbox" / "photos" / "old.jpg"
    old.write_text("x")
    os.utime(old, (NOW - 86400 * 2, NOW - 86400 * 2))
    (tmp_path / "outbox" / "nfc").mkdir(parents=True)
    (tmp_path / "outbox" / "nfc" / "2026-10-14.jsonl").write_text(
        json.dumps({"uid": "04A1B2C3D4E5F6"}) + "\n" + json.dumps({"uid": "04FF", "label": "desk"}) + "\n")
    assert oled.screen_lifelog(ctx(tmp_path)) == ["LIFELOG today", "photo 2 memo 1", "video 0 nfc 2", "last tag desk"]


def test_text_flash_and_off(tmp_path):
    f = frame(tmp_path, {"text": "Gate 3 at 14:00 boarding now", "text_until": NOW + 10})
    assert f.lines == ("Gate 3 at 14:00", "boarding now")
    assert frame(tmp_path, {"text": "old", "text_until": NOW - 1}).lines[1] == "idle"
    oled.flash(["NFC  desk", "09:41"], 3, tmp_path, now=NOW)
    assert frame(tmp_path).lines == ("NFC  desk", "09:41")
    assert frame(tmp_path, now=NOW + 4).lines[1] == "idle"
    assert frame(tmp_path, {"on": False}).blank


def test_dim_sleep_and_shift(tmp_path):
    assert not frame(tmp_path, mtime=NOW - 5).dim
    assert frame(tmp_path, mtime=NOW - 60).dim
    assert frame(tmp_path, {"sleep_after": 120}, mtime=NOW - 200).blank
    a, b = frame(tmp_path, now=NOW), frame(tmp_path, now=NOW + oled.SHIFT_EVERY_S)
    assert (a.dx, a.dy) != (b.dx, b.dy)


def test_rotation_and_next(tmp_path):
    clock = lambda: 1000.0
    assert oled.main(["rotate", "wallet,qs", "lifelog", "--every", "10"], tmp_path, clock) == 0
    conf = oled.load(tmp_path)
    assert conf["rotation"] == ["wallet", "qs", "lifelog"] and conf["every"] == 10
    first = oled.current_screen(conf, 1000.0)
    oled.main(["next"], tmp_path, clock)
    assert oled.current_screen(oled.load(tmp_path), 1000.0) != first
    assert oled.main(["show", "qs"], tmp_path, clock) == 0
    conf = oled.load(tmp_path)
    assert conf["rotation"] == [] and oled.current_screen(conf, 0) == "qs"
    oled.main(["next"], tmp_path, clock)
    assert oled.load(tmp_path)["screen"] == "lifelog"


def test_cli_rejects_bad_input(tmp_path):
    assert oled.main(["show", "nope"], tmp_path) == 1
    assert oled.main(["rotate", "wallet,nope"], tmp_path) == 1
    assert oled.main(["hide", "maybe"], tmp_path) == 1
    assert not (tmp_path / "oled.json").exists()
    assert oled.main(["hide", "on"], tmp_path) == 0 and oled.load(tmp_path)["hide_amounts"]
    assert oled.main(["text", "hi", "--for", "30"], tmp_path, lambda: 100.0) == 0
    assert oled.load(tmp_path)["text_until"] == 130.0


def test_custom_screen_and_its_errors(tmp_path):
    d = tmp_path / "oled" / "screens"
    d.mkdir(parents=True)
    (d / "gm.py").write_text("def render(ctx):\n    return ['GM', 'x' * 40, 'a\\x07b', 'l4', 'l5']\n")
    (d / "broken.py").write_text("def render(ctx):\n    raise ValueError('boom')\n")
    (d / "Bad Name.py").write_text("")
    custom = oled.Custom(tmp_path)
    assert custom.names() == ["broken", "gm"]
    c = ctx(tmp_path)
    assert oled.render("gm", c, custom) == ["GM", "x" * 21, "a b", "l4"]
    assert oled.render("broken", c, custom) == ["broken", "error:", "ValueError"]
    assert oled.render("missing", c, custom) == ["missing", "no such screen"]
    assert oled.main(["show", "gm"], tmp_path) == 0


def test_run_draws_only_on_change(tmp_path):
    drawn = []

    class Fake:
        def draw(self, f):
            drawn.append(f)
    oled.run(tmp_path, display=Fake(), clock=lambda: NOW, sleep=lambda s: None, ticks=3)
    assert len(drawn) == 1 and drawn[0].lines[0].startswith("09:41")
