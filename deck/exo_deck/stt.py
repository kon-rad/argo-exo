"""Speech to text in the cloud. Deepgram first, Gemini if Deepgram is down or has no key.

No on-device model: the Pi 4 is too slow, and both keys are cheap to run on short clips.
"""
from __future__ import annotations

import base64
import os
from typing import Callable, Mapping

import requests

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_PROMPT = ("Transcribe this audio verbatim. Return only the spoken words, "
                 "no labels, no quotes, no commentary. Return nothing if there is no speech.")


class STTError(RuntimeError):
    pass


def deepgram(wav: bytes, key: str, post: Callable, model: str) -> str:
    r = post(DEEPGRAM_URL, params={"model": model, "smart_format": "true"},
             headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"},
             data=wav, timeout=20)
    if r.status_code != 200:
        raise STTError(f"HTTP {r.status_code}")
    try:
        return r.json()["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise STTError(f"unexpected response: {exc!r}") from exc


def gemini(wav: bytes, key: str, post: Callable, model: str) -> str:
    body = {"contents": [{"parts": [
        {"text": GEMINI_PROMPT},
        {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav).decode()}},
    ]}]}
    r = post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": key},
             json=body, timeout=30)
    if r.status_code != 200:
        raise STTError(f"HTTP {r.status_code}")
    try:
        candidate = r.json()["candidates"][0]
        # Silence comes back as a candidate with no parts (finishReason STOP): empty transcript.
        parts = (candidate.get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise STTError(f"unexpected response: {exc!r}") from exc


PROVIDERS = {
    "deepgram": (deepgram, "DEEPGRAM_API_KEY", "EXO_DEEPGRAM_MODEL", "nova-3"),
    "gemini": (gemini, "GEMINI_API_KEY", "EXO_GEMINI_MODEL", "gemini-2.5-flash"),
}


def transcribe(wav: bytes, env: Mapping[str, str] = os.environ,
               post: Callable = requests.post) -> tuple[str, str]:
    errors = []
    for name in env.get("EXO_STT_ORDER", "deepgram,gemini").split(","):
        name = name.strip()
        if name not in PROVIDERS:
            errors.append(f"{name}: unknown provider")
            continue
        fn, key_var, model_var, default_model = PROVIDERS[name]
        key = env.get(key_var, "")
        if not key:
            errors.append(f"{name}: {key_var} not set")
            continue
        try:
            return fn(wav, key, post, env.get(model_var, default_model)), name
        except (STTError, requests.RequestException) as exc:
            errors.append(f"{name}: {exc}")
    raise STTError("; ".join(errors))
