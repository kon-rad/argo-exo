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


def is_confirmed(tx: dict) -> bool:
    """Blockbook also notifies on mempool entry (confirmations 0, blockHeight 0 or -1)."""
    def num(k):
        v = tx.get(k)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
    return num("confirmations") >= 1 or num("blockHeight") > 0


async def subscribe_addresses(chain: str, addresses: list[str], on_tx: Callable[[dict], None], api_key: str | None = None,
                              connect: Callable = websockets.connect, stop: asyncio.Event | None = None,
                              backoff=(1, 2, 5, 10)) -> None:
    """Calls on_tx once per confirmed txid, in order, on a worker thread so recv/pings never stall."""
    key = api_key if api_key is not None else os.environ.get("NOWNODES_API_KEY", "")
    url = hosts.WSS[chain] + key
    stop = stop or asyncio.Event()
    seen: dict[str, None] = {}          # confirmed txids only; survives reconnects so replays are not re-announced
    queue: asyncio.Queue = asyncio.Queue()

    async def worker():
        while True:
            tx = await queue.get()
            try:
                await asyncio.to_thread(on_tx, tx)
            except Exception as exc:    # a handler bug must not kill the worker
                log.error("on_tx failed for %s: %s", tx.get("txid"), type(exc).__name__)
            finally:
                queue.task_done()

    worker_task = asyncio.create_task(worker())
    attempt = 0
    try:
        while not stop.is_set():
            try:
                async with connect(url, ping_interval=30) as ws:
                    await ws.send(json.dumps({"id": "1", "method": "subscribeAddresses", "params": {"addresses": addresses}}))
                    acked = False
                    while not stop.is_set():
                        msg = json.loads(await ws.recv())
                        data = msg.get("data") if isinstance(msg, dict) else None
                        data = data if isinstance(data, dict) else {}
                        if not acked:
                            if data.get("subscribed") is True:
                                acked, attempt = True, 0
                                continue
                            raise ConnectionError("subscribe not acknowledged")
                        tx = data.get("tx")
                        txid = tx.get("txid") if isinstance(tx, dict) else None
                        if not txid or txid in seen or not is_confirmed(tx):
                            continue
                        seen[txid] = None
                        if len(seen) > SEEN_CAP:
                            seen.pop(next(iter(seen)))
                        queue.put_nowait(tx)
            except Exception as exc:  # network drops, server closes
                if stop.is_set():
                    break
                delay = backoff[min(attempt, len(backoff) - 1)]
                log.warning("wss %s dropped (%s); retry in %ss", redact_url(url), type(exc).__name__, delay)
                attempt += 1
                await asyncio.sleep(delay)
        await queue.join()
    finally:
        worker_task.cancel()
