"""Blockbook WebSocket: subscribeAddresses with reconnect, re-subscribe, keepalive and txid dedup.

NOWNodes embeds the API key in the WSS path (/wss/{key}), so the URL is never logged unredacted."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Callable

import websockets

from . import hosts

log = logging.getLogger("exo-nownodes-wss")
SEEN_CAP = 5000


def redact_url(url: str) -> str:
    return re.sub(r"/wss/[^/?#]+", "/wss/***", url)


async def subscribe_addresses(chain: str, addresses: list[str], on_tx: Callable[[dict], None], api_key: str | None = None,
                              connect: Callable = websockets.connect, stop: asyncio.Event | None = None,
                              backoff=(1, 2, 5, 10)) -> None:
    key = api_key if api_key is not None else os.environ.get("NOWNODES_API_KEY", "")
    url = hosts.WSS[chain] + key
    stop = stop or asyncio.Event()
    seen: dict[str, None] = {}          # insertion-ordered; survives reconnects so replays are not re-announced
    attempt = 0
    while not stop.is_set():
        try:
            async with connect(url, ping_interval=30) as ws:
                await ws.send(json.dumps({"id": "1", "method": "subscribeAddresses", "params": {"addresses": addresses}}))
                attempt = 0
                while not stop.is_set():
                    msg = json.loads(await ws.recv())
                    tx = (msg.get("data") or {}).get("tx") if isinstance(msg, dict) else None
                    txid = tx.get("txid") if isinstance(tx, dict) else None
                    if not txid or txid in seen:
                        continue
                    seen[txid] = None
                    if len(seen) > SEEN_CAP:
                        seen.pop(next(iter(seen)))
                    try:
                        on_tx(tx)
                    except Exception as exc:    # a handler bug must not drop the socket or lose the dedup entry
                        log.error("on_tx failed for %s: %s", txid, type(exc).__name__)
        except Exception as exc:  # network drops, server closes
            if stop.is_set():
                break
            delay = backoff[min(attempt, len(backoff) - 1)]
            log.warning("wss %s dropped (%s); retry in %ss", redact_url(url), type(exc).__name__, delay)
            attempt += 1
            await asyncio.sleep(delay)
