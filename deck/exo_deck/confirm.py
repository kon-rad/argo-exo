"""deck-confirm: speak and log confirmations for the agent Safe, the hot key and ExoModule.

A mined tx is announced from its outcome, never just from being mined: Blockbook's `ethereumSpecific.status`
(1 ok, 0 failed, -1 pending), or the receipt when Blockbook doesn't say. Status 0 is "That payment failed: …",
never "Confirmed". state/frozen follows ExoModule.frozen() on every confirmation and on a timer: the CRE freeze
arrives through the forwarder, not as a tx to the module address, so the address filter alone would miss it."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

from exo_nownodes.wss import is_confirmed

from .state import _write

log = logging.getLogger("deck-confirm")
FROZEN_SELECTOR = "0x054f7d9c"   # frozen()  (cast sig "frozen()")
FROZEN_POLL_S = 30.0


def _status(tx: dict) -> int | None:
    """Blockbook's ethereumSpecific.status: 1, 0, or None when absent / pending (-1) / malformed."""
    es = tx.get("ethereumSpecific")
    st = es.get("status") if isinstance(es, dict) else None
    return st if st in (0, 1) and not isinstance(st, bool) else None


class Announcer:
    def __init__(self, state: Path, speak: Callable[[str], None], frozen_check: Callable[[], bool], module: str,
                 receipt_status: Callable[[str], int | None] | None = None, chain: str = "ethereum"):
        self.state, self.speak, self.frozen_check, self.module = Path(state), speak, frozen_check, module.lower()
        self.receipt_status, self.chain = receipt_status, chain

    def _summary(self, txid: str) -> str:
        d = self.state / "tx-approved"
        for p in sorted(d.glob("*.json"), reverse=True) if d.exists() else []:
            try:
                item = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(item, dict) and str(item.get("sent_tx", "")).lower() == txid.lower():
                return str(item.get("summary", ""))
        return ""

    def sync_frozen(self) -> None:
        try:
            frozen = bool(self.frozen_check())
        except Exception:               # RPC down: leave the flag as it was rather than guess
            return
        if frozen:
            _write(self.state / "frozen", "1\n")
        else:
            (self.state / "frozen").unlink(missing_ok=True)

    def _outcome(self, tx: dict) -> int | None:
        st = _status(tx)
        if st is None and self.receipt_status is not None:
            try:
                r = self.receipt_status(tx.get("txid", ""))
                st = r if r in (0, 1) and not isinstance(r, bool) else None
            except Exception:  # noqa: BLE001
                st = None
        return st

    def handle(self, tx: dict) -> str | None:
        txid = tx.get("txid", "")
        if not txid or not is_confirmed(tx):     # mempool notice: stay silent until it is mined
            return None
        self.sync_frozen()                         # every confirmation: frozen() is the truth, not the address filter
        what = self._summary(txid) or f"transaction {txid[:6]}…{txid[-4:]}"
        status = self._outcome(tx)
        if status == 1:
            text = "Confirmed: " + what
        elif status == 0:
            text = "That payment failed: " + what
        else:
            text = f"Mined: {what}. I couldn't read whether it succeeded."
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / "confirmations.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.time(), "chain": self.chain, "tx": txid, "summary": text,
                                "status": status}) + "\n")
        self.speak(text)
        return text


async def _poll_frozen(a: Announcer, every_s: float = FROZEN_POLL_S) -> None:
    while True:
        await asyncio.to_thread(a.sync_frozen)
        await asyncio.sleep(every_s)


def main() -> None:
    from exo_nownodes import wss
    from exo_nownodes.rpc import Rpc
    from . import tts
    from .config import Settings

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("websockets").setLevel(logging.WARNING)   # DEBUG logs the request line, which carries the key
    s = Settings.from_env()
    module = os.environ["EXO_MODULE_ADDRESS"]
    rpc = Rpc("ethereum")
    frozen_check = lambda: int(rpc.call("eth_call", [{"to": module, "data": FROZEN_SELECTOR}, "latest"]) or "0x0", 16) == 1

    def receipt_status(txid: str) -> int | None:
        r = rpc.call("eth_getTransactionReceipt", [txid])
        st = r.get("status") if isinstance(r, dict) else None
        return {"0x1": 1, "0x0": 0}.get(st)

    a = Announcer(s.state, tts.speak, frozen_check, module, receipt_status=receipt_status)
    addrs = [x.strip() for x in os.environ.get("EXO_WATCH_ADDRESSES", "").split(",") if x.strip()] + [module]

    async def run() -> None:
        poll = asyncio.create_task(_poll_frozen(a))
        try:
            await wss.subscribe_addresses("ethereum", addrs, a.handle)
        finally:
            poll.cancel()

    asyncio.run(run())


if __name__ == "__main__":
    main()
