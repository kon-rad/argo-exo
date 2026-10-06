from datetime import date
from pathlib import Path
from exo_deck.router import Route, route
from exo_deck.stt import STTError
from exo_deck.bridge_client import BridgeError
from exo_deck.config import Settings
from exo_deck import voice


def settings(tmp_path):
    s = Settings.from_env({"DECK_ROOT": str(tmp_path), "EXO_BRIDGE_URL": "http://b", "EXO_BRIDGE_TOKEN": "t"})
    s.state.mkdir(parents=True, exist_ok=True)
    (s.state / "rec.wav").write_bytes(b"x" * 20000)
    return s


class FakeBridge:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def talk(self, text, session):
        if self.fail:
            raise BridgeError("down")
        self.calls.append(("talk", text, session))
        return "You hold 0.4 ETH."

    def delegate(self, text, agent):
        if self.fail:
            raise BridgeError("down")
        self.calls.append(("delegate", text, agent))
        return {"id": "t_1"}


def deps(said="What's my portfolio?", bridge=None, stt_fail=False):
    spoken = []

    def transcribe(wav):
        if stt_fail:
            raise STTError("deepgram: HTTP 500; gemini: HTTP 500")
        return said, "deepgram"

    d = voice.Deps(transcribe=transcribe, route=route, bridge=bridge or FakeBridge(),
                   speak=spoken.append, today=lambda: date(2026, 10, 7))
    return d, spoken


def stop(s, d):
    voice.recorder_stop = lambda state, min_bytes: state / "rec.wav"
    return voice.handle_stop(s, d)


def test_talk_reply_is_spoken_and_saved(tmp_path):
    s = settings(tmp_path)
    d, spoken = deps()
    assert stop(s, d) == "You hold 0.4 ETH."
    assert d.bridge.calls == [("talk", "What's my portfolio?", "exo-deck-2026-10-07")]
    assert spoken == ["You hold 0.4 ETH."]
    assert (s.state / "last-heard.txt").read_text() == "What's my portfolio?"
    assert (s.state / "last-reply.txt").read_text() == "You hold 0.4 ETH."


def test_delegate_confirms_by_agent(tmp_path):
    s = settings(tmp_path)
    d, spoken = deps("Have the researcher find gas APIs")
    stop(s, d)
    assert d.bridge.calls == [("delegate", "find gas APIs", "researcher")]
    assert "researcher" in spoken[0] and "Telegram" in spoken[0]


def test_empty_transcript_says_didnt_catch(tmp_path):
    s = settings(tmp_path)
    d, spoken = deps("")
    stop(s, d)
    assert d.bridge.calls == [] and spoken == ["Sorry, I didn't catch that."]


def test_stt_down_speaks_error(tmp_path):
    s = settings(tmp_path)
    d, spoken = deps(stt_fail=True)
    stop(s, d)
    assert spoken == ["Speech recognition is down. Try again in a moment."]


def test_bridge_down_speaks_error(tmp_path):
    s = settings(tmp_path)
    d, spoken = deps(bridge=FakeBridge(fail=True))
    stop(s, d)
    assert spoken == ["I can't reach Hermes right now."]


def test_stop_short_recording_is_silent(tmp_path):
    s = settings(tmp_path)
    d, spoken = deps()
    voice.recorder_stop = lambda state, min_bytes: None
    assert voice.handle_stop(s, d) == "" and spoken == []


def test_repeat_speaks_last_reply(tmp_path):
    s = settings(tmp_path)
    (s.state / "last-reply.txt").write_text("again")
    spoken = []
    voice.handle_repeat(s, spoken.append)
    assert spoken == ["again"]


def test_unexpected_exception_is_spoken_not_silent(tmp_path):
    s = settings(tmp_path)
    d, spoken = deps()
    d.transcribe = lambda wav: (_ for _ in ()).throw(RuntimeError("boom"))
    (s.state / "listening").touch()
    stop(s, d)
    assert spoken == ["Something went wrong on the deck."] and not (s.state / "listening").exists()


def test_wav_deleted_after_read(tmp_path):
    s = settings(tmp_path)
    d, _ = deps()
    stop(s, d)
    assert not (s.state / "rec.wav").exists()


def test_turns_are_logged(tmp_path):
    from exo_deck import state as st
    s = settings(tmp_path)
    d, _ = deps()
    stop(s, d)
    assert [t["role"] for t in st.recent_turns(s.state)] == ["you", "hermes"]


def test_turn_log_failure_does_not_silence_speech(tmp_path, monkeypatch):
    from exo_deck import state as st

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(st, "append_turn", boom)
    s = settings(tmp_path)
    d, spoken = deps()
    assert stop(s, d) == "You hold 0.4 ETH." and spoken == ["You hold 0.4 ETH."]


def test_empty_transcript_logs_no_you_turn(tmp_path):
    from exo_deck import state as st
    s = settings(tmp_path)
    d, _ = deps(said="")
    stop(s, d)
    assert [t["role"] for t in st.recent_turns(s.state)] == ["hermes"]
