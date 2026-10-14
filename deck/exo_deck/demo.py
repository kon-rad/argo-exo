"""Demo mode: every kiosk panel shows scripted data instead of live sources, so the dashboard can be shown
anywhere (no bridge, no ring, no chain). On while state/demo exists (exo-demo on|off|toggle) or EXO_DEMO=1.
The kiosk shows a DEMO chip whenever it is on.

The Agents panel plays a ~72 s scenario on a loop: a voice request arrives, Hermes splits it into child tasks,
delegates them to the profiles, the profiles message each other, one child goes through the Guardian, and the
parent closes. Everything is a pure function of the wall clock, so every screen shows the same moment.
The wording in SCENARIO and the panel fixtures is placeholder demo text: edit it here.
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from . import approvals
from .agents_view import COLS

LOOP_S = 72.0

# (at_s, kind, payload). kinds: task (id, title, assignee, status, parent), move (id, status), say (who, to, text)
SCENARIO: list[tuple[float, str, tuple]] = [
    (0, "say", ("you", "hermes", "Brief me on Chainlink CRE before the 3 pm demo, and top up gas on Base.")),
    (0, "task", ("t-101", "Brief on CRE + top up Base gas", "hermes", "triage", None)),
    (5, "move", ("t-101", "running")),
    (6, "say", ("hermes", "team", "Splitting this into three tasks: research, notes, wallet check.")),
    (9, "task", ("t-102", "Summarise CRE workflows, with doc links", "researcher", "ready", "t-101")),
    (9, "task", ("t-103", "Find my CRE notes in the vault", "librarian", "ready", "t-101")),
    (9, "task", ("t-104", "Check Base gas and the hot wallet's ETH", "wallet", "ready", "t-101")),
    (14, "move", ("t-102", "running")),
    (14, "move", ("t-104", "running")),
    (15, "say", ("researcher", "hermes", "Reading docs.chain.link/cre: triggers, capabilities, consensus.")),
    (21, "move", ("t-104", "done")),
    (22, "say", ("wallet", "hermes", "Hot wallet 0.004 ETH on Base, under the 0.01 floor. Gas 0.02 gwei.")),
    (25, "task", ("t-105", "Propose a gas top-up: 25 USDC to ETH", "portfolio", "running", "t-101")),
    (26, "say", ("hermes", "portfolio", "Top up to the floor. Stay inside the daily cap.")),
    (29, "move", ("t-103", "running")),
    (33, "say", ("portfolio", "guardian", "Request: swap 25 USDC to ETH on Base, for gas.")),
    (34, "move", ("t-105", "blocked")),
    (38, "say", ("guardian", "portfolio", "CRE verdict: approve. Low risk, inside caps. Waiting for the key.")),
    (40, "move", ("t-103", "done")),
    (41, "say", ("librarian", "researcher", "Three notes: cre-explained, guardian-workflow, exo-cre-workflow.")),
    (45, "say", ("researcher", "librarian", "Got them. Merging your notes into the brief.")),
    (48, "say", ("you", "guardian", "Approve key pressed.")),
    (49, "move", ("t-105", "done")),
    (50, "say", ("portfolio", "hermes", "Sent: 25 USDC to 0.0089 ETH. Tx 0x3f9c…a91e confirmed.")),
    (55, "move", ("t-102", "done")),
    (56, "say", ("researcher", "hermes", "Brief done: 6 points, 4 sources, 2 of your notes linked.")),
    (60, "move", ("t-101", "done")),
    (61, "say", ("hermes", "you", "Brief is on the Talk panel. Base gas topped up, 0.0129 ETH now.")),
]

# Older finished work, so the Done column isn't empty at the start of each loop.
BACKGROUND = [
    ("t-097", "Add a BME680 tile to the Sensors panel", "builder", "done", None, 2 * 3600),
    ("t-095", "Weekly PnL summary for the DeFi desk", "trader", "done", None, 5 * 3600),
    ("t-093", "File the TOKEN2049 side-event list", "librarian", "done", None, 9 * 3600),
]


def enabled(state: Path, env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get("EXO_DEMO", "").lower() in ("1", "on", "true") or (state / "demo").exists()


def set_enabled(state: Path, on: bool) -> None:
    state.mkdir(parents=True, exist_ok=True)
    flag = state / "demo"
    if on:
        flag.write_text("on\n")
        flag.chmod(0o644)
    else:
        flag.unlink(missing_ok=True)


def _phase(now: float) -> tuple[float, float]:
    """Seconds into the current loop, and the wall-clock time the loop started."""
    t = now % LOOP_S
    return t, now - t


def board(now: float) -> tuple[list[dict], list[dict]]:
    """The scenario's tasks and messages as they stand at `now`."""
    t, start = _phase(now)
    tasks: dict[str, dict] = {}
    feed: list[dict] = []
    for tid, title, who, status, parent, age in BACKGROUND:
        tasks[tid] = {"id": tid, "title": title, "assignee": who, "status": status, "parent": parent,
                      "created_at": now - age}
    for at, kind, p in SCENARIO:
        if at > t:
            break
        if kind == "task":
            tid, title, who, status, parent = p
            tasks[tid] = {"id": tid, "title": title, "assignee": who, "status": status, "parent": parent,
                          "created_at": start + at}
        elif kind == "move":
            tasks[p[0]]["status"] = p[1]
        elif kind == "say":
            feed.append({"from": p[0], "to": p[1], "text": p[2], "ts": start + at})
    return list(tasks.values()), feed


