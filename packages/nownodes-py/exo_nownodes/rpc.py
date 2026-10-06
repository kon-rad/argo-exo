from __future__ import annotations

import itertools
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

from . import hosts
from .usage import Usage, safe_add

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
        self.chain = chain
        self.url = "https://" + hosts.RPC[chain]
        self.key = api_key if api_key is not None else os.environ.get("NOWNODES_API_KEY", "")
        self.post, self.sleep, self.counter = post, sleep, counter

    def _redact(self, text: str) -> str:
        return text.replace(self.key, "***") if self.key else text

    def _err_text(self, err) -> str:
        msg = err.get("message", err) if isinstance(err, dict) else err
        return self._redact(str(msg))

    def _send(self, body):
        if not self.key:
            raise RpcError("NOWNODES_API_KEY is not set")
        for attempt in range(len(BACKOFF) + 1):
            try:
                r = self.post(self.url, json=body, headers={"api-key": self.key, "Content-Type": "application/json"}, timeout=30)
            except requests.RequestException as exc:
                err = f"network: {self._redact(str(exc))}"
            else:
                if r.status_code == 200:
                    try:
                        data = r.json()
                    except ValueError:
                        err = f"invalid JSON from {self.chain}"
                    else:
                        safe_add(self.counter, len(body) if isinstance(body, list) else 1)
                        return data
                else:
                    err = f"HTTP {r.status_code}"
                    if r.status_code not in RETRY_STATUS:
                        break
            if attempt < len(BACKOFF):
                self.sleep(BACKOFF[attempt])
        raise RpcError(err)

    def call(self, method: str, params: list) -> Any:
        reply = self._send({"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params})
        if not isinstance(reply, dict):
            raise RpcError(f"{method}: malformed reply")
        if "error" in reply:
            raise RpcError(f"{method}: {self._err_text(reply['error'])}")
        return reply.get("result")

    def batch(self, calls: list[tuple[str, list]]) -> list[Any]:
        body = [{"jsonrpc": "2.0", "id": next(_ids), "method": m, "params": p} for m, p in calls]
        reply = self._send(body)
        if isinstance(reply, dict) and "error" in reply:
            raise RpcError(f"batch: {self._err_text(reply['error'])}")
        if not isinstance(reply, list):
            raise RpcError("batch: malformed reply")
        by_id = {r.get("id"): r for r in reply if isinstance(r, dict)}
        out = []
        for c in body:
            r = by_id.get(c["id"], {"error": "missing reply"})
            if "error" in r:
                raise RpcError(f"{c['method']}: {self._err_text(r['error'])}")
            out.append(r.get("result"))
        return out
