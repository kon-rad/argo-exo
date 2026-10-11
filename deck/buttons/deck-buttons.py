#!/usr/bin/env python3
"""Exo deck buttons + status lights.

Buttons (GPIO to GND, internal pull-ups) -> gestures -> actions.
Lights follow state files, so other services (voice loop, CRE, wallet) only need to touch a file.

  Approve  GPIO 5   NOWNodes key (keychain): press and release = approve + send the oldest queued transaction
                    (on release, and only if Mic stayed up: Approve + Mic is the panic chord)
  Panic    Approve + Mic held together for 2 s = hooks/freeze (ExoModule.freeze() from the deck's hot key)
  Talk     GPIO 26  press = start listening at once (hooks/talk-start), release = send (hooks/talk-stop);
                    a press under 0.3 s = hooks/talk-cancel; two quick taps = hooks/repeat
  Camera   GPIO 6   tap = one photo (deck-capture snap), double = video with sound on/off (deck-capture video toggle;
                    deck-recorder records the clip's stream + the Pi mic)
  Mic      GPIO 13  tap = audio memo start/stop (deck-capture mic toggle); runs alongside video
  P1       GPIO 16  programmable; default tap = next panel, double = kiosk <-> desktop
  P2       GPIO 27  programmable; nothing by default   [optional button]
                    P1/P2 run the commands in /srv/deck/buttons.json (tap / double / hold), re-read on every
                    press; set them with deck-button

  LED Key  GPIO 17  in the key: blinks while transactions wait for approval; 3 fast flashes when one is sent
  LED Rec  GPIO 22  blinking = video recording; solid = audio only; one short blink = a photo arrived
                    (listening, verdicts and the cold-key wait show in the glasses; those LEDs were dropped)

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
from exo_deck.chord import ChordState   # noqa: E402
from exo_deck.hookq import HookQueue   # noqa: E402

DECK = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
HOOKS, STATE = DECK / "hooks", DECK / "state"
BUTTONS, PHOTOS = DECK / "buttons.json", DECK / "outbox" / "photos"
QUEUE, APPROVED = STATE / "tx-queue", STATE / "tx-approved"
HOLD_S, DOUBLE_S, TALK_MIN_S = 0.6, 0.4, 0.3   # hold threshold, double-tap window, shortest real Talk
APPROVE_GAP_S = 1.0                              # at most one approval per second (bounce, auto mode)
BIN = {"python": os.environ.get("DECK_PY", "/srv/deck/venv/bin/python"),
       "capture": os.environ.get("DECK_CAPTURE", "/usr/local/bin/deck-capture"),
       "kiosk": os.environ.get("DECK_KIOSK", "/usr/local/bin/deck-kiosk")}

DEFAULT_BUTTONS = {"p1": {"tap": f"{BIN['python']} -m exo_deck.navcli next", "double": f"{BIN['kiosk']} toggle", "hold": ""},
                   "p2": {"tap": "", "double": "", "hold": ""}}

log = logging.getLogger("deck-buttons")


def program(button):
    try:
        conf = json.loads(BUTTONS.read_text())
    except Exception:
        conf = {}
    return {**DEFAULT_BUTTONS.get(button, {}), **conf.get(button, {})}


def run_programmed(button, gesture):
    cmd = program(button).get(gesture, "").strip()
    if not cmd:
        log.info("%s %s: not programmed", button, gesture)
        return
    log.info("%s %s: %s", button, gesture, cmd)
    try:
        subprocess.Popen(["/bin/sh", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, "PYTHONPATH": os.environ.get("EXO_DECK_PKG", "/srv/deck/app/deck")})
    except OSError as e:
        log.error("cannot run %s: %s", cmd, e)


def run(*cmd):
    log.info("run %s", " ".join(cmd))
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log.error("cannot run %s: %s", cmd[0], e)


def run_wait(name, *args):
    """Run a hook and wait for it to exit (used by the talk queue). Never raises on a missing binary."""
    path = HOOKS / name
    if not os.access(path, os.X_OK):
        log.info("hook %s not installed %s", name, " ".join(args))
        return
    log.info("run %s %s", path, " ".join(args))
    try:
        subprocess.run([str(path), *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("hook %s failed: %s", name, e)


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
    """Turns press/release into tap, double-tap and hold. has_double() says whether to wait for a second tap."""

    def __init__(self, button, on_tap=None, on_double=None, on_hold=None, has_double=None):
        self.on_tap, self.on_double, self.on_hold = on_tap, on_double, on_hold
        self.has_double = has_double or (lambda: bool(self.on_double))
        self.taps, self.timer, self.held = 0, None, False
        button.hold_time = HOLD_S
        button.when_pressed = lambda: setattr(self, "held", False)
        button.when_held = self._held
        button.when_released = self._released

    def _held(self):
        self.held = True
        self._cancel()
        self.taps = 0
        self.on_hold and self.on_hold()

    def _released(self):
        if self.held:
            return
        if not self.has_double():        # no double action: fire the tap at once
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
        self.talk, self.camera, self.mic = Button(26, bounce_time=0.03), Button(6, bounce_time=0.03), \
            Button(13, bounce_time=0.03)
        self.p1, self.p2 = Button(16, bounce_time=0.03), Button(27, bounce_time=0.03)
        self.led = {k: LED(p) for k, p in {"key": 17, "rec": 22}.items()}
        self.talking, self.talk_down, self.talk_tap_at = False, 0.0, 0.0
        self.talkq = HookQueue(run_wait)   # talk hooks run one at a time, in order
        self.last_approve = 0.0
        self.chord, self.chord_lock = ChordState(), threading.Lock()   # gpiozero callbacks vs tick()
        self.flashing = {"key": False, "rec": False}
        self.key.when_pressed = self.key_pressed
        self.key.when_released = self.key_released
        self.talk.when_pressed = self.talk_start
        self.talk.when_released = self.talk_released
        Gestures(self.camera, on_tap=lambda: run(BIN["capture"], "snap"),
                 on_double=lambda: run(BIN["capture"], "video", "toggle"))
        Gestures(self.mic, on_tap=lambda: run(BIN["capture"], "mic", "toggle"))
        mic_gesture_press = self.mic.when_pressed
        self.mic.when_pressed = lambda: (mic_gesture_press(), self.mic_pressed())
        for name, button in (("p1", self.p1), ("p2", self.p2)):
            Gestures(button, on_tap=lambda n=name: run_programmed(n, "tap"),
                     on_double=lambda n=name: run_programmed(n, "double"),
                     on_hold=lambda n=name: run_programmed(n, "hold"),
                     has_double=lambda n=name: bool(program(n).get("double", "").strip()))
        self.last_sent = mtime(STATE / "tx-sent")
        self.last_photo = mtime(PHOTOS)

    # --- approval key -------------------------------------------------
    def key_pressed(self):
        with self.chord_lock:
            self.chord.key_down(time.time(), self.mic.is_pressed)

    def mic_pressed(self):
        with self.chord_lock:
            panic = self.chord.mic_down(time.time(), self.key.is_pressed)
        if panic:
            log.warning("panic chord: freezing")
            hook("freeze")

    def key_released(self):
        with self.chord_lock:
            approve = self.chord.key_up(time.time(), self.mic.is_pressed)
        if approve:
            self.approve_oldest()

    def approve_oldest(self):
        now = time.time()
        action, dest = approvals.on_key(STATE, now, self.last_approve)
        if action == "ignored":
            return
        self.last_approve = now          # covers the press right after a confirmation too
        if action == "confirmed-auto":
            log.info("auto-approve turned on by key")
            self.flash("key", 2)
        else:
            log.info("approved %s (key)", dest.name)
            hook("approve", str(dest))

    # --- talk -------------------------------------------------------------
    def talk_start(self):
        self.talking, self.talk_down = True, time.time()
        self.talkq.put("talk-start")

    def talk_released(self):
        if not self.talking:
            return
        self.talking = False
        now = time.time()
        if now - self.talk_down >= TALK_MIN_S:
            self.talkq.put("talk-stop")
            return
        self.talkq.put("talk-cancel")
        if now - self.talk_tap_at <= DOUBLE_S + TALK_MIN_S:
            self.talk_tap_at = 0.0
            self.talkq.put("repeat")
        else:
            self.talk_tap_at = now

    # --- lights -----------------------------------------------------------
    def clip_audio(self):
        """The clip's own mic is recording (only used when the Pi mic isn't fitted)."""
        try:
            clip = json.loads((DECK / "clip-status.json").read_text())
            cap = json.loads((DECK / "capture.json").read_text())
            return (time.time() - clip["seen"] < 10 and str(clip.get("rec")) == "1"
                    and bool(cap.get("mic")) and not (STATE / "pi-mic").exists())
        except Exception:
            return False

    def flash(self, led, times, on_s=0.12):
        def go():
            self.flashing[led] = True
            was = self.led[led].value
            for _ in range(times):
                self.led[led].value = not was; time.sleep(on_s)   # a blink shows even on a solid LED
                self.led[led].value = was; time.sleep(0.12)
            self.flashing[led] = False
        threading.Thread(target=go, daemon=True).start()

    def tick(self, now):
        with self.chord_lock:
            panic = self.chord.poll(now, self.key.is_pressed, self.mic.is_pressed)
        if panic:
            log.warning("panic chord: freezing")
            hook("freeze")            # its own process, never behind the talk queue
        dest = approvals.on_tick_auto(STATE, now, self.last_approve)
        if dest:
            self.last_approve = now
            log.info("approved %s (auto)", dest.name)
            hook("approve", str(dest))
        items = approvals.pending(STATE, now)
        waiting = bool(items)          # anything left needs the key, in either mode

        blink = int(now * 2) % 2 == 0
        if not self.flashing["key"]:
            self.led["key"].value = waiting and blink
        if not self.flashing["rec"]:
            video = (STATE / "rec-video").exists()
            audio = (STATE / "rec-audio").exists() or self.clip_audio()
            self.led["rec"].value = blink if video else audio

        mt = mtime(PHOTOS)
        if mt and mt != self.last_photo:      # a new photo landed in the outbox
            self.last_photo = mt
            self.flash("rec", 1, on_s=0.3)

        mt = mtime(STATE / "tx-sent")
        if mt and mt != self.last_sent:
            self.last_sent = mt
            self.flash("key", 3)



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