def agents_panel(now: float, per_col: int = 3, feed_n: int = 3) -> dict:
    tasks, feed = board(now)
    cols = {c: [] for c in ("queued", "ready", "running", "done")}
    for task in sorted(tasks, key=lambda x: x["created_at"], reverse=True):
        col = cols[COLS[task["status"]]]
        if len(col) < per_col:
            col.append(task)
    return {"online": True, "cols": cols, "counts": dict(Counter(x["status"] for x in tasks)),
            "feed": list(reversed(feed[-feed_n:]))}


def _talk(now: float) -> dict:
    return {"turns": [
        {"ts": now - 600, "role": "you", "text": "What's on my calendar after lunch?"},
        {"ts": now - 595, "role": "hermes", "text": "NOWNodes meeting at 3 pm, then the Guardian demo at 4."},
        {"ts": now - 240, "role": "you", "text": "How did I sleep?"},
        {"ts": now - 236, "role": "hermes", "text": "5 h 40 m, HRV 48 ms. I've capped today's trades at 15 USDC."},
        {"ts": now - 60, "role": "you", "text": "Brief me on Chainlink CRE before the 3 pm demo."},
        {"ts": now - 55, "role": "hermes", "text": "On it. Researcher, librarian and wallet are working on it now."},
    ]}


def _approvals(now: float) -> dict:
    return {"mode": "manual", "pending_total": 2, "page": 0, "pending_more": 0, "labels": dict(approvals.COPY),
            "auto_request": False,
            "pending": [
                {"key": True, "risk": "low", "auto_eligible": True,
                 "explanation": "Swap 25 USDC for about 0.0089 ETH on Base, to keep gas above the 0.01 ETH floor. "
                                "Inside the 50 USDC daily cap.",
                 "summary": "portfolio: gas top-up for the hot wallet"},
                {"key": False, "risk": "medium", "auto_eligible": False,
                 "explanation": "Buy 15 USDC of ETH (DCA). Sleep was short, so the cap is 15, not 20.",
                 "summary": "trader: weekly DCA, reduced"},
            ],
            "approved": [{"summary": "DCA 20 USDC to ETH on Base"}, {"summary": "Gas top-up 10 USDC on Arbitrum"}]}


def _transactions(now: float) -> dict:
    rows = [
        ("Swap 20 USDC to ETH", "base", 3600, "", "executed"),
        ("Gas top-up 10 USDC to ETH", "arbitrum", 3 * 3600, "", "executed"),
        ("Swap 200 USDC to PEPE", "base", 5 * 3600, "outside mandate: memecoin", "refused"),
        ("Send 0.5 ETH to 0x71c…09f", "ethereum", 8 * 3600, "over cap: needs the cold key", "waiting_key"),
        ("Bridge 30 USDC to Base", "ethereum", 26 * 3600, "", "executed"),
    ]
    return {"counts": {"executed": 3, "refused": 1, "waiting_key": 1}, "more": 0,
            "rows": [{"summary": s, "chain": c, "created_at": now - a, "reason": r, "status": st}
                     for s, c, a, r, st in rows]}


def _wallets(now: float) -> dict:
    return {"loading": False, "more": 0, "errors": [], "age_s": 12, "wallets": [
        {"label": "Hot (agents)", "chain": "base", "address_short": "0x9a4…c21", "stale": False,
         "native": "0.0129 ETH", "tokens": [{"amount": "212.40", "symbol": "USDC"}], "tokens_more": 0},
        {"label": "Savings", "chain": "ethereum", "address_short": "0x5e2…7b0", "stale": False,
         "native": "0.84 ETH", "tokens": [{"amount": "1,500.00", "symbol": "USDC"}], "tokens_more": 0},
        {"label": "Cold (hardware)", "chain": "ethereum", "address_short": "0xc03…e15", "stale": False,
         "native": "2.10 ETH", "tokens": [], "tokens_more": 0},
    ]}


