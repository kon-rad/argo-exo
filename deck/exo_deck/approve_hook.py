"""hooks/approve <state/tx-approved/item.json>: sign ExoModule.execute for an approved item and broadcast it.

Idempotent and crash-safe. The item file is the record:
  sending  {"tx_hash", "raw"}  written atomically BEFORE the broadcast. A re-run never signs again: it asks the
                               node for the receipt / the tx, and if the node has never seen it, rebroadcasts
                               the identical signed bytes (same nonce, same hash: it can land at most once).
  sent_tx  "0x…"               the broadcast tx hash (also written to state/tx-sent for the key LED, and read by
                               deck-confirm for "Confirmed: <summary>"). Broadcast is NOT executed: nothing is
                               reported yet. deck-queue-sync's sweep settles it from the receipt (settle()).
  settled  "executed"          the receipt says status 1; reported to the bridge as executed(tx_hash).
  failed   "<fixed message>"   a definite failure (before signing, a rejected broadcast, a receipt with status 0
                               "reverted onchain", or "dropped": unknown to the node after the approval expired);
                               reported as failed(error). A re-run does nothing but retry the report.
  reported true                exo-bridge recorded the outcome (or answered 409: it was no longer waiting).

Right before signing it recomputes the approval hash from the item and re-reads ExoModule: approvedUntil(h) must
be live, used(h) false and frozen() false. Only fixed messages go to the bridge or the speaker; the key, the
passphrase and raw RPC errors never leave the deck."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable

from eth_utils import keccak
from exo_nownodes.rpc import RpcError

from .exo_module import CHAIN_IDS, ItemError, approval_hash, chain_id, execute_calldata, parse_item, read_state
from .signer import FeeTooHigh, build_tx, key_lock
from .state import _write

log = logging.getLogger("hooks/approve")

MIN_TTL_S = 12          # don't spend gas on an approval that will likely expire before the tx is mined
SPOKEN = {
    "invalid queue item": "the queued item was malformed",
    "wrong network": "the node is on the wrong network",
    "frozen": "the module is frozen",
    "approval already used": "it was already executed",
    "not approved onchain": "the approval isn't live onchain",
    "rpc unavailable": "the node didn't answer",
    "gas estimate reverted": "the module would reject it",
    "fee too high": "gas is too expensive right now",
    "broadcast rejected": "the node rejected it",
    "reverted onchain": "it reverted onchain",
    "dropped": "it never made it onchain",
    "deck error": "something went wrong on the deck",
}
AMBIGUOUS = ("already known", "known transaction", "nonce too low")
DROP_GRACE_S = 120      # unknown to the node this long after the approval expired: it can never land, call it dropped


class _Fail(Exception):
    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg


def _speak(text: str) -> None:
    from . import tts
    tts.speak(text)


def _save(path: Path, item: dict, durable: bool = False) -> None:
    _write(path, json.dumps(item), durable=durable)


def _rpc_fail(exc: RpcError, method: str) -> _Fail:
    """A JSON-RPC error reply to eth_estimateGas is a revert; anything else is the node being unavailable."""
    if method == "eth_estimateGas" and str(exc).startswith("eth_estimateGas:"):
        return _Fail("gas estimate reverted")
    return _Fail("rpc unavailable")


def _probe(rpc, txh: str) -> str:
    """'ok' | 'reverted' (mined) · 'pending' (in the mempool) · 'unknown'. Raises on RPC trouble."""
    r = rpc.call("eth_getTransactionReceipt", [txh])
    if isinstance(r, dict):
        st = r.get("status")
        if st == "0x1":
            return "ok"
        if st == "0x0":
            return "reverted"
        raise ValueError("malformed receipt")
    if r is not None:
        raise ValueError("malformed receipt")
    return "pending" if rpc.call("eth_getTransactionByHash", [txh]) else "unknown"


class _Hook:
    def __init__(self, path: Path, signer, rpc, bridge, module: str, speak: Callable[[str], None],
                 now: Callable[[], float], resume_only: bool = False):
        self.path, self.signer, self.rpc, self.bridge, self.module = path, signer, rpc, bridge, module
        self._speak, self.now, self.resume_only = speak, now, resume_only
        self.state = path.parent.parent
        self.after: list[Callable[[], None]] = []   # bridge reports and speech: run once the key lock is released

    def speak(self, text: str) -> None:
        self.after.append(lambda: self._speak(text))

    # --- outcomes -------------------------------------------------------------------------------------------
    def report(self, item: dict, **kw) -> None:
        self.after.append(lambda pid=item["id"]: self._report_now(pid, kw))

    def _report_now(self, pid: str, kw: dict) -> None:
        from .bridge_client import BridgeError
        try:
            self.bridge.report_executed(pid, **kw)             # True, or False on 409 (no longer waiting)
        except (BridgeError, ValueError) as exc:
            log.warning("report for %s failed (%s); a re-run retries it", pid, type(exc).__name__)
            return
        with key_lock(self.state):                             # brief: just the file update
            try:
                cur = json.loads(self.path.read_text())
            except (OSError, ValueError):
                return
            if isinstance(cur, dict) and cur.get("id") == pid and not cur.get("reported"):
                cur["reported"] = True
                _save(self.path, cur)

    def sent(self, item: dict, txh: str) -> str:
        """Broadcast (or seen by the node). Not an outcome: the bridge hears nothing until settle() reads the receipt."""
        item["sent_tx"] = txh
        _save(self.path, item, durable=True)
        _write(self.state / "tx-sent", txh)
        log.info("sent %s for %s", txh, item["id"])
        return txh

    def executed(self, item: dict, txh: str) -> str:
        """A receipt with status 1: the one outcome reported as executed(tx_hash)."""
        item["sent_tx"], item["settled"] = txh, "executed"
        _save(self.path, item, durable=True)
        log.info("executed %s for %s", txh, item["id"])
        self.report(item, tx_hash=txh)
        return txh

    def fail(self, item: dict, msg: str, quiet: bool = False) -> None:
        item["failed"] = msg
        _save(self.path, item, durable=True)
        log.info("failed %s: %s", item.get("id"), msg)
        self.report(item, error=msg)
        if not quiet:
            self.speak(f"That transaction didn't go through: {SPOKEN.get(msg, msg)}.")
        return None

    def settle(self, item: dict) -> str | None:
        """The sweep's job for a broadcast item: read its receipt. status 1 → executed(tx_hash); status 0 → failed
        ("reverted onchain", spoken by deck-confirm when it sees the mined tx, so quiet here); unknown to the node
        past the approval's expiry → failed ("dropped", e.g. replaced by the freeze), spoken here since nothing is
        mined for deck-confirm to announce. In the mempool, or the node unsure: wait for the next sweep."""
        txh = item["sent_tx"]
        try:
            seen = _probe(self.rpc, txh)
        except (RpcError, ValueError):
            return None
        if seen == "ok":
            return self.executed(item, txh)
        if seen == "reverted":
            return self.fail(item, "reverted onchain", quiet=True)
        exp = item.get("expires_at")
        if seen == "unknown" and isinstance(exp, (int, float)) and not isinstance(exp, bool) \
                and self.now() > exp + DROP_GRACE_S:
            self.fail(item, "dropped", quiet=True)
            summary = str(item.get("summary") or "").strip()[:120]
            self.speak(f"That payment failed: {summary or 'a transaction'}. It never made it onchain.")
        return None

    def unsure(self, item: dict) -> None:
        log.warning("%s: broadcast outcome unknown; marker kept, deck-queue-sync retries it", item.get("id"))
        if self.resume_only:
            return None                                         # the recovery sweep stays quiet while unsure
        self.speak("I'm not sure that transaction went out. I'll keep it and won't sign it again.")
        return None

    # --- the paths ------------------------------------------------------------------------------------------
    def broadcast(self, item: dict, raw_hex: str, txh: str) -> str | None:
        try:
            self.rpc.call("eth_sendRawTransaction", [raw_hex])
            return self.sent(item, txh)          # the hash is ours (keccak of the signed bytes), not the node's
        except RpcError as exc:
            msg = str(exc)
            rejected = msg.startswith("eth_sendRawTransaction:") and not any(a in msg.lower() for a in AMBIGUOUS)
        try:
            seen = _probe(self.rpc, txh)
        except (RpcError, ValueError):
            seen = None
        if seen == "ok":
            self.sent(item, txh)
            return self.executed(item, txh)
        if seen == "pending":
            return self.sent(item, txh)
        if seen == "reverted":
            return self.fail(item, "reverted onchain")
        if rejected:
            return self.fail(item, "broadcast rejected")
        return self.unsure(item)

    def resume(self, item: dict) -> str | None:
        m = item["sending"]
        txh, raw_hex = (m.get("tx_hash"), m.get("raw")) if isinstance(m, dict) else (None, None)
        try:
            raw = bytes.fromhex(raw_hex[2:]) if isinstance(raw_hex, str) and raw_hex.startswith("0x") else b""
        except ValueError:
            raw = b""
        if not raw or not isinstance(txh, str) or "0x" + keccak(raw).hex() != txh.lower():
            log.error("%s: corrupt sending marker; not touching it", item.get("id"))
            return self.unsure(item)
        try:
            seen = _probe(self.rpc, txh)
        except (RpcError, ValueError):
            return self.unsure(item)
        if seen == "ok":
            self.sent(item, txh)
            return self.executed(item, txh)
        if seen == "pending":
            return self.sent(item, txh)
        if seen == "reverted":
            return self.fail(item, "reverted onchain")
        return self.broadcast(item, raw_hex, txh)        # never seen: the identical bytes again

    def put_back(self, item: dict) -> None:
        dest = self.state / "tx-queue" / self.path.name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.path.replace(dest)
        except OSError:
            pass
        self.speak("The hot key is locked. Run exo-unlock, then press approve again.")

    def fresh(self, item: dict) -> str | None:
        if self.resume_only:
            return None                                         # the recovery sweep never signs
        if self.signer is None:
            return self.put_back(item)
        method = "eth_chainId"
        try:
            call = parse_item(item, self.module)
            cid = chain_id(self.rpc)
            if cid != CHAIN_IDS[item["chain"]]:
                raise _Fail("wrong network")
            h = approval_hash(cid, self.module, call.to, call.value, call.data, call.salt)
            stated = item.get("hash")
            if stated is not None and (not isinstance(stated, str) or stated.lower() != "0x" + h.hex()):
                raise _Fail("invalid queue item")
            method = "eth_call"
            s = read_state(self.rpc, self.module, h)
            if s.frozen:
                raise _Fail("frozen")
            if s.used:
                raise _Fail("approval already used")
            if s.approved_until <= self.now() + MIN_TTL_S:
                raise _Fail("not approved onchain")
            data = execute_calldata(call.to, call.value, call.data, call.salt)
            method = "eth_estimateGas"
            tx = build_tx(self.rpc, self.signer.address, self.module, data)   # value 0: the Safe pays `value`
            if tx["chainId"] != cid:
                raise _Fail("wrong network")
            raw = bytes(self.signer.sign(tx))
        except _Fail as f:
            return self.fail(item, f.msg)
        except ItemError:
            return self.fail(item, "invalid queue item")
        except FeeTooHigh:
            return self.fail(item, "fee too high")
        except RpcError as exc:
            return self.fail(item, _rpc_fail(exc, method).msg)
        except ValueError:
            return self.fail(item, "rpc unavailable")        # a malformed node reply
        except Exception as exc:  # noqa: BLE001
            log.error("%s: %s before signing", item.get("id"), type(exc).__name__)
            return self.fail(item, "deck error")
        txh, raw_hex = "0x" + keccak(raw).hex(), "0x" + raw.hex()
        item["sending"] = {"tx_hash": txh, "raw": raw_hex}
        _save(self.path, item, durable=True)                    # on disk BEFORE the broadcast: no double-send
        return self.broadcast(item, raw_hex, txh)

    def run(self) -> str | None:
        try:
            item = json.loads(self.path.read_text())
        except (OSError, ValueError):
            log.error("unreadable item %s", self.path.name)
            return None
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            log.error("malformed item %s", self.path.name)
            return None
        if item.get("failed"):
            if not item.get("reported"):
                self.report(item, error=item["failed"])
            return None                                         # already decided: never broadcast twice
        if item.get("sent_tx"):
            if item.get("settled") == "executed":
                if not item.get("reported"):
                    self.report(item, tx_hash=item["sent_tx"])
                return None
            return self.settle(item) if self.resume_only else None   # never broadcast twice; the sweep settles it
        if item.get("sending") is not None:
            return self.resume(item)
        return self.fresh(item)


def run_hook(item_path: Path, signer, rpc, bridge, module: str, speak: Callable[[str], None],
             now: Callable[[], float] = time.time, resume_only: bool = False) -> str | None:
    """Returns the broadcast tx hash, or None (already handled, failed, or outcome unknown).
    resume_only=True (the recovery sweep) completes markers and reports but never signs."""
    item_path = Path(item_path)
    hook = _Hook(item_path, signer, rpc, bridge, module, speak, now, resume_only)
    with key_lock(item_path.parent.parent):                    # nonce -> sign -> broadcast, one signer at a time
        result = hook.run()
    for fn in hook.after:                                       # the bridge POST and TTS never hold the lock
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("after-step failed (%s)", type(exc).__name__)
    return result


def needs_recovery(item: dict) -> bool:
    """A marker without an outcome, a broadcast tx not yet settled from its receipt, or an outcome the bridge
    hasn't recorded yet."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return False
    if item.get("failed"):
        return not item.get("reported")
    if item.get("sent_tx"):
        return item.get("settled") != "executed" or not item.get("reported")
    return item.get("sending") is not None


