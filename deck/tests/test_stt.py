import base64
import pytest
import requests
from exo_deck import stt


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


DG_OK = {"results": {"channels": [{"alternatives": [{"transcript": " what's my portfolio "}]}]}}
GM_OK = {"candidates": [{"content": {"parts": [{"text": "what's my portfolio\n"}]}}]}
ENV = {"DEEPGRAM_API_KEY": "dg", "GEMINI_API_KEY": "gm"}


def test_deepgram_first(monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        return Resp(200, DG_OK)

    assert stt.transcribe(b"RIFF", ENV, post) == ("what's my portfolio", "deepgram")
    url, kw = calls[0]
    assert url == "https://api.deepgram.com/v1/listen"
    assert kw["headers"]["Authorization"] == "Token dg"
    assert kw["params"]["model"] == "nova-3"
    assert kw["data"] == b"RIFF"
    assert kw["timeout"] <= 20


def test_falls_back_to_gemini_when_deepgram_fails():
    def post(url, **kw):
        if "deepgram" in url:
            return Resp(500, {})
        assert kw["headers"]["x-goog-api-key"] == "gm"
        part = kw["json"]["contents"][0]["parts"][1]["inline_data"]
        assert part["mime_type"] == "audio/wav" and base64.b64decode(part["data"]) == b"RIFF"
        return Resp(200, GM_OK)

    assert stt.transcribe(b"RIFF", ENV, post) == ("what's my portfolio", "gemini")


def test_network_error_falls_back():
    def post(url, **kw):
        if "deepgram" in url:
            raise requests.ConnectionError("down")
        return Resp(200, GM_OK)

    assert stt.transcribe(b"RIFF", ENV, post)[1] == "gemini"


def test_missing_key_skips_provider():
    assert stt.transcribe(b"RIFF", {"GEMINI_API_KEY": "gm"}, lambda u, **k: Resp(200, GM_OK))[1] == "gemini"


def test_all_fail_raises_with_reasons():
    with pytest.raises(stt.STTError) as e:
        stt.transcribe(b"RIFF", ENV, lambda u, **k: Resp(503, {}))
    assert "deepgram" in str(e.value) and "gemini" in str(e.value)


def test_order_and_models_are_configurable():
    env = dict(ENV, EXO_STT_ORDER="gemini,deepgram", EXO_GEMINI_MODEL="gemini-x")
    seen = []

    def post(url, **kw):
        seen.append(url)
        return Resp(200, GM_OK)

    stt.transcribe(b"RIFF", env, post)
    assert "models/gemini-x:generateContent" in seen[0]


def test_empty_transcript_is_a_result_not_a_failure():
    empty = {"results": {"channels": [{"alternatives": [{"transcript": ""}]}]}}
    assert stt.transcribe(b"RIFF", ENV, lambda u, **k: Resp(200, empty)) == ("", "deepgram")


def test_gemini_silence_without_parts_is_an_empty_transcript():
    silent = {"candidates": [{"finishReason": "STOP", "content": {"role": "model"}}]}
    assert stt.transcribe(b"RIFF", {"GEMINI_API_KEY": "gm"}, lambda u, **k: Resp(200, silent)) == ("", "gemini")
    assert stt.transcribe(b"RIFF", {"GEMINI_API_KEY": "gm"},
                          lambda u, **k: Resp(200, {"candidates": [{"finishReason": "STOP"}]})) == ("", "gemini")
