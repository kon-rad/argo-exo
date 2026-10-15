"""Known Base tokens by symbol. Any other token can be named by its 0x address (decimals/symbol read onchain)."""
from __future__ import annotations

import re

from .chain import NATIVE

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

BASE = {
    "ETH": NATIVE,
    "WETH": "0x4200000000000000000000000000000000000006",
    "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "cbBTC": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
    "cbETH": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22",
    "wstETH": "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452",
    "AERO": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
}
_BY_UPPER = {k.upper(): v for k, v in BASE.items()}


def resolve(name: str, extra: dict[str, str] | None = None) -> str:
    """Symbol (case-insensitive) or 0x address → address. Unknown symbols raise: an address is unambiguous."""
    name = name.strip()
    if ADDRESS.match(name):
        return name
    table = {**_BY_UPPER, **{k.upper(): v for k, v in (extra or {}).items()}}
    if name.upper() in table:
        return table[name.upper()]
    raise ValueError(f"unknown token {name!r}: use a known symbol ({', '.join(BASE)}) or its 0x address")


def symbol_for(address: str, extra: dict[str, str] | None = None) -> str | None:
    for k, v in {**BASE, **(extra or {})}.items():
        if v.lower() == address.lower():
            return k
    return None