def sweep(state: Path, rpc, bridge, module: str, speak: Callable[[str], None], now: Callable[[], float] = time.time,
          last_try: dict | None = None, every_s: float = 30.0) -> list[str]:
    """Finish what a crash or an unsure broadcast left in tx-approved/: resume markers, settle broadcast txs from
    their receipts (executed / failed / dropped), retry reports. Never signs.
    Each item is retried at most every `every_s`. Returns the ids it looked at."""
    last_try = {} if last_try is None else last_try
    d = Path(state) / "tx-approved"
    seen = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            item = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not needs_recovery(item) or now() - last_try.get(p.name, float("-inf")) < every_s:
            continue
        last_try[p.name] = now()
        run_hook(p, None, rpc, bridge, module, speak, now=now, resume_only=True)
        seen.append(item["id"])
    return seen


def main(argv: list[str]) -> int:
    from exo_nownodes.rpc import Rpc

    from .bridge_client import Bridge
    from .config import Settings
    from .signer import HotSigner, SignerLocked

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if len(argv) != 1:
        print("usage: hooks/approve <state/tx-approved/item.json>", file=sys.stderr)
        return 2
    path = Path(argv[0]).resolve()
    if path.parent.name != "tx-approved" or path.suffix != ".json":
        log.error("refusing %s: not an item in tx-approved/", path)
        return 2
    module = os.environ.get("EXO_MODULE_ADDRESS", "")
    if not module:
        log.error("EXO_MODULE_ADDRESS is not set; nothing signed")
        return 2
    try:
        signer = HotSigner(Path(os.environ.get("EXO_HOT_KEYSTORE", "/srv/deck/keys/hot.json")),
                           Path(os.environ.get("EXO_HOT_PASSFILE", "/run/exo/hot.pass")))
    except SignerLocked:
        log.warning("hot key locked")
        signer = None                                           # resume/report paths still work without it
    s = Settings.from_env()
    txh = run_hook(path, signer, Rpc("ethereum"), Bridge(s.bridge_url, s.bridge_token), module, _speak)
    return 0 if txh else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
