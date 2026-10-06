from __future__ import annotations

import hashlib
import hmac
import logging
import time
from functools import wraps

from flask import Flask, jsonify, request

from .config import Config
from .kanban import KanbanError
from .talk import TalkError

log = logging.getLogger("exo-bridge")
BOARD_FIELDS = ("id", "title", "assignee", "status", "created_at", "completed_at", "result")
MAX_BODY_BYTES = 16 * 1024


def _text(body, field="text", limit=2000):
    v = body.get(field) if isinstance(body, dict) else None
    if not isinstance(v, str) or not v.strip() or len(v) > limit:
        return None
    return v.strip()


def create_app(cfg: Config, kanban, talker, blueprints=()) -> Flask:
    """`blueprints` is the extension point: the Guardian and kiosk routes register here.
    Every route of every blueprint is behind the bearer token."""
    app = Flask("exo-bridge")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    expected = f"Bearer {cfg.token}".encode()

    def authed(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            return _gate() or fn(*a, **kw)
        return wrapper

    def _gate():
        got = request.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(got, expected):
            return jsonify(error="unauthorized"), 401
        return None

    @app.errorhandler(413)
    def too_big(_):
        return jsonify(error="request too large"), 413

    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.post("/talk")
    @authed
    def talk():
        body = request.get_json(silent=True)
        text = _text(body)
        if text is None:
            return jsonify(error="text must be 1-2000 characters"), 400
        session = body.get("session")
        session = session[:100] if isinstance(session, str) else ""
        try:
            return jsonify(reply=talker.ask(text, session))
        except TalkError as exc:
            log.warning("talk failed: %s", exc)
            return jsonify(error="hermes unavailable"), 502

    @app.post("/tasks")
    @authed
    def tasks():
        body = request.get_json(silent=True)
        text = _text(body)
        agent = body.get("agent") if isinstance(body, dict) else None
        if text is None or agent not in cfg.agents:
            return jsonify(error=f"need text and agent in {list(cfg.agents)}"), 400
        title = text if len(text) <= 80 else text[:77].rstrip() + "..."
        # Minute-bucketed: a double-fired hook returns the existing task, not a duplicate.
        idem = hashlib.sha256(f"{agent}|{text}|{int(time.time() // 60)}".encode()).hexdigest()[:24]
        try:
            task = kanban.create(title, text, agent, cfg.max_runtime, idem)
        except KanbanError as exc:
            log.warning("create failed: %s", exc)
            return jsonify(error="kanban failed"), 502
        if cfg.telegram_chat_id and task.get("id"):
            try:
                kanban.subscribe(task["id"], cfg.telegram_chat_id)
            except KanbanError as exc:   # the task exists; a missing ping is not worth failing it
                log.warning("subscribe failed: %s", exc)
        return jsonify(task=task), 201

    @app.get("/board")
    @authed
    def board():
        try:
            rows = kanban.list()
        except KanbanError as exc:
            log.warning("list failed: %s", exc)
            return jsonify(error="kanban failed"), 502
        return jsonify(tasks=[{k: t.get(k) for k in BOARD_FIELDS} for t in rows])

    for bp in blueprints:
        bp.before_request(_gate)
        app.register_blueprint(bp)

    return app
