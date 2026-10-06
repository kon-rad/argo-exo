from __future__ import annotations

from typing import Callable

import requests


class BridgeError(RuntimeError):
    pass


class Bridge:
    """Client for exo-bridge. Every failure mode (down, timeout, non-2xx, bad body) is a BridgeError."""

    def __init__(self, url: str, token: str, post: Callable = requests.post, get: Callable = requests.get):
        self.url, self.post, self.get = url.rstrip("/"), post, get
        self.headers = {"Authorization": f"Bearer {token}"}

    def _check(self, r, key: str):
        if r.status_code not in (200, 201):
            try:
                msg = r.json().get("error", "")
            except Exception:
                msg = ""
            raise BridgeError(f"HTTP {r.status_code} {msg}".strip())
        try:
            return r.json()[key]
        except (KeyError, TypeError, ValueError) as exc:
            raise BridgeError(f"bad response body: {exc!r}") from exc

    def _call(self, fn: Callable, path: str, key: str, **kw):
        try:
            r = fn(f"{self.url}{path}", headers=self.headers, **kw)
        except requests.RequestException as exc:
            raise BridgeError(str(exc)) from exc
        return self._check(r, key)

    def talk(self, text: str, session: str) -> str:
        return self._call(self.post, "/talk", "reply", json={"text": text, "session": session}, timeout=125)

    def delegate(self, text: str, agent: str) -> dict:
        return self._call(self.post, "/tasks", "task", json={"text": text, "agent": agent}, timeout=40)

    def board(self) -> list:
        return self._call(self.get, "/board", "tasks", timeout=10)
