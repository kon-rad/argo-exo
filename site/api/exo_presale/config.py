"""Runtime config from env and the site's content files. No secrets here: NOWNODES_API_KEY is read by exo_nownodes."""
import ipaddress
import json
import os
import re
from pathlib import Path

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ZERO = "0x" + "00" * 20


ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def read_env_file(path) -> dict:
    """KEY=VALUE lines of a systemd-style EnvironmentFile, read literally: no shell, no expansion of $ or backticks.
    Blank lines and # comments are skipped, an `export ` prefix is allowed, one pair of matching surrounding quotes
    is removed, and lines without a valid KEY= are ignored."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        if not ENV_KEY_RE.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load(env=os.environ) -> dict:
    content = Path(env.get("EXO_SITE_CONTENT", "/srv/exo-site/content"))
    tiers = [int(t["tier"]) for t in json.loads((content / "tiers.json").read_text())]
    # Unfilled slots ("TODO(konrad)") are not countries: no claim is accepted until Konrad lists real ones.
    countries = tuple(c for c in json.loads((content / "copy.json").read_text())["terms"]["countries"]
                      if isinstance(c, str) and c.strip() and "TODO(" not in c)
    contract = env.get("EXO_PREORDER")
    if not contract:
        pj = Path(env.get("EXO_PREORDER_JSON", "/srv/exo-site/static/preorder.json"))
        contract = json.loads(pj.read_text()).get("contract") if pj.exists() else ZERO
    if not ADDR_RE.match(contract or ""):
        raise SystemExit("EXO_PREORDER / preorder.json contract is not an address")
    host = env.get("EXO_PRESALE_HOST", "127.0.0.1")
    if not _loopback(host):
        raise SystemExit(f"refusing to bind {host}: the sale API listens on loopback only, behind Caddy")
    return {"contract": contract, "tiers": tiers, "countries": countries,
            "db": env.get("EXO_CLAIMS_DB", "/var/lib/exo-presale/claims.db"),
            "waitlist": env.get("EXO_WAITLIST_MD", "/var/lib/exo-presale/waitlist.md"),
            "rate": int(env.get("EXO_PRESALE_RATE", "10")),
            "host": host, "port": int(env.get("EXO_PRESALE_PORT", "5310"))}
