"""deck-queue-sync: pull Guardian-approved items from exo-bridge into the approve-key queue.

Nothing reaches tx-queue/ on the bridge's word alone. Each new item is confirmed onchain first (OnchainCheck):
the approval hash is recomputed here from the item's own fields, and ExoModule must say approvedUntil(hash) > now
(and no later than the item's expires_at), used(hash) == false and frozen() == false. A forged ledger row therefore
never shows up on the key."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

from .exo_module import CHAIN_IDS, ItemError, approval_hash, chain_id, parse_item, read_state

log = logging.getLogger("deck-queue-sync")

KNOWN_DIRS = ("tx-queue", "tx-approved", "tx-expired", "tx-rejected")
SAFE_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")      # ledger ids are UUIDs; the id becomes part of a filename
POLL_S = 3.0
RETRY_S = 15.0                                     # re-check a refused id at most this often (report tx may be unmined)


def _known(state: Path) -> set[str]:
    ids = set()
    for d in KNOWN_DIRS:
        for p in (state / d).glob("*.json") if (state / d).exists() else []:
            ids.add(p.stem.split("_", 1)[-1])
    return ids


def sync_once(state: Path, items: list, verify: Callable[[dict], dict | None]) -> list[str]:
    """Write each new, verified item to tx-queue/<ns>_<id>.json (atomic rename). Returns the new ids.
    `verify(item)` returns the item to queue (possibly rewritten) or None to skip; an exception skips too."""
    known, new = _known(state), []
    (state / "tx-queue").mkdir(parents=True, exist_ok=True)
    for it in items if isinstance(items, list) else []:
        pid = it.get("id") if isinstance(it, dict) else None
        if not isinstance(pid, str) or not SAFE_ID.match(pid) or pid in known:
            continue
        try:
            ok = verify(it)
        except Exception as exc:  # noqa: BLE001
            log.warning("skip %s: verify failed (%s)", pid, type(exc).__name__)
            continue
        if not isinstance(ok, dict) or ok.get("id") != pid:
            continue
        name = f"{time.time_ns():020d}_{pid}.json"
        tmp = state / "tx-queue" / ("." + name + ".tmp")     # not *.json: never seen half-written
        tmp.write_text(json.dumps(ok))
        tmp.replace(state / "tx-queue" / name)
        known.add(pid)
        new.append(pid)
    if new:
        tmp = state / ".verdict.tmp"
        tmp.write_text("approve\n")
        tmp.replace(state / "verdict")
    return new


class OnchainCheck:
    """verify() for sync_once: the item must match a live, unused approval on an unfrozen ExoModule."""

    def __init__(self, rpc, module: str, now: Callable[[], float] = time.time, retry_s: float = RETRY_S):
        self.rpc, self.module, self.now, self.retry_s = rpc, module, now, retry_s
        self._chain_id: int | None = None
        self._retry_at: dict[str, float] = {}

    def _skip(self, pid: str, why: str) -> None:
        log.info("skip %s: %s", pid, why)
        self._retry_at[pid] = self.now() + self.retry_s
        return None

    def __call__(self, item: dict) -> dict | None:
        pid = item.get("id")
        if self._retry_at.get(pid, 0) > self.now():
            return None
        try:
            call = parse_item(item, self.module)
        except ItemError as exc:
            return self._skip(pid, f"invalid item ({exc})")
        try:
            if self._chain_id is None:
                cid = chain_id(self.rpc)
                if cid != CHAIN_IDS[item["chain"]]:
                    return self._skip(pid, "rpc is on the wrong network")
                self._chain_id = cid
            h = approval_hash(self._chain_id, self.module, call.to, call.value, call.data, call.salt)
            stated = item.get("hash")
            if stated is not None and (not isinstance(stated, str) or stated.lower() != "0x" + h.hex()):
                return self._skip(pid, "stated hash does not match the item")
            s = read_state(self.rpc, self.module, h)
        except Exception as exc:  # noqa: BLE001  (RpcError text stays on the deck, and only its type is logged)
            return self._skip(pid, f"onchain check failed ({type(exc).__name__})")
        now = self.now()
        exp = item.get("expires_at")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return self._skip(pid, "no expiry")
        if s.frozen:
            return self._skip(pid, "module frozen")
        if s.used:
            return self._skip(pid, "approval already used")
        if not (now < s.approved_until <= exp):
            return self._skip(pid, "no live onchain approval")
        self._retry_at.pop(pid, None)
        return dict(item, expires_at=s.approved_until)    # the deck expires it when the chain does


def main() -> None:
    import os

    from exo_nownodes.rpc import Rpc

    from .bridge_client import Bridge, BridgeError
    from .config import Settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    s = Settings.from_env()
    bridge = Bridge(s.bridge_url, s.bridge_token)
    check = OnchainCheck(Rpc("ethereum"), os.environ["EXO_MODULE_ADDRESS"])
    while True:
        try:
            new = sync_once(s.state, bridge.pending_approvals(), check)
            if new:
                log.info("queued %s", ", ".join(new))
        except BridgeError as exc:
            log.info("bridge unavailable (%s)", type(exc).__name__)
        except Exception as exc:  # noqa: BLE001  a bad poll never kills the service
            log.warning("sync failed (%s)", type(exc).__name__)
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
