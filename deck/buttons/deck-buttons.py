#!/usr/bin/env python3
"""Exo deck buttons + status lights.

Buttons (GPIO to GND, internal pull-ups) -> gestures -> actions.
Lights follow state files, so other services (voice loop, CRE, wallet) only need to touch a file.

  Approve  GPIO 5   NOWNodes key (keychain): one press = approve + send the oldest queued transaction
  Talk     GPIO 26  press = start listening at once (hooks/talk-start), release = send (hooks/talk-stop);
                    a press under 0.3 s = hooks/talk-cancel; two quick taps = hooks/repeat
  Camera   GPIO 6   tap = one photo (deck-capture snap), double = camera on/off
  Mic      GPIO 13  tap = memo start/stop (deck-capture mic toggle)
  Kiosk    GPIO 16  tap = dashboard <-> desktop (deck-kiosk toggle)   [optional button]

  LED Key    GPIO 17  in the key: blinks while transactions wait for approval; 3 fast flashes when one is sent
  LED Rec    GPIO 22  on while the clip reports it is recording
  LED OK     GPIO 23  flashes when state/verdict changes (approve 1x, cut 2x, refuse 3x)
  LED Wait   GPIO 24  blinks while state/waiting-key exists (over-cap: needs the cold wallet)
  LED Listen GPIO 25  on while Talk is held, or while state/listening exists

Approval queue (state/):
  tx-queue/*.json      transactions CRE has approved, waiting for the human (oldest name first)
  approve-mode         "manual" (default) or "auto"; auto approves only Guardian low-risk, auto_eligible,
                       unexpired items; everything else waits for the key. Turning auto on needs a key press
                       within 5 s of state/auto-request. Expired items move to tx-expired/ unsent.
  tx-approved/         a press (or auto) moves the oldest file here, then runs hooks/approve <file>,
                       which signs and broadcasts it through NOWNodes
  tx-sent              the signer rewrites this after each broadcast (e.g. the tx hash); the key flashes

Hooks are optional executables in /srv/deck/hooks/; a missing hook is logged, not an error.
"""
import logging, json, os, subprocess, sys, threading, time
from pathlib import Path

from gpiozero import Button, LED

sys.path.insert(0, os.environ.get("EXO_DECK_PKG", "/srv/deck/app/deck"))
from exo_deck import approvals   # noqa: E402

DECK = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
HOOKS, STATE = DECK / "hooks", DECK / "state"
QUEUE, APPROVED = STATE / "tx-queue", STATE / "tx-approved"
HOLD_S, DOUBLE_S, TALK_MIN_S = 0.6, 0.4, 0.3   # hold threshold, double-tap window, shortest real Talk
APPROVE_GAP_S = 1.0                              # at most one approval per second (bounce, auto mode)
BIN = {"capture": os.environ.get("DECK_CAPTURE", "/usr/local/bin/deck-capture"),
       "kiosk": os.environ.get("DECK_KIOSK", "/usr/local/bin/deck-kiosk")}

log = logging.getLogger("deck-buttons")


def run(*cmd):
    log.info("run %s", " ".join(cmd))
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def hook(name, *args):
    path = HOOKS / name
    if os.access(path, os.X_OK):
        run(str(path), *args)
    else:
        log.info("hook %s not installed %s", name, " ".join(args))


def mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


