"""Push-to-talk entry point, called by deck-buttons hooks:
    python -m exo_deck.voice start | stop | cancel | repeat
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable

from . import approvals, recorder, router, state as st, stt, tts
from .bridge_client import Bridge, BridgeError
from .config import Settings

log = logging.getLogger("exo-voice")

DIDNT_CATCH = "Sorry, I didn't catch that."
STT_DOWN = "Speech recognition is down. Try again in a moment."
BRIDGE_DOWN = "I can't reach Hermes right now."
GENERIC_FAIL = "Something went wrong on the deck."

recorder_stop: Callable = recorder.stop   # swapped in tests


@dataclass
class Deps:
    transcribe: Callable
    route: Callable
    bridge: object
    speak: Callable
    today: Callable = date.today


def _log_turn(s: Settings, role: str, text: str) -> None:
    """Conversation log is a display nicety: a failure here must never silence the deck."""
    try:
        st.append_turn(s.state, role, text)
    except Exception:
        log.exception("could not log %s turn", role)


def _say(s: Settings, d: Deps, text: str) -> str:
    (s.state / "last-reply.txt").write_text(text)
    _log_turn(s, "hermes", text)
    d.speak(text)
    return text


def _handle_stop(s: Settings, d: Deps) -> str:
    wav = recorder_stop(s.state, s.min_wav_bytes)
    if wav is None:
        return ""
    try:
        audio = wav.read_bytes()
        wav.unlink(missing_ok=True)
        heard, provider = d.transcribe(audio)
    except stt.STTError as exc:
        log.warning("stt failed: %s", exc)
        return _say(s, d, STT_DOWN)
    (s.state / "last-heard.txt").write_text(heard)
    if heard.strip():
        _log_turn(s, "you", heard)
    r = d.route(heard)
    if r.kind == "empty":
        return _say(s, d, DIDNT_CATCH)
    if r.kind == "nav":
        if r.text in ("more", "back"):
            st.page(s.state, +1 if r.text == "more" else -1)
            return ""                              # silent: the screen is the answer
        return "" if st.set_panel(s.state, r.text) else _say(s, d, DIDNT_CATCH)
    if r.kind == "media-open":
        (s.state / "media-open").write_text(r.text)
        st.set_panel(s.state, "media")
        return ""
    if r.kind == "control":
        if r.text == "close":
            (s.state / "media-open").unlink(missing_ok=True)
            return ""
        if r.text == "auto-off":
            approvals.set_mode(s.state, "manual")
            return _say(s, d, "Auto-approve is off. Every transaction waits for the key.")
        approvals.request_auto(s.state, time.time())     # a voice can only ask; the key turns it on
        return _say(s, d, "Press the approve key within five seconds to turn on auto-approve.")
    try:
        if r.kind == "talk":
            return _say(s, d, d.bridge.talk(r.text, f"exo-deck-{d.today().isoformat()}"))
        d.bridge.delegate(r.text, r.agent)
        return _say(s, d, f"Sent to the {r.agent}. I'll ping you on Telegram when it's done.")
    except BridgeError as exc:
        log.warning("bridge failed: %s", exc)
        return _say(s, d, BRIDGE_DOWN)


def handle_stop(s: Settings, d: Deps) -> str:
    """Never silent: any unexpected failure is logged and spoken as a generic error."""
    try:
        return _handle_stop(s, d)
    except Exception:
        log.exception("voice stop failed")
        (s.state / "listening").unlink(missing_ok=True)
        try:
            return _say(s, d, GENERIC_FAIL)
        except Exception:
            log.exception("could not speak failure")
            return ""


def handle_repeat(s: Settings, speak: Callable) -> None:
    try:
        speak((s.state / "last-reply.txt").read_text())
    except OSError:
        speak("Nothing to repeat yet.")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s exo-voice %(message)s")
    s = Settings.from_env()
    cmd = argv[0] if argv else ""
    if cmd == "start":
        recorder.start(s.state, s.arecord_cmd)
    elif cmd == "cancel":
        recorder.cancel(s.state)
    elif cmd == "repeat":
        handle_repeat(s, tts.speak)
    elif cmd == "stop":
        d = Deps(transcribe=stt.transcribe, route=router.route,
                 bridge=Bridge(s.bridge_url, s.bridge_token), speak=tts.speak)
        handle_stop(s, d)
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
