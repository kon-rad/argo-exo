"""Ask the Hermes gateway's api_server (loopback only) and return the reply text."""
from __future__ import annotations

from typing import Callable

import requests


class TalkError(RuntimeError):
    pass


class Talker:
    def __init__(self, url: str, key: str, post: Callable = requests.post):
        self.url, self.key, self.post = url, key, post

    def ask(self, text: str, session: str) -> str:
        headers = {"Authorization": f"Bearer {self.key}"}
        if session:
            headers["X-Hermes-Session-Id"] = session
        try:
            r = self.post(f"{self.url}/v1/chat/completions", headers=headers,
                          json={"messages": [{"role": "user", "content": text}]}, timeout=120)
        except requests.RequestException as exc:
            raise TalkError(f"api_server unreachable: {exc}") from exc
        if r.status_code != 200:
            raise TalkError(f"api_server HTTP {r.status_code}")
        try:
            return r.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise TalkError(f"api_server bad response: {exc!r}") from exc
