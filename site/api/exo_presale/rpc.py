"""The Base RPC the sale API and the admin CLI read from.

Normally NOWNodes (`exo_nownodes.Rpc("base")`, key from NOWNODES_API_KEY). For a local fork rehearsal only,
EXO_BASE_RPC_URL may point at an anvil fork on this machine: it must be exactly `http://127.0.0.1:<port>[/path]`.
Anything else is refused at startup, and the NOWNodes api-key header is never sent to the override.
"""
from __future__ import annotations

import itertools
import re
from typing import Any, Callable

import requests
from exo_nownodes.rpc import Rpc, RpcError, default_usage

LOCAL_RE = re.compile(r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})(/[A-Za-z0-9._~/-]*)?$")
_ids = itertools.count(1)


class LocalRpc:
    """Minimal JSON-RPC client for a loopback anvil fork. No key, no retries."""

    def __init__(self, url: str, post: Callable = requests.post, timeout: float = 30):
        m = LOCAL_RE.match(url or "")
        if not m or not 1 <= int(m.group(1)) <= 65535:
            raise SystemExit("EXO_BASE_RPC_URL must be http://127.0.0.1:<port> (a local fork); refusing " + repr(url))
        self.chain, self.url, self.post, self.timeout = "base", url, post, timeout

    def _send(self, body):
        try:
            r = self.post(self.url, json=body, headers={"Content-Type": "application/json"}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise RpcError(f"network: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise RpcError(f"HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise RpcError("invalid JSON from local fork") from exc

    def call(self, method: str, params: list) -> Any:
        reply = self._send({"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params})
        if not isinstance(reply, dict):
            raise RpcError(f"{method}: malformed reply")
        if "error" in reply:
            e = reply["error"]
            raise RpcError(f"{method}: {e.get('message', e) if isinstance(e, dict) else e}")
        return reply.get("result")

    def batch(self, calls: list[tuple[str, list]]) -> list[Any]:
        body = [{"jsonrpc": "2.0", "id": next(_ids), "method": m, "params": p} for m, p in calls]
        reply = self._send(body)
        if not isinstance(reply, list):
            raise RpcError("batch: malformed reply")
        by_id = {r.get("id"): r for r in reply if isinstance(r, dict)}
        out = []
        for c in body:
            r = by_id.get(c["id"], {"error": "missing reply"})
            if "error" in r:
                e = r["error"]
                raise RpcError(f"{c['method']}: {e.get('message', e) if isinstance(e, dict) else e}")
            out.append(r.get("result"))
        return out


def base_rpc(env, timeout: float | None = None, usage: bool = False, _post: Callable = requests.post):
    """The Base RPC for this process. `timeout` (seconds) shortens each request and drops the retry backoff sleeps,
    for the sale-state path that holds a lock while it reads."""
    override = env.get("EXO_BASE_RPC_URL")
    if override is not None:
        return LocalRpc(override, post=_post, timeout=timeout or 30)
    post = _post if timeout is None else (lambda url, **kw: _post(url, **{**kw, "timeout": timeout}))
    rpc = Rpc("base", api_key=env.get("NOWNODES_API_KEY", ""), post=post,
              counter=default_usage() if usage else None)
    if timeout is not None:
        rpc.sleep = lambda s: None
    return rpc