class Gestures:
    """Turns press/release into tap and double-tap. A long press is ignored."""

    def __init__(self, button, on_tap=None, on_double=None):
        self.on_tap, self.on_double = on_tap, on_double
        self.taps, self.timer, self.held = 0, None, False
        button.hold_time = HOLD_S
        button.when_pressed = lambda: setattr(self, "held", False)
        button.when_held = self._held
        button.when_released = self._released

    def _held(self):
        self.held = True
        self._cancel()
        self.taps = 0

    def _released(self):
        if self.held:
            return
        if not self.on_double:           # no double action: fire the tap at once
            self.on_tap and self.on_tap()
            return
        self.taps += 1
        self._cancel()
        if self.taps >= 2:
            self.taps = 0
            self.on_double()
        else:
            self.timer = threading.Timer(DOUBLE_S, self._single)
            self.timer.start()

    def _single(self):
        self.taps = 0
        self.on_tap and self.on_tap()

    def _cancel(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None


class Deck:
    def __init__(self):
        self.key = Button(5, bounce_time=0.05)
        self.talk, self.camera, self.mic, self.kiosk = Button(26), Button(6), Button(13), Button(16)
        self.led = {k: LED(p) for k, p in
                    {"key": 17, "rec": 22, "ok": 23, "wait": 24, "listen": 25}.items()}
        self.talking, self.talk_down, self.talk_tap_at = False, 0.0, 0.0
        self.last_approve = 0.0
        self.flashing = {"key": False, "ok": False}
        self.key.when_pressed = self.key_pressed
        self.talk.when_pressed = self.talk_start
        self.talk.when_released = self.talk_released
        Gestures(self.camera, on_tap=lambda: run(BIN["capture"], "snap"),
                 on_double=lambda: run(BIN["capture"], "camera", "toggle"))
        Gestures(self.mic, on_tap=lambda: run(BIN["capture"], "mic", "toggle"))
        Gestures(self.kiosk, on_tap=lambda: run(BIN["kiosk"], "toggle"))
        self.last_verdict = mtime(STATE / "verdict")
        self.last_sent = mtime(STATE / "tx-sent")

    # --- approval key -------------------------------------------------
    def approve(self, item, source):
        now = time.time()
        if now - self.last_approve < APPROVE_GAP_S:
            return False
        self.last_approve = now
        APPROVED.mkdir(parents=True, exist_ok=True)
        dest = APPROVED / item["file"]
        try:
            (QUEUE / item["file"]).replace(dest)       # atomic: approved at most once
        except OSError:
            return False
        log.info("approved %s (%s)", dest.name, source)
        hook("approve", str(dest))
        return True

    def key_pressed(self):
        now = time.time()
        if approvals.confirm_auto(STATE, now):          # a press right after "auto approve on"
            log.info("auto-approve turned on by key")
            self.flash("key", 2)
            return
        item = approvals.next_manual(approvals.pending(STATE, now))
        if item:
            self.approve(item, "key")
        else:
            log.info("approve (key): queue empty")

    # --- talk -------------------------------------------------------------
    def talk_start(self):
        self.talking, self.talk_down = True, time.time()
        hook("talk-start")

    def talk_released(self):
        if not self.talking:
            return
        self.talking = False
        now = time.time()
        if now - self.talk_down >= TALK_MIN_S:
            hook("talk-stop")
            return
        hook("talk-cancel")
        if now - self.talk_tap_at <= DOUBLE_S + TALK_MIN_S:
            self.talk_tap_at = 0.0
            hook("repeat")
        else:
            self.talk_tap_at = now

    # --- lights -----------------------------------------------------------
    def clip_recording(self):
        try:
            clip = json.loads((DECK / "clip-status.json").read_text())
            return time.time() - clip["seen"] < 10 and str(clip.get("rec")) == "1"
        except Exception:
            return False

    def flash(self, led, times):
        def go():
            self.flashing[led] = True
            for _ in range(times):
                self.led[led].on(); time.sleep(0.12)
                self.led[led].off(); time.sleep(0.12)
            self.flashing[led] = False
        threading.Thread(target=go, daemon=True).start()

    def tick(self, now):
        items = approvals.pending(STATE, now)
        if approvals.mode(STATE) == "auto":
            item = approvals.next_auto(items, now)
            if item and self.approve(item, "auto"):
                items = [i for i in items if i["file"] != item["file"]]
        waiting = bool(items)          # anything left needs the key, in either mode

        blink = int(now * 2) % 2 == 0
        if not self.flashing["key"]:
            self.led["key"].value = waiting and blink
        self.led["listen"].value = self.talking or (STATE / "listening").exists()
        self.led["rec"].value = self.clip_recording()
        self.led["wait"].value = (STATE / "waiting-key").exists() and blink

        mt = mtime(STATE / "tx-sent")
        if mt and mt != self.last_sent:
            self.last_sent = mt
            self.flash("key", 3)

        mt = mtime(STATE / "verdict")
        if mt and mt != self.last_verdict:
            self.last_verdict = mt
            text = (STATE / "verdict").read_text().strip().lower()
            self.flash("ok", {"approve": 1, "cut": 2, "refuse": 3}.get(text.split()[0] if text else "", 1))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for d in (STATE, QUEUE, APPROVED):
        d.mkdir(parents=True, exist_ok=True)
    deck = Deck()
    log.info("deck-buttons ready (approve mode: %s)", approvals.mode(STATE))
    while True:
        deck.tick(time.time())
        time.sleep(0.1)


if __name__ == "__main__":
    main()
