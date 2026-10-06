"""Replies into the earbuds: Deepgram Aura (same key as STT), espeak-ng when offline."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Mapping

import requests

SPEAK_URL = "https://api.deepgram.com/v1/speak"
MAX_CHARS = 1900  # Deepgram's per-request text limit is 2000


def speak(text: str, env: Mapping[str, str] = os.environ, post: Callable | None = requests.post,
          run: Callable = subprocess.run, tmpdir: Path | None = None) -> str:
    key = env.get("DEEPGRAM_API_KEY", "")
    if key and post is not None:
        try:
            r = post(SPEAK_URL,
                     params={"model": env.get("EXO_TTS_MODEL", "aura-2-thalia-en"),
                             "encoding": "linear16", "container": "wav", "sample_rate": "24000"},
                     headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
                     json={"text": text[:MAX_CHARS]}, timeout=20)
            if r.status_code == 200 and r.content:
                out = Path(tmpdir or tempfile.gettempdir()) / "exo-reply.wav"
                out.write_bytes(r.content)
                run([env.get("EXO_PLAYER", "aplay"), "-q", str(out)], check=False)
                return "deepgram"
        except requests.RequestException:
            pass
    run(["espeak-ng", text[:MAX_CHARS]], check=False)
    return "espeak"
