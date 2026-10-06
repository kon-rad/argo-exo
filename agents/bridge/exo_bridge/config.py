from __future__ import annotations

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

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        token = env.get("EXO_BRIDGE_TOKEN", "")
        if len(token) < 32:
            raise ValueError("EXO_BRIDGE_TOKEN must be at least 32 characters (openssl rand -hex 32)")
        host = env.get("EXO_BRIDGE_HOST", "").strip()
        if host in ("", "0.0.0.0", "::"):
            raise ValueError("EXO_BRIDGE_HOST must be the droplet's tailnet IP, never empty or a wildcard")
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
            api_server_key=key,
        )
