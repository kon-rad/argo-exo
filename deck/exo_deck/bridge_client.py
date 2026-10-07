from __future__ import annotations

import re
from typing import Callable

import requests


class BridgeError(RuntimeError):
    pass


PROPOSAL_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")   # ledger ids are UUIDs; anything else must not reach a URL path


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

    # --- Transaction Guardian (00-architecture §4.1) ---------------------------------------------------------
    def pending_approvals(self) -> list:
        items = self._call(self.get, "/approvals/pending", "items", timeout=(5, 15))
        if not isinstance(items, list):
            raise BridgeError("bad response body: items is not a list")
        return items

    def report_executed(self, pid: str, tx_hash: str | None = None, error: str | None = None) -> bool:
        """True once recorded; False on 409 (the proposal is no longer waiting for the key, e.g. already
        recorded). Any other failure is a BridgeError, so the caller can retry later."""
        if not isinstance(pid, str) or not PROPOSAL_ID.match(pid):
            raise ValueError("bad proposal id")
        if (tx_hash is None) == (error is None):
            raise ValueError("send exactly one of tx_hash or error")
        body = {"tx_hash": tx_hash} if tx_hash is not None else {"error": error}
        try:
            r = self.post(f"{self.url}/approvals/{pid}/executed", headers=self.headers, json=body, timeout=(5, 15))
        except requests.RequestException as exc:
            raise BridgeError(str(exc)) from exc
        if r.status_code == 409:
            return False
        return bool(self._check(r, "ok"))

    def freeze(self, reason: str) -> dict:
        """The CRE freeze record. Slow (a workflow simulation), so a long read timeout; connect fails fast."""
        ok = self._call(self.post, "/freeze", "ok", json={"reason": reason}, timeout=(5, 300))
        return {"ok": ok is True}
