from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Mapping

DEFAULT_MANIFEST = "/srv/exo-guard/argo-exo/chain/cre/workflow-manifest.json"   # exoguard's checkout
# The wearer's agent names, then the Claude Code lanes run by agents/cc-dispatcher (agents/cc-lanes/lanes.toml).
# The first three are the old Hermes profile names; BRIDGE_AGENT_MAP turns them into engine roster bots, because
# nobody claims a card assigned to a Hermes profile name on the engine board. The money agents (trader, portfolio,
# wallet) are not on the roster and are refused: only the wearer's approve key path starts money actions.
DEFAULT_AGENTS = "librarian,builder,researcher,cc-coder,cc-research,cc-writer,cc-reviewer"
DEFAULT_AGENT_MAP = "researcher=vega,librarian=vega,builder=rune"
MONEY_AGENTS = ("trader", "portfolio", "wallet")


def _agent_map(raw: str) -> dict:
    """"researcher=vega,builder=rune" -> {"researcher": "vega", "builder": "rune"}."""
    out = {}
    for pair in raw.split(","):
        if not pair.strip():
            continue
        name, sep, bot = (x.strip() for x in pair.partition("="))
        if not sep or not name or not bot:
            raise ValueError(f"BRIDGE_AGENT_MAP entries look like name=bot, got {pair!r}")
        if name in MONEY_AGENTS or bot in MONEY_AGENTS:
            raise ValueError("BRIDGE_AGENT_MAP must not map the money agents")
        out[name] = bot
    return out


@dataclass(frozen=True)
class Config:
    token: str
    host: str
    port: int
    board: str
    agents: tuple[str, ...]
    agent_map: dict
    cli_board_bin: str
    notify_cmd: str
    api_server_url: str
    api_server_key: str
    guard_token: str = ""   # optional narrow token: accepted ONLY for POST /guard
    ledger_dsn: str = ""    # EXO_LEDGER_DSN: the read-only exo_reader role; empty = /ledger/* answer 503
    cre_manifest: str = DEFAULT_MANIFEST

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
            agent_map=_agent_map(env.get("BRIDGE_AGENT_MAP", DEFAULT_AGENT_MAP)),
            cli_board_bin=env.get("BRIDGE_CLI_BOARD", "cli-board"),
            notify_cmd=env.get("EXO_NOTIFY_CMD", "").strip(),
            api_server_url=env.get("API_SERVER_URL", "http://127.0.0.1:8642").rstrip("/"),
            api_server_key=key, guard_token=guard_token,
            ledger_dsn=env.get("EXO_LEDGER_DSN", ""), cre_manifest=env.get("EXO_CRE_MANIFEST", DEFAULT_MANIFEST),
        )
