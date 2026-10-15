"""Where the fund lives and who owns it.

FUND_DIR           the live state folder (constitution.md, trade-log.md, meetings/, state/). Required.
FUND_AGENT_KEY     path to the agent key file (hex, mode 600). Default ~/.argo-fund/agent.key
FUND_RPC_URL       Base JSON-RPC. Default https://mainnet.base.org
FUND_DECK_OWNER    the deck's address (second owner). Overrides state/config.json
FUND_PHONE_OWNER   Konrad's phone wallet (third owner). Overrides state/config.json; fill in before create-safe
FUND_SAVINGS       the savings vault (the Pi Zero's address). Withdrawals and profits go only here

state/config.json holds the non-secret rest: safe, owners, threshold, slippage. No keys ever go in it."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from eth_account import Account

from .tokens import ADDRESS

CHAIN_ID = 8453
DEFAULTS = {"chain_id": CHAIN_ID, "safe": "", "threshold": 2, "slippage_bps": 100,
            "owners": {"agent": "", "deck": "", "phone": ""}, "savings": "", "tokens": {}}


class ConfigError(RuntimeError):
    pass


def file_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines (FUND_DIR, FUND_RPC_URL, …) so fund-trade works under `sudo -u hermes`, which drops the
    caller's environment. Real environment variables win over the file."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for ln in lines:
        k, sep, v = ln.strip().partition("=")
        if sep and k.startswith("FUND_") and not k.startswith("#"):
            out[k.strip()] = v.strip().strip('"')
    return out


@dataclass
class Fund:
    dir: Path
    rpc_url: str
    key_path: Path
    cfg: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, env=os.environ) -> "Fund":
        env = {**file_env(Path(env.get("FUND_ENV_FILE", "~/.argo-fund/env")).expanduser()), **env}
        d = env.get("FUND_DIR", "")
        if not d:
            raise ConfigError("FUND_DIR is not set")
        f = cls(dir=Path(d).expanduser(), rpc_url=env.get("FUND_RPC_URL", "https://mainnet.base.org"),
                key_path=Path(env.get("FUND_AGENT_KEY", "~/.argo-fund/agent.key")).expanduser())
        f.cfg = {**DEFAULTS, **f._read_cfg()}
        f.cfg["owners"] = {**DEFAULTS["owners"], **f.cfg.get("owners", {})}
        for role, var in (("deck", "FUND_DECK_OWNER"), ("phone", "FUND_PHONE_OWNER")):
            if env.get(var):
                f.cfg["owners"][role] = env[var]
        if env.get("FUND_SAVINGS"):
            f.cfg["savings"] = env["FUND_SAVINGS"]
        return f

    @property
    def savings(self) -> str:
        s = self.cfg.get("savings", "")
        if not ADDRESS.match(s or ""):
            raise ConfigError("no savings address: set FUND_SAVINGS (the Pi Zero's address) in ~/.argo-fund/env")
        return s

    @property
    def state(self) -> Path:
        return self.dir / "state"

    @property
    def proposals(self) -> Path:
        return self.state / "proposals"

    def _read_cfg(self) -> dict:
        p = self.state / "config.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save_cfg(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        tmp = self.state / ".config.json.tmp"
        tmp.write_text(json.dumps(self.cfg, indent=2) + "\n")
        tmp.replace(self.state / "config.json")

    @property
    def safe(self) -> str:
        s = self.cfg.get("safe", "")
        if not ADDRESS.match(s or ""):
            raise ConfigError("no Safe yet: run `fund-trade create-safe` first")
        return s

    def owners(self) -> dict[str, str]:
        """agent, deck, phone. Every one must be a real address before a Safe can be created."""
        o = dict(self.cfg["owners"])
        o["agent"] = o.get("agent") or (self.agent_address() if self.key_path.exists() else "")
        missing = [r for r in ("agent", "deck", "phone") if not ADDRESS.match(o.get(r) or "")]
        if missing:
            raise ConfigError(f"owner address missing for: {', '.join(missing)} "
                              "(FUND_DECK_OWNER / FUND_PHONE_OWNER, or owners in state/config.json)")
        return o

    # --- the agent key: never printed, never in config.json -------------------------------------------
    def agent_key(self) -> str:
        st = self.key_path.stat()
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ConfigError(f"{self.key_path} must be mode 600")
        return self.key_path.read_text().strip()

    def agent_address(self) -> str:
        return Account.from_key(self.agent_key()).address

    def init_agent_key(self) -> str:
        if self.key_path.exists():
            return self.agent_address()
        self.key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        acct = Account.create()
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(acct.key.hex() + "\n")
        return acct.address
