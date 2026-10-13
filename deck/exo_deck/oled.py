"""Exo chest OLED (0.96" SSD1315, I2C 0x3C): one programmable screen at a time.

  deck-oled                          what's showing, the rotation, on/off
  deck-oled list                     screens you can show (built-in + custom)
  deck-oled show <screen>            switch to one screen and stay there
  deck-oled rotate a,b,c [--every 10]
  deck-oled next                     next screen (a P button: deck-button set p2 tap "deck-oled next")
  deck-oled text "msg" [--for 60]    show a message, then go back
  deck-oled on | off                 display on or blank
  deck-oled hide on | off            mask wallet and portfolio amounts
  deck-oled run                      the service (deck-oled.service)

The CLI only writes /srv/deck/oled.json; the service re-reads it every second, so no restart is needed.
Custom screens: /srv/deck/oled/screens/<name>.py defining render(ctx) -> up to 4 strings. ctx has
deck_root, now, hide_amounts and panel(name), the kiosk's JSON for wallets / fund / body / sensors / ...
Display only: nothing here approves or signs. Transactions stay on the buttons.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger("deck-oled")

DECK = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
KIOSK = os.environ.get("EXO_KIOSK_URL", "http://127.0.0.1:8080")
WIDTH, LINES, LINE_H = 21, 4, 16         # 128x64 with the default 6 px font: 21 chars, 4 rows of 16 px
DIM_AFTER_S, SHIFT_EVERY_S, PANEL_TTL_S = 30, 300, 5
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
DEFAULT = {"screen": "status", "rotation": [], "every": 10, "offset": 0, "on": True, "hide_amounts": False,
           "text": "", "text_until": 0, "sleep_after": 0}
MASK = "***"


def conf_path(root: Path) -> Path:
    return root / "oled.json"


def flash_path(root: Path) -> Path:
    return root / "state" / "oled-flash.json"


def load(root: Path = DECK) -> dict:
    try:
        conf = json.loads(conf_path(root).read_text())
        if not isinstance(conf, dict):
            conf = {}
    except (OSError, ValueError):
        conf = {}
    return {**DEFAULT, **{k: v for k, v in conf.items() if k in DEFAULT}}


def save(conf: dict, root: Path = DECK) -> None:
    p = conf_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(conf, indent=2) + "\n")
    tmp.replace(p)


def flash(lines: list[str], seconds: float, root: Path = DECK, now: float | None = None) -> None:
    """Show lines for a few seconds over whatever screen is up (deck-nfc uses this on a tap)."""
    p = flash_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"lines": [str(x) for x in lines][:LINES],
                               "until": (time.time() if now is None else now) + seconds}))
    tmp.replace(p)


def fit(lines) -> list[str]:
    """At most 4 printable lines of at most 21 characters."""
    out = []
    for line in list(lines or [])[:LINES]:
        s = "".join(ch if ch.isprintable() else " " for ch in str(line))
        out.append(s[:WIDTH])
    return out


def wrap(text: str) -> list[str]:
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        while len(w) > WIDTH:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:WIDTH])
            w = w[WIDTH:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= WIDTH:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:LINES]


# ---------- data for the screens ----------

def _http_get(url: str) -> dict:
    import requests
    r = requests.get(url, timeout=(1, 2))
    return r.json()


class Ctx:
    """What a screen can read. panel() answers from the kiosk's local API, cached for a few seconds."""

    def __init__(self, root: Path, clock: Callable[[], float] = time.time, get: Callable[[str], dict] = _http_get,
                 hide_amounts: bool = False):
        self.deck_root, self.clock, self.get, self.hide_amounts = Path(root), clock, get, hide_amounts
        self._cache: dict[str, tuple[float, dict]] = {}

    @property
    def now(self) -> float:
        return self.clock()

    def panel(self, name: str) -> dict:
        hit = self._cache.get(name)
        if hit and self.now - hit[0] < PANEL_TTL_S:
            return hit[1]
        try:
            data = self.get(f"{KIOSK}/api/panel/{name}")
            if not isinstance(data, dict):
                data = {"error": "bad reply"}
        except Exception:  # noqa: BLE001  (kiosk down, timeout, not JSON)
            data = {"error": "kiosk offline"}
        self._cache[name] = (self.now, data)
        return data

    def amount(self, s) -> str:
        return MASK if self.hide_amounts else str(s)


