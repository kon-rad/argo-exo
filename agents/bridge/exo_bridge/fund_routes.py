"""Argo Fund routes (plans/11-argo-fund-design §6). The bridge holds no fund keys and reads no fund files: every route
runs the `fund-trade` CLI (FUND_TRADE_CMD, e.g. "sudo -n -u hermes /home/hermes/…/fund/bin/fund-trade") with
validated arguments and relays its JSON. Unset FUND_TRADE_CMD → every route answers 503."""
from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
from typing import Callable

from flask import Blueprint, jsonify, request

log = logging.getLogger("exo-bridge")
FUND_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{1,3}$")
SIGNATURE = re.compile(r"^0x[0-9a-fA-F]{130}$")
TIMEOUTS = {"proposals": 20, "log": 20, "show": 20, "prepare": 60, "execute": 240, "refuse": 20}


def cli_runner(cmd: str) -> Callable[[list[str]], tuple[int, dict]]:
    base = shlex.split(cmd)

    def run(args: list[str]) -> tuple[int, dict]:
        p = subprocess.run([*base, *args], capture_output=True, text=True, timeout=TIMEOUTS.get(args[0], 30))
        try:
            return p.returncode, json.loads(p.stdout)
        except ValueError:
            log.warning("fund-trade %s: unreadable output (exit %s)", args[0], p.returncode)
            return 1, {"error": "fund-trade failed"}
    return run


def fund_blueprint(run: Callable[[list[str]], tuple[int, dict]] | None) -> Blueprint:
    bp = Blueprint("fund", __name__)

    def call(args: list[str], ok_status: int = 200):
        if run is None:
            return jsonify(error="fund not configured"), 503
        try:
            code, out = run(args)
        except subprocess.TimeoutExpired:
            return jsonify(error="fund-trade timed out"), 504
        except OSError:
            return jsonify(error="fund-trade unavailable"), 503
        if code != 0:
            return jsonify(error=str(out.get("error", "fund-trade failed"))[:300]), 409
        return jsonify(out), ok_status

    def valid(pid: str) -> bool:
        return bool(FUND_ID.match(pid))

    @bp.get("/fund/proposals")
    def proposals():
        return call(["proposals"])

    @bp.get("/fund/log")
    def trade_log():
        return call(["log"])

    @bp.get("/fund/proposals/<pid>")
    def show(pid):
        return call(["show", pid]) if valid(pid) else (jsonify(error="bad id"), 400)

    @bp.post("/fund/proposals/<pid>/prepare")
    def prepare(pid):
        return call(["prepare", pid]) if valid(pid) else (jsonify(error="bad id"), 400)

    @bp.post("/fund/proposals/<pid>/approve")
    def approve(pid):
        body = request.get_json(silent=True) or {}
        sig = body.get("signature") if isinstance(body, dict) else None
        if not valid(pid) or not isinstance(sig, str) or not SIGNATURE.match(sig):
            return jsonify(error="bad id or signature"), 400
        return call(["execute", pid, sig])

    @bp.post("/fund/proposals/<pid>/refuse")
    def refuse(pid):
        return call(["refuse", pid]) if valid(pid) else (jsonify(error="bad id"), 400)

    return bp
