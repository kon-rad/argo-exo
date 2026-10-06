"""deck-confirm: speak and log confirmations for the agent Safe, the hot key and ExoModule."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Callable

from .state import _write

FROZEN_SELECTOR = "0x054f7d9c"   # frozen()  (cast sig "frozen()")


class Announcer:
    def __init__(self, state: Path, speak: Callable[[str], None], frozen_check: Callable[[], bool], module: str):
        self.state, self.speak, self.frozen_check, self.module = Path(state), speak, frozen_check, module.lower()

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

    def _sync_frozen(self) -> None:
        try:
            frozen = bool(self.frozen_check())
        except Exception:               # RPC down: leave the flag as it was rather than guess
            return
        if frozen:
            _write(self.state / "frozen", "1\n")
        else:
            (self.state / "frozen").unlink(missing_ok=True)

    def handle(self, tx: dict) -> str | None:
        txid = tx.get("txid", "")
        if not txid:
            return None
        addrs = {a.lower() for part in ("vin", "vout") for io in tx.get(part, []) for a in io.get("addresses", [])}
        if self.module in addrs:
            self._sync_frozen()
        text = "Confirmed: " + (self._summary(txid) or f"transaction {txid[:6]}…{txid[-4:]}")
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / "confirmations.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.time(), "tx": txid, "summary": text}) + "\n")
        self.speak(text)
        return text


def main() -> None:
    from exo_nownodes import wss
    from exo_nownodes.rpc import Rpc
    from . import tts
    from .config import Settings

    s = Settings.from_env()
    module = os.environ["EXO_MODULE_ADDRESS"]
    rpc = Rpc("ethereum")
    frozen_check = lambda: int(rpc.call("eth_call", [{"to": module, "data": FROZEN_SELECTOR}, "latest"]) or "0x0", 16) == 1
    a = Announcer(s.state, tts.speak, frozen_check, module)
    addrs = [x.strip() for x in os.environ.get("EXO_WATCH_ADDRESSES", "").split(",") if x.strip()] + [module]
    asyncio.run(wss.subscribe_addresses("ethereum", addrs, a.handle))


if __name__ == "__main__":
    main()
