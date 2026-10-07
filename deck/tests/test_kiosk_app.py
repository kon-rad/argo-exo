from exo_deck.config import Settings
from exo_deck.kiosk.app import create_app


def client(tmp_path, providers=None, hermes=lambda: True):
    s = Settings.from_env({"DECK_ROOT": str(tmp_path)})
    s.state.mkdir(parents=True)
    app = create_app(s, providers or {"talk": lambda page: {"turns": [], "page": page}}, hermes_ok=hermes)
    return app.test_client(), s


def test_state_endpoint(tmp_path):
    c, _ = client(tmp_path)
    j = c.get("/api/state").json
    assert j["panel"] == "talk" and j["hermes"] is True and j["mode"] == "manual"


def test_panel_endpoint_passes_page(tmp_path):
    c, s = client(tmp_path)
    (s.state / "page").write_text("2")
    assert c.get("/api/panel/talk").json == {"turns": [], "page": 2}
    assert c.get("/api/panel/nope").status_code == 404


def test_provider_error_is_reported_not_raised(tmp_path):
    def broken(page):
        raise RuntimeError("db locked")
    c, _ = client(tmp_path, {"talk": broken})
    r = c.get("/api/panel/talk")
    assert r.status_code == 200 and r.json == {"error": "db locked"}


def test_index_served(tmp_path):
    c, _ = client(tmp_path)
    assert b"exo-kiosk" in c.get("/").data


def test_nav_route(tmp_path):
    c, s = client(tmp_path)
    assert c.post("/api/nav/3").json == {"panel": "approvals"}
    assert c.post("/api/nav/zzz").status_code == 400


def test_hermes_probe_failure_is_offline(tmp_path):
    def boom():
        raise OSError("x")
    c, _ = client(tmp_path, hermes=boom)
    assert c.get("/api/state").json["hermes"] is False


def test_hermes_probe_is_cached(tmp_path):
    from exo_deck.kiosk.app import create_app as mk
    s = Settings.from_env({"DECK_ROOT": str(tmp_path)})
    s.state.mkdir(parents=True)
    calls, now = [], [0.0]
    app = mk(s, {}, hermes_ok=lambda: calls.append(1) or False, clock=lambda: now[0])
    c = app.test_client()
    c.get("/api/state"); c.get("/api/state")
    assert len(calls) == 1
    now[0] = 11
    c.get("/api/state")
    assert len(calls) == 2


def test_check_listen():
    import pytest
    from exo_deck.kiosk.__main__ import check_listen
    for bad in ("0.0.0.0:8080", "[::]:8080", "*:8080", ":8080", "127.0.0.1:8080 0.0.0.0:8080"):
        with pytest.raises(SystemExit):
            check_listen(bad)
    assert check_listen("127.0.0.1:8080") == "127.0.0.1:8080"


def _media_client(tmp_path):
    c, s = client(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / "outbox/photos").mkdir(parents=True)
    (tmp_path / "outbox/photos/a.jpg").write_bytes(b"jpegbytes")
    (tmp_path / "outbox/photos/link.jpg").symlink_to(tmp_path / ".env")
    (tmp_path / "outbox/photos_evil").mkdir()
    (tmp_path / "outbox/photos_evil/x.jpg").write_bytes(b"evil")
    return c


def test_media_file_serves_and_sets_nosniff(tmp_path):
    c = _media_client(tmp_path)
    r = c.get("/api/media/file/photos/a.jpg")
    assert r.data == b"jpegbytes" and r.headers["X-Content-Type-Options"] == "nosniff"


def test_media_file_refuses_traversal(tmp_path):
    c = _media_client(tmp_path)
    for bad in ("photos/../../.env", "..%2f.env", "photos/%2e%2e/%2e%2e/.env", "photos/link.jpg",
                "photos/../photos_evil/x.jpg", "photos_evil/x.jpg", "%2e%2e/.env", "state/panel", "photos/nope.jpg"):
        assert c.get(f"/api/media/file/{bad}").status_code == 404, bad
        assert c.get(f"/api/media/thumb/{bad}").status_code == 404, bad
    assert c.get("/api/media/file//etc/passwd", follow_redirects=True).status_code == 404


def test_media_file_refuses_non_media_extensions(tmp_path):
    c = _media_client(tmp_path)
    (tmp_path / "outbox/photos/x.html").write_text("<script>alert(1)</script>")
    assert c.get("/api/media/file/photos/x.html").status_code == 404
    assert c.get("/api/media/thumb/photos/x.html").status_code == 404


def test_build_providers_wires_agents_and_wallets(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from exo_deck.config import Settings
    from exo_deck.kiosk.__main__ import build_providers
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "packages/nownodes-py"))
    monkeypatch.setenv("EXO_WALLETS_FILE", str(tmp_path / "none.json"))
    p = build_providers(Settings.from_env({"DECK_ROOT": str(tmp_path)}))
    assert {"agents", "wallets"} <= set(p)
    assert p["agents"](0) == {"online": False, "cols": {}, "counts": {}}      # no bridge url: offline, not a crash
    assert p["wallets"](0)["wallets"] == []
