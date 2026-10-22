"""python -m agent_hub  — serve on 127.0.0.1 only. Env: AGENT_HUB_TOKEN (required), AGENT_HUB_PORT (8790)."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cc_dispatcher.config import load as load_lanes

from .chats import ChatManager, ChatSettings
from .hub import Hub

HERE = Path(__file__).resolve()
DEFAULT_LANES = HERE.parents[2] / "cc-lanes" / "lanes.toml"


def build(env: dict) -> Hub:
    token = env.get("AGENT_HUB_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("AGENT_HUB_TOKEN must be at least 32 characters (openssl rand -hex 32)")
    if env.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is set; unset it (chats must use the subscription login)")
    lanes = load_lanes(env.get("CC_LANES", str(DEFAULT_LANES)))
    deny = next(iter(lanes.lanes.values())).denied_tools   # the defaults every lane shares
    home = Path(env.get("HOME", "/home/hermes"))
    chats = ChatManager(ChatSettings(
        root=Path(env.get("AGENT_HUB_CHATS", home / "cc-chats")), claude_bin=lanes.claude_bin,
        cwd=env.get("AGENT_HUB_CWD", str(home / "secondBrain")), model=env.get("AGENT_HUB_MODEL", "sonnet"),
        max_live=int(env.get("AGENT_HUB_MAX_LIVE", "2")), denied_tools=deny, base_env=dict(env)))
    return Hub(token=token, lanes=lanes, chats=chats, hermes_bin=lanes.hermes_bin,
               telegram_script=env.get("TELEGRAM_AGENT_SCRIPT",
                                       str(home / "secondBrain/.hermes/scripts/telegram-agent.sh")))


def handler_for(hub: Hub):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agent-hub"

        def _go(self, method: str) -> None:
            url = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            body = None
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > 64 * 1024:
                    return self._send(413, {"error": "body too large"})
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._send(400, {"error": "body is not JSON"})
                if not isinstance(body, dict):
                    return self._send(400, {"error": "body must be an object"})
            status, payload = hub.handle(method, url.path, query, body, self.headers.get("Authorization", ""))
            self._send(status, payload)

        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            self._go("GET")

        def do_POST(self):  # noqa: N802
            self._go("POST")

        def log_message(self, fmt, *args):
            logging.info("%s %s", self.address_string(), fmt % args)

    return Handler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    hub = build(dict(os.environ))
    port = int(os.environ.get("AGENT_HUB_PORT", "8790"))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(hub))   # loopback only, never a public address

    def reaper():
        while True:
            time.sleep(60)
            hub.chats.reap_idle()
    threading.Thread(target=reaper, daemon=True).start()
    logging.info("agent-hub on 127.0.0.1:%d, chats in %s", port, hub.chats.s.root)
    try:
        server.serve_forever()
    finally:
        for c in hub.chats.chats.values():
            c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
