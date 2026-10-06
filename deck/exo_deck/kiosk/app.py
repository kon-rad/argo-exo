from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from flask import Flask, jsonify, send_from_directory

from .. import state as st

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("exo-kiosk")


def create_app(settings, providers: dict[str, Callable[[int], dict]], hermes_ok: Callable[[], bool],
               clock: Callable[[], float] = time.monotonic, hermes_ttl: float = 10.0) -> Flask:
    app = Flask("exo-kiosk", static_folder=str(STATIC), static_url_path="/static")

    cache: dict = {"at": None, "ok": False}

    def hermes_cached() -> bool:
        now = clock()
        if cache["at"] is None or now - cache["at"] >= hermes_ttl:
            try:
                cache["ok"] = bool(hermes_ok())
            except Exception:
                cache["ok"] = False
            cache["at"] = now
        return cache["ok"]

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/api/state")
    def state():
        snap = st.snapshot(settings.state)
        snap["hermes"] = hermes_cached()
        return jsonify(snap)

    @app.get("/api/panel/<name>")
    def panel(name):
        if name not in providers:
            return jsonify(error="unknown panel"), 404
        try:
            return jsonify(providers[name](st.snapshot(settings.state)["page"]))
        except Exception as exc:          # one broken source must not blank the kiosk
            log.warning("panel %s failed: %s", name, exc)
            return jsonify(error=str(exc))

    @app.post("/api/nav/<target>")
    def nav(target):
        new = st.set_panel(settings.state, target)
        return (jsonify(panel=new), 200) if new else (jsonify(error="unknown panel"), 400)

    return app