def _cre(now: float) -> dict:
    calls = [("guard", "gas top-up inside caps", 40, 412, "approve"),
             ("guard", "memecoin outside mandate", 5 * 3600, 388, "refuse"),
             ("guard", "0.5 ETH over the hot cap", 8 * 3600, 431, "cut"),
             ("sleep-cap", "sleep 5 h 40 m: cap 15 USDC", 9 * 3600, 296, "cut")]
    return {"handlers": [{"handler": "guard", "status": "simulated"}, {"handler": "sleep-cap", "status": "simulated"},
                         {"handler": "price-watch", "status": "planned"}],
            "calls_error": None, "more": False,
            "calls": [{"handler": h, "reason": r, "created_at": now - a, "latency_ms": ms, "verdict": v}
                      for h, r, a, ms, v in calls]}


def _body(now: float) -> dict:
    trend = [("2026-10-01", 82), ("2026-10-02", 77), ("2026-10-03", 71), ("2026-10-04", 85),
             ("2026-10-05", 79), ("2026-10-06", 74), ("2026-10-07", 63)]
    return {"last": {"day": "2026-10-07", "sleep_score": 63, "total_sleep_min": 340, "deep_min": 62, "rem_min": 71,
                     "hrv_avg": 48, "sleep_rhr": 58, "recovery_index": 61, "temp_deviation_c": 0.2},
            "trend": [{"day": d, "sleep_score": s} for d, s in trend], "synced_at": now - 1800, "sync_error": None}


def _sensors(now: float) -> dict:
    return {"deck": {"cpu_c": 52.1, "load1": 0.42, "disk_free_gb": 104.6, "uptime_h": 3.2},
            "clip": {"online": True, "recording": False, "sd": True, "rssi": -54},
            "sensors": [{"name": "Temp", "value": "27.4", "unit": "°C"}, {"name": "Humidity", "value": "68", "unit": "%"},
                        {"name": "Pressure", "value": "1008", "unit": "hPa"}, {"name": "Air (VOC)", "value": "41", "unit": "kΩ"},
                        {"name": "Heart rate", "value": "74", "unit": "bpm"}, {"name": "Steps", "value": "8,412", "unit": ""}]}


def _media(now: float) -> dict:
    kinds = ["photo", "photo", "video", "audio", "photo", "photo", "video", "photo", "audio"]
    where = ["both", "both", "sd", "both", "cloud", "both", "both", "sd", "cloud"]
    items = [{"rel": f"demo/{k}_{n}", "kind": k, "where": w, "mtime": now - 300 * n, "n": n + 1}
             for n, (k, w) in enumerate(zip(kinds, where))]
    return {"items": items, "page": 0, "total": 47, "sd_only": 6, "cloud_only": 11, "synced_at": now - 900, "open": None}


def _projects(now: float) -> dict:
    c = lambda **k: {"idea": 0, "designing": 0, "building": 0, "testing": 0, "live": 0, "parked": 0, "done": 0, **k}  # noqa: E731
    projects = [
        {"title": "Argo app", "stage": "live", "counts": c(building=1, testing=1, live=1, parked=2), "next": "Upload build 11", "needs_konrad": True},
        {"title": "Body track", "stage": "building", "counts": c(building=2, live=3), "next": "Finish the node", "needs_konrad": True},
        {"title": "Exo capture", "stage": "building", "counts": c(building=2, live=1, parked=1), "next": "Solder the I2S mic", "needs_konrad": True},
        {"title": "Second brain", "stage": "live", "counts": c(idea=1, designing=2, building=1, testing=1, live=1), "next": "One-database cutover", "needs_konrad": True},
        {"title": "argo-lifeos", "stage": "designing", "counts": c(idea=7, done=1), "next": "Phase 1 headless core", "needs_konrad": True},
    ]
    needs = [{"project": "argo-app", "feature": None, "title": "Argo app", "detail": "Upload build 11"},
             {"project": "exo-capture", "feature": "i2s-mic", "title": "I2S mic", "detail": "Solder the mic board"}]
    return {"view": "projects", "page": 0, "pages": 3, "totals": {"features": c(idea=13, building=7, testing=9, live=19)},
            "projects": projects, "needs": needs, "attention": [], "gaps": {"goals_uncovered": ["W3", "W4"]},
            "shipped_7d": 0, "fetched_at": now - 60, "stale": False, "error": None}


PANELS = {"talk": _talk, "agents": lambda now: agents_panel(now), "approvals": _approvals,
          "transactions": _transactions, "wallets": _wallets, "cre": _cre, "body": _body,
          "sensors": _sensors, "media": _media, "projects": _projects}


def panel(name: str, now: float) -> dict | None:
    fn = PANELS.get(name)
    return fn(now) if fn else None


def overlay_state(snap: dict, now: float) -> dict:
    """The status chips in demo mode: Hermes up, the demo's pending approvals, and the DEMO chip."""
    return dict(snap, demo=True, hermes=True, pending=_approvals(now)["pending_total"],
                heard=snap.get("heard") or "Brief me on Chainlink CRE before the 3 pm demo.",
                reply=snap.get("reply") or "On it. Researcher, librarian and wallet are working on it now.")
