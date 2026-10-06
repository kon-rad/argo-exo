#!/usr/bin/env python3
"""
Cyberdeck dashboard — VITURE kiosk at http://localhost:8080

Reads: /srv/deck/body.db (SQLite), /srv/deck/outbox/, Tailscale droplet ping.
Start: python3 /srv/deck/dashboard.py
Autostart: see cyberdeck-dashboard.service systemd unit
Env: DECK_DROPLET=hermes-droplet  (Tailscale hostname of the DO droplet)
"""
import json, os, time, subprocess, glob
from pathlib import Path
from flask import Flask, jsonify, Response
import sqlite3

DECK_ROOT = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
DB_PATH   = DECK_ROOT / "body.db"
OUTBOX    = DECK_ROOT / "outbox"
DROPLET   = os.environ.get("DECK_DROPLET", "hermes-droplet")

app = Flask(__name__)


# ─── data helpers ──────────────────────────────────────────────────────────────

def _db_latest(table: str, max_age_s: int = 30):
    if not DB_PATH.exists():
        return None
    try:
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        row = con.execute(
            f"SELECT * FROM {table} ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        con.close()
        if row and row["ts"] >= time.time() - max_age_s:
            return dict(row)
    except Exception:
        pass
    return None


def _last_nfc():
    nfc_dir = OUTBOX / "nfc"
    if not nfc_dir.exists():
        return None
    files = sorted(nfc_dir.glob("*.jsonl"), reverse=True)
    for f in files[:3]:
        try:
            for line in reversed(f.read_text().strip().splitlines()):
                return json.loads(line)
        except Exception:
            continue
    return None


def _outbox_count():
    total = 0
    for sub in ("nfc", "audio", "photos", "body"):
        d = OUTBOX / sub
        if d.exists():
            total += sum(1 for _ in d.iterdir())
    return total


def _droplet_ok():
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "2", DROPLET],
            capture_output=True, timeout=4
        )
        return r.returncode == 0
    except Exception:
        return False


# ─── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/data")
def api_data():
    pulse    = _db_latest("pulse",       max_age_s=30)
    activity = _db_latest("activity",    max_age_s=60)
    env      = _db_latest("environment", max_age_s=60)
    nfc      = _last_nfc()

    return jsonify({
        "ts":        int(time.time()),
        "bpm":       pulse["bpm"]              if pulse    else None,
        "steps":     activity["steps"]         if activity else None,
        "voc":       env["voc_index"]          if env      else None,
        "temp_c":    env["temperature_c"]      if env      else None,
        "humidity":  env["humidity_pct"]       if env      else None,
        "pressure":  env["pressure_hpa"]       if env      else None,
        "last_nfc":  nfc,
        "outbox":    _outbox_count(),
        "droplet":   _droplet_ok(),
    })


# ─── dashboard HTML (served at /) ─────────────────────────────────────────────

