from __future__ import annotations

import itertools
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

from . import hosts
from .usage import Usage

RETRY_STATUS = {429, 500, 502, 503, 504}
BACKOFF = (0.5, 1.5)
_ids = itertools.count(1)


class RpcError(RuntimeError):
    pass


def default_usage() -> Usage:
    return Usage(Path(os.environ.get("EXO_NOWNODES_USAGE", Path.home() / ".cache" / "exo-nownodes" / "usage.json")))


class Rpc:
    def __init__(self, chain: str, api_key: str | None = None, post: Callable = requests.post,
                 sleep: Callable = time.sleep, counter: Usage | None = None):
        if chain not in hosts.RPC:
            raise ValueError(f"unknown chain {chain}")
        self.url = "https://" + hosts.RPC[chain]
        self.key = api_key if api_key is not None else os.environ.get("NOWNODES_API_KEY", "")
        self.post, self.sleep, self.counter = post, sleep, counter

    def _redact(self, text: str) -> str:
        return text.replace(self.key, "***") if self.key else text

    def _send(self, body):
        for attempt in range(len(BACKOFF) + 1):
            try:
                r = self.post(self.url, json=body, headers={"api-key": self.key, "Content-Type": "application/json"}, timeout=30)
            except requests.RequestException as exc:
                err = f"network: {self._redact(str(exc))}"
            else:
                if r.status_code == 200:
                    if self.counter:
                        self.counter.add(len(body) if isinstance(body, list) else 1)
                    return r.json()
                err = f"HTTP {r.status_code}"
                if r.status_code not in RETRY_STATUS:
                    break
            if attempt < len(BACKOFF):
                self.sleep(BACKOFF[attempt])
        raise RpcError(err)

    def call(self, method: str, params: list) -> Any:
        reply = self._send({"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params})
        if "error" in reply:
            raise RpcError(self._redact(f"{method}: {reply['error'].get('message', reply['error'])}"))
        return reply.get("result")

    def batch(self, calls: list[tuple[str, list]]) -> list[Any]:
        body = [{"jsonrpc": "2.0", "id": next(_ids), "method": m, "params": p} for m, p in calls]
        by_id = {r.get("id"): r for r in self._send(body)}
        out = []
        for c in body:
            r = by_id.get(c["id"], {"error": {"message": "missing reply"}})
            if "error" in r:
                raise RpcError(self._redact(f"{c['method']}: {r['error'].get('message')}"))
            out.append(r.get("result"))
        return out
