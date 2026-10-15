"""USD prices from DefiLlama (secondhand aggregator; fine for NAV display, never used to sign anything)."""
from __future__ import annotations

from .chain import NATIVE, http_json


def llama_key(address: str) -> str:
    return "coingecko:ethereum" if address.lower() == NATIVE.lower() else f"base:{address}"


def usd(addresses: list[str], get=http_json) -> dict[str, float | None]:
    """address (lowercase) -> USD price, or None when DefiLlama has no confident price."""
    keys = {a.lower(): llama_key(a) for a in addresses}
    out: dict[str, float | None] = {a: None for a in keys}
    if not keys:
        return out
    try:
        coins = {k.lower(): v for k, v in get("https://coins.llama.fi/prices/current/" + ",".join(sorted(set(keys.values())))).get("coins", {}).items()}
    except Exception:  # noqa: BLE001  prices are display-only: a dead API shows "?" rather than failing a trade
        return out
    for a, k in keys.items():
        c = coins.get(k.lower())
        if c and c.get("confidence", 1) >= 0.8:
            out[a] = float(c["price"])
    return out