_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1920">
<title>Cyberdeck</title>
<style>
  :root {
    --bg:     #0a0a0f;
    --panel:  #111118;
    --border: #1e1e2e;
    --dim:    #44475a;
    --text:   #cdd6f4;
    --sub:    #7f849c;
    --red:    #f38ba8;
    --green:  #a6e3a1;
    --yellow: #f9e2af;
    --blue:   #89b4fa;
    --teal:   #94e2d5;
    --mauve:  #cba6f7;
    --glow-r: rgba(243,139,168,0.15);
    --glow-g: rgba(166,227,161,0.12);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: "JetBrains Mono", "Fira Mono", monospace;
    height: 100vh;
    display: grid;
    grid-template-rows: auto 1fr;
    overflow: hidden;
  }

  /* header */
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 18px 40px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  header .logo { font-size: 1.1rem; color: var(--mauve); letter-spacing: 0.15em; }
  header .clock { font-size: 2rem; font-weight: 700; letter-spacing: 0.08em; }
  header .date { font-size: 0.85rem; color: var(--sub); text-align: right; }

  /* grid */
  .grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    grid-template-rows: repeat(2, 1fr);
    gap: 20px;
    padding: 24px 40px;
  }

  .tile {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 28px 32px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    position: relative;
    overflow: hidden;
    transition: border-color 0.3s;
  }
  .tile::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    border-radius: 12px 12px 0 0;
  }
  .tile.red::before   { background: var(--red); }
  .tile.green::before { background: var(--green); }
  .tile.yellow::before{ background: var(--yellow); }
  .tile.blue::before  { background: var(--blue); }
  .tile.teal::before  { background: var(--teal); }
  .tile.mauve::before { background: var(--mauve); }

  .tile-label {
    font-size: 0.75rem;
    color: var(--sub);
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }
  .tile-value {
    font-size: 4rem;
    font-weight: 700;
    line-height: 1;
    letter-spacing: -0.02em;
  }
  .tile-unit {
    font-size: 1rem;
    color: var(--sub);
    align-self: flex-end;
    padding-bottom: 6px;
    margin-left: 8px;
  }
  .tile-row { display: flex; align-items: baseline; }
  .tile-sub {
    font-size: 0.9rem;
    color: var(--sub);
    margin-top: auto;
  }
  .tile-sub span { color: var(--text); }

  /* stale overlay */
  .stale .tile-value { opacity: 0.35; }
  .stale .tile-label::after { content: " · stale"; color: var(--dim); }

  /* link status dot */
  .dot {
    display: inline-block;
    width: 10px; height: 10px;
    border-radius: 50%;
    background: var(--dim);
    margin-right: 8px;
    vertical-align: middle;
  }
  .dot.ok  { background: var(--green); box-shadow: 0 0 8px var(--green); }
  .dot.err { background: var(--red);   box-shadow: 0 0 8px var(--red); }

  /* NFC tile specific */
  .nfc-uid   { font-size: 1.6rem; font-weight: 700; letter-spacing: 0.08em; font-family: monospace; }
  .nfc-type  { font-size: 0.85rem; color: var(--sub); margin-top: 4px; }
  .nfc-time  { font-size: 0.8rem; color: var(--dim); margin-top: auto; }

  /* VOC quality label */
  .voc-label { font-size: 1rem; font-weight: 600; padding: 4px 12px; border-radius: 6px; }
  .voc-good  { background: rgba(166,227,161,0.15); color: var(--green); }
  .voc-ok    { background: rgba(249,226,175,0.15); color: var(--yellow); }
  .voc-bad   { background: rgba(243,139,168,0.15); color: var(--red); }
</style>
</head>
<body>
<header>
  <div class="logo">■ CYBERDECK</div>
  <div class="clock" id="clock">--:--:--</div>
  <div class="date" id="date">----</div>
</header>

<div class="grid">
  <!-- BPM -->
  <div class="tile red" id="tile-bpm">
    <div class="tile-label">Heart Rate</div>
    <div class="tile-row">
      <div class="tile-value" id="bpm">--</div>
      <div class="tile-unit">bpm</div>
    </div>
    <div class="tile-sub" id="bpm-sub">&nbsp;</div>
  </div>

  <!-- Activity -->
  <div class="tile green" id="tile-act">
    <div class="tile-label">Activity</div>
    <div class="tile-row">
      <div class="tile-value" id="steps">--</div>
      <div class="tile-unit">steps</div>
    </div>
    <div class="tile-sub" id="act-sub">&nbsp;</div>
  </div>

  <!-- Sync / droplet -->
  <div class="tile mauve" id="tile-sync">
    <div class="tile-label">Link</div>
    <div class="tile-value" id="outbox" style="font-size:3rem">--</div>
    <div class="tile-sub">
      <span class="dot" id="dot"></span><span id="droplet-label">droplet</span>
    </div>
  </div>

  <!-- Temp + Humidity -->
  <div class="tile yellow" id="tile-env">
    <div class="tile-label">Environment</div>
    <div class="tile-row">
      <div class="tile-value" id="temp">--</div>
      <div class="tile-unit">°C</div>
    </div>
    <div class="tile-sub">Humidity: <span id="humidity">--</span>% &nbsp;·&nbsp; Pressure: <span id="pressure">--</span> hPa</div>
  </div>

  <!-- VOC / Air -->
  <div class="tile teal" id="tile-voc">
    <div class="tile-label">Air Quality</div>
    <div class="tile-row">
      <div class="tile-value" id="voc">--</div>
      <div class="tile-unit">IAQ</div>
    </div>
    <div class="tile-sub"><span id="voc-label" class="voc-label">&nbsp;</span></div>
  </div>

  <!-- NFC -->
  <div class="tile blue" id="tile-nfc">
    <div class="tile-label">Last NFC Tap</div>
    <div class="nfc-uid" id="nfc-uid">-- -- -- --</div>
    <div class="nfc-type" id="nfc-type">&nbsp;</div>
    <div class="nfc-time" id="nfc-time">&nbsp;</div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