def _count(folder: Path, day: str) -> int:
    try:
        return sum(1 for p in folder.iterdir() if p.is_file() and
                   datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d") == day)
    except OSError:
        return 0


def _taps(root: Path, day: str) -> list[dict]:
    try:
        lines = (root / "outbox" / "nfc" / f"{day}.jsonl").read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
            if isinstance(e, dict):
                out.append(e)
        except ValueError:
            continue
    return out


def screen_status(ctx: Ctx) -> list[str]:
    st = ctx.deck_root / "state"
    t = datetime.fromtimestamp(ctx.now)
    rec = "REC VIDEO" if (st / "rec-video").exists() else "REC AUDIO" if (st / "rec-audio").exists() else "idle"
    mic = "mic ok" if (st / "pi-mic").exists() else "no mic"
    q = st / "tx-queue"
    waiting = len(list(q.glob("*.json"))) if q.is_dir() else 0      # counted only; never read or moved here
    return [t.strftime("%H:%M  %a %d %b"), rec, mic, f"{waiting} waiting" if waiting else "queue empty"]


def screen_wallet(ctx: Ctx) -> list[str]:
    d = ctx.panel("wallets")
    if d.get("error"):
        return ["WALLET", str(d["error"])]
    if d.get("loading"):
        return ["WALLET", "loading..."]
    rows = d.get("wallets") or []
    if not rows:
        return ["WALLET", "no wallets"]
    out = ["WALLET"]
    for w in rows[:LINES - 1]:
        native = str(w.get("native", "?"))
        amount, _, unit = native.partition(" ")
        val = f"{ctx.amount(amount)} {unit}".strip()
        label = str(w.get("label", ""))[:WIDTH - len(val) - 1]
        out.append(f"{label} {val}" + ("~" if w.get("stale") else ""))
    return out


def screen_portfolio(ctx: Ctx) -> list[str]:
    d = ctx.panel("fund")
    if d.get("error"):
        return ["PORTFOLIO", str(d["error"])]
    out = ["PORTFOLIO"]
    if d.get("nav"):
        out.append(f"NAV {ctx.amount(d['nav'])}")
    p = d.get("proposal")
    if p:
        out.append(f"{d.get('pending_total', 1)} open: {p.get('tally', '')}")
        out.append(MASK if ctx.hide_amounts else str(p.get("trade", "")))
    else:
        out.append("no open proposals")
    return out


def screen_qs(ctx: Ctx) -> list[str]:
    d = ctx.panel("body")
    last = d.get("last") if isinstance(d, dict) else None
    if not last:
        return ["BODY", str(d.get("error") or d.get("sync_error") or "no ring data")]

    def v(k, unit=""):
        x = last.get(k)
        return "-" if x is None else f"{round(x) if isinstance(x, float) else x}{unit}"
    mins = last.get("total_sleep_min")
    sleep = f"{int(mins) // 60}h{int(mins) % 60:02d}" if isinstance(mins, (int, float)) else "-"
    return [f"BODY {str(last.get('day', ''))[5:]}", f"sleep {v('sleep_score')} {sleep}",
            f"HRV {v('hrv_avg')} RHR {v('sleep_rhr')}", f"steps {v('steps_total')}"]


def screen_lifelog(ctx: Ctx) -> list[str]:
    r, day = ctx.deck_root, datetime.fromtimestamp(ctx.now).strftime("%Y-%m-%d")
    taps = _taps(r, day)
    last = taps[-1] if taps else None
    who = (last.get("label") or str(last.get("uid", ""))[-8:]) if last else "-"
    return ["LIFELOG today", f"photo {_count(r / 'outbox' / 'photos', day)} memo {_count(r / 'outbox' / 'audio', day)}",
            f"video {_count(r / 'media' / 'video', day)} nfc {len(taps)}", f"last tag {who}"]


def screen_env(ctx: Ctx) -> list[str]:
    d = ctx.panel("sensors")
    vals = {s.get("name"): s for s in d.get("sensors") or [] if isinstance(s, dict) and s.get("name") != "NFC tag"}
    if not vals:
        return ["AIR", str(d.get("error") or "no sensor data")]
    out = ["AIR"]
    for name in ("Air temp", "Humidity", "Pressure", "Air (VOC)"):
        s = vals.get(name)
        if s and len(out) < LINES:
            out.append(f"{name} {s.get('value')}{s.get('unit', '')}")
    return out


BUILTIN: dict[str, Callable[[Ctx], list[str]]] = {
    "status": screen_status, "wallet": screen_wallet, "portfolio": screen_portfolio, "qs": screen_qs,
    "lifelog": screen_lifelog, "env": screen_env,
}


class Custom:
    """Loads /srv/deck/oled/screens/<name>.py, reloading when the file changes."""

    def __init__(self, root: Path):
        self.dir = Path(root) / "oled" / "screens"
        self._mods: dict[str, tuple[float, object]] = {}

    def names(self) -> list[str]:
        try:
            return sorted(p.stem for p in self.dir.glob("*.py") if NAME.match(p.stem) and p.stem not in BUILTIN)
        except OSError:
            return []

    def get(self, name: str):
        p = self.dir / f"{name}.py"
        if not NAME.match(name) or not p.is_file():
            return None
        mtime = p.stat().st_mtime
        hit = self._mods.get(name)
        if hit and hit[0] == mtime:
            return hit[1]
        spec = importlib.util.spec_from_file_location(f"oled_screen_{name}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._mods[name] = (mtime, mod)
        return mod


def all_names(custom: Custom) -> list[str]:
    return list(BUILTIN) + custom.names()


def render(name: str, ctx: Ctx, custom: Custom) -> list[str]:
    """One screen's lines. A broken screen shows its error; it never takes the service down."""
    try:
        if name in BUILTIN:
            return fit(BUILTIN[name](ctx))
        mod = custom.get(name)
        if mod is None:
            return fit([name, "no such screen"])
        return fit(mod.render(ctx))
    except Exception as exc:  # noqa: BLE001
        log.warning("screen %s failed: %s", name, exc)
        return fit([name, "error:", type(exc).__name__])


# ---------- what goes on the glass this second ----------

@dataclass(frozen=True)
class Frame:
    lines: tuple = ()
    rec: bool = False
    dim: bool = False
    dx: int = 0
    dy: int = 0
    blank: bool = False


def current_screen(conf: dict, now: float) -> str:
    rot = [n for n in conf.get("rotation") or [] if isinstance(n, str)]
    if rot:
        every = max(2, int(conf.get("every") or 10))
        return rot[(int(now // every) + int(conf.get("offset") or 0)) % len(rot)]
    return conf.get("screen") or "status"


def frame(root: Path, conf: dict, now: float, ctx: Ctx, custom: Custom, conf_mtime: float = 0.0) -> Frame:
    st = root / "state"
    rec = (st / "rec-video").exists() or (st / "rec-audio").exists()
    idle = now - conf_mtime
    if not conf.get("on", True):
        return Frame(blank=True)
    sleep_after = conf.get("sleep_after") or 0
    if sleep_after and idle > sleep_after and not rec:
        return Frame(blank=True)
    lines = None
    try:
        f = json.loads(flash_path(root).read_text())
        if isinstance(f, dict) and f.get("until", 0) > now:
            lines, idle = fit(f.get("lines") or []), 0
    except (OSError, ValueError):
        pass
    if lines is None and conf.get("text") and conf.get("text_until", 0) > now:
        lines = wrap(conf["text"])
    if lines is None:
        lines = render(current_screen(conf, now), ctx, custom)
    k = int(now // SHIFT_EVERY_S)             # burn-in: move the whole image a pixel or two now and then
    return Frame(lines=tuple(lines), rec=rec, dim=idle > DIM_AFTER_S, dx=k % 3, dy=(k // 3) % 2)


class LumaDisplay:
    """The real SSD1315 through luma.oled's SSD1306 driver. Imported only here, so tests and the CLI never need it."""

    def __init__(self, port: int = 1, address: int = 0x3C, rotate: int = 0):
        from luma.core.interface.serial import i2c
        from luma.core.render import canvas
        from luma.oled.device import ssd1306
        self.canvas = canvas
        self.device = ssd1306(i2c(port=port, address=address), rotate=rotate)

    def draw(self, f: Frame) -> None:
        if f.blank:
            self.device.hide()
            return
        self.device.show()
        self.device.contrast(0x10 if f.dim else 0xCF)
        with self.canvas(self.device) as c:
            for i, line in enumerate(f.lines):
                c.text((f.dx, f.dy + i * LINE_H), line, fill="white")
            if f.rec:                         # recording dot on every screen, top right
                c.ellipse((121, 0, 127, 6), fill="white")


def run(root: Path = DECK, display=None, clock: Callable[[], float] = time.time, sleep=time.sleep,
        ticks: int | None = None) -> None:
    if display is None:
        display = LumaDisplay(address=int(os.environ.get("EXO_OLED_ADDRESS", "0x3C"), 16),
                              rotate=int(os.environ.get("EXO_OLED_ROTATE", "0")))
    custom, ctx, last = Custom(root), Ctx(root, clock=clock), None
    log.info("deck-oled running")
    n = 0
    while ticks is None or n < ticks:
        n += 1
        conf = load(root)
        ctx.hide_amounts = bool(conf.get("hide_amounts"))
        try:
            mtime = conf_path(root).stat().st_mtime
        except OSError:
            mtime = 0.0
        f = frame(root, conf, clock(), ctx, custom, mtime)
        if f != last:
            display.draw(f)
            last = f
        sleep(1)


# ---------- CLI ----------

def _flag(args: list[str], name: str, default: float) -> tuple[list[str], float]:
    if name in args:
        i = args.index(name)
        try:
            val = float(args[i + 1])
        except (IndexError, ValueError):
            raise SystemExit(f"{name} needs a number")
        return args[:i] + args[i + 2:], val
    return args, default


def show_conf(conf: dict, root: Path, now: float) -> None:
    rot = conf.get("rotation") or []
    print(f"display  {'on' if conf.get('on') else 'off'}")
    print(f"showing  {current_screen(conf, now)}")
    print(f"rotation {', '.join(rot) + ' every ' + str(conf.get('every')) + ' s' if rot else '-'}")
    print(f"amounts  {'hidden' if conf.get('hide_amounts') else 'shown'}")
    if conf.get("text") and conf.get("text_until", 0) > now:
        print(f"text     {conf['text']!r} for {int(conf['text_until'] - now)} s more")


def main(argv: list[str], root: Path = DECK, clock: Callable[[], float] = time.time) -> int:
    args, now = list(argv), clock()
    custom = Custom(root)
    names = all_names(custom)
    if args == ["run"]:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s deck-oled %(message)s")
        run(root)
        return 0
    conf = load(root)
    if not args or args == ["status"]:
        show_conf(conf, root, now)
        return 0
    if args == ["list"]:
        for n in names:
            print(n + ("" if n in BUILTIN else "  (custom)"))
        return 0
    cmd = args[0]
    if cmd == "show" and len(args) == 2 and args[1] in names:
        conf.update(screen=args[1], rotation=[], offset=0)
    elif cmd == "rotate" and len(args) >= 2:
        rest, every = _flag(args[1:], "--every", conf.get("every") or 10)
        rot = [n.strip() for n in ",".join(rest).split(",") if n.strip()]
        bad = [n for n in rot if n not in names]
        if not rot or bad:
            print(f"unknown screen: {', '.join(bad) or '(none given)'}; see deck-oled list", file=sys.stderr)
            return 1
        conf.update(rotation=rot, every=max(2, int(every)), offset=0)
    elif args == ["next"]:
        if conf.get("rotation"):
            conf["offset"] = int(conf.get("offset") or 0) + 1
        else:
            cur = conf.get("screen") if conf.get("screen") in names else names[0]
            conf["screen"] = names[(names.index(cur) + 1) % len(names)]
    elif cmd == "text" and len(args) >= 2:
        rest, secs = _flag(args[1:], "--for", 60)
        conf.update(text=" ".join(rest), text_until=now + max(1, secs))
    elif args in (["on"], ["off"]):
        conf["on"] = args == ["on"]
    elif cmd == "hide" and args[1:] in (["on"], ["off"]):
        conf["hide_amounts"] = args[1] == "on"
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 1
    save(conf, root)
    show_conf(conf, root, now)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
