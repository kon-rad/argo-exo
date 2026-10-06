#!/usr/bin/env python3
"""Clip collector: on-demand capture control + uploads into /srv/deck/outbox/{photos,audio}/.

GET  /control         clip polls every ~2 s; reply "camera=0 mic=1 interval=30 snap=4"; records the clip's heartbeat
                      snap is a counter: each increase asks the clip for one photo
POST /upload/<name>   clip uploads a JPEG or WAV
State: /srv/deck/capture.json (written by the dashboard or deck-capture), reset to all-off at start.
"""
import hmac, json, os, re, time
from pathlib import Path
from flask import Flask, abort, request

DECK = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
OUTBOX = DECK / "outbox"
STATE = DECK / "capture.json"
CLIP = DECK / "clip-status.json"
TOKEN = os.environ["DECK_UPLOAD_TOKEN"]
NAME = re.compile(r"^(img|aud)_[A-Za-z0-9_]+\.(jpg|wav)$")
OFF = {"camera": False, "mic": False, "interval": 30, "snap": 0}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


def write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def read_state():
    try:
        return {**OFF, **json.loads(STATE.read_text())}
    except Exception:
        return dict(OFF)


def check_token():
    if not hmac.compare_digest(request.headers.get("X-Deck-Token", ""), TOKEN):
        abort(403)


@app.get("/control")
def control():
    check_token()
    write_json(CLIP, {"seen": time.time(), **{k: request.args.get(k) for k in ("rec", "cam", "mic", "sd", "rssi")}})
    st = read_state()
    return f"camera={int(bool(st['camera']))} mic={int(bool(st['mic']))} interval={int(st['interval'])} snap={int(st['snap'])}"


@app.post("/upload/<name>")
def upload(name):
    check_token()
    if not NAME.match(name):
        abort(400)
    dest = OUTBOX / ("photos" if name.startswith("img_") else "audio") / name
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(request.get_data())
    tmp.replace(dest)
    return "ok"


if __name__ == "__main__":
    # Privacy: every start begins with capture off. Keep the snap counter, or the clip would read
    # the reset as a new snap and take an unasked photo.
    write_json(STATE, {**OFF, "snap": read_state()["snap"], "updated": time.time()})
    app.run(host="0.0.0.0", port=8081)
