"""Adds NOWNodes' api-key header for tools that can only take a bare RPC URL (CRE simulate, forge, cast).
Listens on loopback only:  http://127.0.0.1:8545/eth  /base  /arbitrum  /polygon"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from exo_nownodes import hosts
from exo_nownodes.rpc import Rpc

ALIASES = {"eth": "ethereum", "mainnet": "ethereum", "ethereum": "ethereum", "base": "base",
           "arbitrum": "arbitrum", "arb": "arbitrum", "polygon": "polygon", "matic": "polygon"}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
MAX_BODY = 1_000_000
UPSTREAM_TIMEOUT = 30


def _err(message: str) -> bytes:
    return json.dumps({"error": {"code": -32000, "message": message}}).encode()


def nownodes_forward(chain: str, body: bytes) -> tuple[int, bytes]:
    key = os.environ.get("NOWNODES_API_KEY", "")
    if not key:
        return 503, _err("NOWNODES_API_KEY is not set")
    redact = Rpc(chain, api_key=key)._redact
    req = urllib.request.Request(f"https://{hosts.RPC[chain]}", data=body, method="POST",
                                 headers={"api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as r:
            status, out = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, out = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 502, _err("upstream unreachable")
    return status, redact(out.decode("utf-8", "replace")).encode()


def make_server(host: str, port: int, forward) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ValueError("nownodes-proxy binds loopback only")

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, out: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_POST(self):
            chain = ALIASES.get(self.path.strip("/").split("/")[0].lower())
            if not chain:
                return self._reply(404, _err("unknown chain"))
            try:
                n = int(self.headers.get("Content-Length", 0))
            except ValueError:
                return self._reply(400, _err("bad Content-Length"))
            if n < 0 or n > MAX_BODY:
                left = min(max(n, 0), 8 * MAX_BODY)   # drain (bounded) so the client sees the 413, not a reset
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                return self._reply(413, _err("body too large"))
            body = self.rfile.read(n)
            try:
                status, out = forward(chain, body)
            except Exception:
                return self._reply(502, _err("proxy error"))
            self._reply(status, out)

        def log_message(self, fmt, *args):   # no request logging: bodies can hold signed txs
            pass

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8545
    make_server("127.0.0.1", port, nownodes_forward).serve_forever()
