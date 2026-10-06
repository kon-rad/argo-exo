from pathlib import Path
from exo_deck import tts


class Resp:
    def __init__(self, status, content=b"RIFFwav"):
        self.status_code, self.content = status, content


def test_deepgram_audio_is_played(tmp_path):
    played = []

    def post(url, **kw):
        assert url == "https://api.deepgram.com/v1/speak"
        assert kw["params"]["container"] == "wav" and kw["params"]["encoding"] == "linear16"
        assert kw["json"] == {"text": "hello"}
        return Resp(200)

    def run(cmd, **kw):
        played.append(cmd)

    assert tts.speak("hello", {"DEEPGRAM_API_KEY": "k"}, post, run, tmp_path) == "deepgram"
    assert played[0][0] == "aplay" and Path(played[0][-1]).read_bytes() == b"RIFFwav"


def test_long_text_is_truncated_under_the_api_limit(tmp_path):
    sent = {}

    def post(url, **kw):
        sent.update(kw["json"])
        return Resp(200)

    tts.speak("x" * 5000, {"DEEPGRAM_API_KEY": "k"}, post, lambda *a, **k: None, tmp_path)
    assert len(sent["text"]) <= 1900


def test_falls_back_to_espeak(tmp_path):
    cmds = []
    assert tts.speak("hi", {"DEEPGRAM_API_KEY": "k"}, lambda u, **k: Resp(500),
                     lambda cmd, **k: cmds.append(cmd), tmp_path) == "espeak"
    assert cmds == [["espeak-ng", "hi"]]


def test_no_key_uses_espeak(tmp_path):
    cmds = []
    assert tts.speak("hi", {}, None, lambda cmd, **k: cmds.append(cmd), tmp_path) == "espeak"


def test_nothing_available_returns_none_without_raising(tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    assert tts.speak("hi", {"DEEPGRAM_API_KEY": "k"}, lambda u, **k: Resp(200), run, tmp_path) == "none"
    assert tts.speak("hi", {}, None, run, tmp_path) == "none"


def test_player_failure_falls_back_to_espeak(tmp_path):
    cmds = []

    def run(cmd, **kw):
        cmds.append(cmd[0])
        if cmd[0] == "aplay":
            raise FileNotFoundError("aplay")

    assert tts.speak("hi", {"DEEPGRAM_API_KEY": "k"}, lambda u, **k: Resp(200), run, tmp_path) == "espeak"
    assert cmds == ["aplay", "espeak-ng"]


def test_timeout_is_set_and_expiry_is_a_failure(tmp_path):
    import subprocess
    seen = []

    def run(cmd, **kw):
        seen.append(kw.get("timeout"))
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    assert tts.speak("hi", {"DEEPGRAM_API_KEY": "k"}, lambda u, **k: Resp(200), run, tmp_path) == "none"
    assert seen == [60, 60]


def test_unexpected_post_error_falls_back(tmp_path):
    def post(u, **k):
        raise ValueError("bad")

    assert tts.speak("hi", {"DEEPGRAM_API_KEY": "k"}, post, lambda *a, **k: None, tmp_path) == "espeak"
