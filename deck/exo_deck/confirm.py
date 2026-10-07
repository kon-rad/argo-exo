"""deck-confirm: speak and log confirmations for the agent Safe, the hot key and ExoModule.

A mined tx is announced from its outcome, never just from being mined: Blockbook's `ethereumSpecific.status`
(1 ok, 0 failed, -1 pending), or the receipt when Blockbook doesn't say. Status 0 is "That payment failed: …",
never "Confirmed". state/frozen follows ExoModule.frozen() on every confirmation and on a timer: the CRE freeze
arrives through the forwarder, not as a tx to the module address, so the address filter alone would miss it.

Two sources of mined txs (EXO_CONFIRM_MODE):
  poll (default)  the receipts of the txs this deck sent (state/tx-approved/*.json with sent_tx), every 3 s.
                  NOWNodes' Start plan has no WebSocket (403, checked 2026-10-07); a few receipt calls per tx.
  wss             Blockbook WebSocket on the watched addresses (Pro plan): also announces txs the deck didn't send."""
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
RECEIPT_POLL_S = 3.0
RECENT_S = 3600.0        # only items touched in the last hour: a restart never announces last week's payments


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


class ReceiptPoller:
    """Announce each tx this deck sent, once, when its receipt appears. Failures spoken elsewhere (dropped,
    rejected) are skipped; "reverted onchain" is ours to speak (settle() stays quiet for it)."""

    def __init__(self, announcer: Announcer, receipt_status: Callable[[str], int | None],
                 now: Callable[[], float] = time.time):
        self.a, self.receipt_status, self.now = announcer, receipt_status, now
        self.done = self._already_announced()

    def _already_announced(self) -> set[str]:
        done = set()
        try:
            for line in (self.a.state / "confirmations.jsonl").read_text().splitlines():
                try:
                    done.add(str(json.loads(line).get("tx", "")).lower())
                except ValueError:
                    continue
        except OSError:
            pass
        return done

    def _candidates(self) -> list[str]:
        d = self.a.state / "tx-approved"
        out = []
        for p in sorted(d.glob("*.json")) if d.exists() else []:
            try:
                if self.now() - p.stat().st_mtime > RECENT_S:
                    continue
                item = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            txid = item.get("sent_tx") if isinstance(item, dict) else None
            if not isinstance(txid, str) or not txid.startswith("0x") or txid.lower() in self.done:
                continue
            if item.get("failed") not in (None, "reverted onchain"):
                continue
            out.append(txid)
        return out

    def poll_once(self) -> list[str]:
        said = []
        for txid in self._candidates():
            try:
                st = self.receipt_status(txid)
            except Exception:  # noqa: BLE001  node trouble: try again next round
                continue
            if st not in (0, 1) or isinstance(st, bool):
                continue                                   # still in the mempool
            text = self.a.handle({"txid": txid, "confirmations": 1, "ethereumSpecific": {"status": st}})
            self.done.add(txid.lower())
            if text:
                said.append(text)
        return said


async def _poll_receipts(p: ReceiptPoller, every_s: float = RECEIPT_POLL_S) -> None:
    while True:
        try:
            await asyncio.to_thread(p.poll_once)
        except Exception as exc:  # noqa: BLE001  a bad round never stops the poller
            log.warning("receipt poll failed (%s)", type(exc).__name__)
        await asyncio.sleep(every_s)


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

    mode = os.environ.get("EXO_CONFIRM_MODE", "poll").strip().lower()

    async def run() -> None:
        poll = asyncio.create_task(_poll_frozen(a))
        try:
            if mode == "wss":
                await wss.subscribe_addresses("ethereum", addrs, a.handle)
            else:
                log.info("confirmations: polling receipts of sent txs every %.0f s (EXO_CONFIRM_MODE=wss for the WebSocket)", RECEIPT_POLL_S)
                await _poll_receipts(ReceiptPoller(a, receipt_status))
        finally:
            poll.cancel()

    asyncio.run(run())


if __name__ == "__main__":
    main()
