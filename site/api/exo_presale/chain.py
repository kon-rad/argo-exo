"""Live sale state read from ExoPreorder on Base through NOWNodes, cached for a few seconds."""
from __future__ import annotations

import threading
import time
from typing import Callable

from eth_utils import keccak
from exo_nownodes.rpc import RpcError

SEL = {name: "0x" + keccak(text=sig)[:4].hex() for name, sig in {
    "paused": "paused()", "totalMinted": "totalMinted()", "maxSupply": "maxSupply()",
    "price": "price(uint8)", "ownerOf": "ownerOf(uint256)"}.items()}


class SaleUnavailable(RuntimeError):
    """The chain could not be read (network, RPC error, malformed reply). Never shown to the buyer verbatim."""


def _word(n: int) -> str:
    return hex(n)[2:].rjust(64, "0")


def _fmt(units: int) -> str:
    return f"{units // 1_000_000}.{(units % 1_000_000) // 10_000:02d}"


def _int(x) -> int:
    if not isinstance(x, str) or not x.startswith("0x"):
        raise SaleUnavailable("malformed eth_call result")
    return int(x, 16) if len(x) > 2 else 0


def deployed(contract: str) -> bool:
    try:
        return int(contract, 16) != 0
    except (TypeError, ValueError):
        return False


class Sale:
    """`rpc` reads ownerOf fresh for claims. `state_rpc` (default: rpc) serves the cached sale state; give it a short
    timeout. A failed state read is remembered for `fail_ttl_s`, and a request that can't get the refresh lock within
    `lock_wait_s` gets SaleUnavailable, so a slow RPC answers 503 quickly instead of queueing every request."""

    def __init__(self, rpc, contract: str, tiers: list[int], ttl_s: int = 30, clock: Callable = time.time,
                 state_rpc=None, fail_ttl_s: float = 5, lock_wait_s: float = 2):
        self.rpc, self.contract, self.tiers, self.ttl, self.clock = rpc, contract, list(tiers), ttl_s, clock
        self.state_rpc = state_rpc if state_rpc is not None else rpc
        self.fail_ttl, self.lock_wait = fail_ttl_s, lock_wait_s
        self._cache, self._at, self._failed_at = None, 0.0, None
        self._lock = threading.Lock()

    def _call(self, data: str) -> tuple[str, list]:
        return ("eth_call", [{"to": self.contract, "data": data}, "latest"])

    def _closed(self) -> dict:
        return {"deployed": False, "open": False, "paused": False, "minted": 0, "max_supply": 0,
                "tiers": [{"tier": t, "price_units": 0, "price": None} for t in self.tiers]}

    def state(self) -> dict:
        if not deployed(self.contract):          # zero address in preorder.json: "opening soon", not an error
            return self._closed()
        if not self._lock.acquire(timeout=self.lock_wait):
            raise SaleUnavailable("sale state refresh busy")
        try:
            now = self.clock()
            if self._cache is not None and now - self._at <= self.ttl:
                return self._cache
            if self._failed_at is not None and now - self._failed_at < self.fail_ttl:
                raise SaleUnavailable("sale state read failed recently")
            try:
                self._cache, self._at, self._failed_at = self._read(), now, None
            except SaleUnavailable:
                self._failed_at = now
                raise
            return self._cache
        finally:
            self._lock.release()

    def _read(self) -> dict:
        calls = [self._call(SEL["paused"]), self._call(SEL["totalMinted"]), self._call(SEL["maxSupply"])]
        calls += [self._call(SEL["price"] + _word(t)) for t in self.tiers]
        try:
            raw = self.state_rpc.batch(calls)
        except RpcError as exc:
            raise SaleUnavailable("sale state read failed") from exc
        if not isinstance(raw, list) or len(raw) != len(calls):
            raise SaleUnavailable("malformed batch reply")
        out = [_int(x) for x in raw]
        paused, minted, max_supply, prices = bool(out[0]), out[1], out[2], out[3:]
        tiers = [{"tier": t, "price_units": p, "price": _fmt(p) if p else None} for t, p in zip(self.tiers, prices)]
        return {"deployed": True, "open": (not paused) and minted < max_supply and any(prices), "paused": paused,
                "minted": minted, "max_supply": max_supply, "tiers": tiers}

    def owner_of(self, n: int) -> str | None:
        """Current holder of receipt n, read fresh (never cached). None when the token doesn't exist or was burned."""
        if not deployed(self.contract):
            return None
        try:
            out = self.rpc.call(*self._call(SEL["ownerOf"] + _word(n)))
        except RpcError as exc:
            if "revert" in str(exc).lower():     # ERC721NonexistentToken: never minted, or refunded and burned
                return None
            raise SaleUnavailable("ownerOf read failed") from exc
        value = _int(out)
        return "0x" + hex(value)[2:].rjust(40, "0") if value else None
