from __future__ import annotations

import os
from typing import Callable

import requests

from . import hosts
from .rpc import RpcError


class Blockbook:
    def __init__(self, chain: str, api_key: str | None = None, get: Callable = requests.get, counter=None):
        self.base = f"https://{hosts.BLOCKBOOK[chain]}/api/v2"
        self.key = api_key if api_key is not None else os.environ.get("NOWNODES_API_KEY", "")
        self.get, self.counter = get, counter

    def _get(self, url: str, params: dict) -> dict:
        try:
            r = self.get(url, params=params, headers={"api-key": self.key}, timeout=20)
        except requests.RequestException as exc:
            raise RpcError(f"network: {str(exc).replace(self.key, '***') if self.key else exc}") from None
        if r.status_code != 200:
            raise RpcError(f"HTTP {r.status_code}")
        if self.counter:
            self.counter.add()
        return r.json()

    def address(self, addr: str, details: str = "basic", **params) -> dict:
        return self._get(f"{self.base}/address/{addr}", {"details": details, **params})

    def tx(self, txid: str) -> dict:
        return self._get(f"{self.base}/tx/{txid}", {})

    def fetcher(self) -> Callable[[str, dict], dict]:
        return lambda url, params: self._get(url, params)