function pad(n) { return String(n).padStart(2, "0"); }

function tick() {
  const now = new Date();
  $("clock").textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  $("date").textContent  = now.toLocaleDateString("en-US", { weekday:"short", year:"numeric", month:"short", day:"numeric" });
}
setInterval(tick, 1000);
tick();

function vocQuality(iaq) {
  if (iaq === null) return ["--", ""];
  if (iaq < 51)  return ["Good",    "voc-good"];
  if (iaq < 101) return ["OK",      "voc-ok"];
  if (iaq < 151) return ["Lightly polluted", "voc-ok"];
  return ["Polluted", "voc-bad"];
}

function timeAgo(ts) {
  if (!ts) return "";
  const delta = Math.floor(Date.now() / 1000 - ts);
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta/60)}m ago`;
  return `${Math.floor(delta/3600)}h ago`;
}

async function refresh() {
  try {
    const d = await fetch("/api/data").then(r => r.json());

    // BPM
    $("bpm").textContent = d.bpm !== null ? Math.round(d.bpm) : "--";
    $("tile-bpm").classList.toggle("stale", d.bpm === null);

    // Steps / activity
    $("steps").textContent = d.steps !== null ? d.steps.toLocaleString() : "--";
    $("tile-act").classList.toggle("stale", d.steps === null);

    // Temp / humidity / pressure
    $("temp").textContent     = d.temp_c     !== null ? d.temp_c.toFixed(1)  : "--";
    $("humidity").textContent = d.humidity   !== null ? Math.round(d.humidity) : "--";
    $("pressure").textContent = d.pressure   !== null ? Math.round(d.pressure) : "--";
    $("tile-env").classList.toggle("stale", d.temp_c === null);

    // VOC
    $("voc").textContent = d.voc !== null ? Math.round(d.voc) : "--";
    const [ql, qc] = vocQuality(d.voc);
    const lbl = $("voc-label");
    lbl.textContent = ql;
    lbl.className = "voc-label " + qc;
    $("tile-voc").classList.toggle("stale", d.voc === null);

    // Droplet / outbox
    const n = d.outbox;
    $("outbox").textContent = n + " pending";
    const dot = $("dot");
    dot.className = "dot " + (d.droplet ? "ok" : "err");
    $("droplet-label").textContent = d.droplet ? "droplet online" : "droplet unreachable";

    // NFC
    if (d.last_nfc) {
      $("nfc-uid").textContent  = d.last_nfc.uid || "???";
      $("nfc-type").textContent = d.last_nfc.tag_type || "";
      $("nfc-time").textContent = timeAgo(d.last_nfc.ts);
      $("tile-nfc").classList.remove("stale");
    } else {
      $("nfc-uid").textContent  = "-- -- -- --";
      $("nfc-type").textContent = "";
      $("nfc-time").textContent = "no recent tap";
      $("tile-nfc").classList.add("stale");
    }
  } catch (e) {
    console.warn("dashboard fetch failed:", e);
  }
}

setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(_HTML, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=False)
