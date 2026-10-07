from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Mapping

DEFAULT_AGENTS = "librarian,trader,portfolio,wallet,builder,researcher"


@dataclass(frozen=True)
class Config:
    token: str
    host: str
    port: int
    board: str
    agents: tuple[str, ...]
    max_runtime: str
    telegram_chat_id: str
    hermes_bin: str
    api_server_url: str
    api_server_key: str
    guard_token: str = ""   # optional narrow token: accepted ONLY for POST /guard

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        token = env.get("EXO_BRIDGE_TOKEN", "")
        if len(token) < 32:
            raise ValueError("EXO_BRIDGE_TOKEN must be at least 32 characters (openssl rand -hex 32)")
        guard_token = env.get("EXO_GUARD_TOKEN", "")
        if guard_token and (len(guard_token) < 32 or guard_token == token):
            raise ValueError("EXO_GUARD_TOKEN must be at least 32 characters and differ from EXO_BRIDGE_TOKEN")
        host = env.get("EXO_BRIDGE_HOST", "").strip().strip("[]")
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            addr = None
        if addr is None or addr.is_unspecified:
            raise ValueError("EXO_BRIDGE_HOST must be the droplet's tailnet IP, never empty, a wildcard or a hostname")
        host = str(addr)
        key = env.get("API_SERVER_KEY", "")
        if not key:
            raise ValueError("API_SERVER_KEY is required (same value as the gateway's api_server key)")
        return cls(
            token=token, host=host, port=int(env.get("EXO_BRIDGE_PORT", "8765")),
            board=env.get("EXO_BOARD", "exo"),
            agents=tuple(a.strip() for a in env.get("EXO_AGENTS", DEFAULT_AGENTS).split(",") if a.strip()),
            max_runtime=env.get("EXO_TASK_MAX_RUNTIME", "30m"),
            telegram_chat_id=env.get("EXO_TELEGRAM_CHAT_ID", ""),
            hermes_bin=env.get("HERMES_BIN", "hermes"),
            api_server_url=env.get("API_SERVER_URL", "http://127.0.0.1:8642").rstrip("/"),
            api_server_key=key, guard_token=guard_token,
        )
