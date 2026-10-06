"""guardian-run: request → ledger row → CRE `guard` simulation → checked verdict → approval queue.

The queue is what the deck's approve key executes, so nothing reaches it unless all of these hold:
  * the workflow said `approve` for this proposal, in a well-formed result;
  * the run was a --broadcast run and its report_tx is a real (non-zero) tx hash, i.e. the approval is onchain;
  * the workflow's tx_hash equals the approval hash recomputed here from the exact to/value/data/salt sent;
  * the approval hasn't expired.
Anything else is recorded in the ledger as refused (or `simulated` for a dry-run approval) and never queued.
The salt, the proposal id, requested_at and context.spent_today_usd are set here, never taken from the requester.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from .hashing import approval_hash
from .simulate import CRE_DIR, SimulationError, run_simulation
from .store import is_real_tx

log = logging.getLogger("exo-guardian")

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
HEXBYTES = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
UINT = re.compile(r"^\d{1,78}$")
UINT256_MAX = 2 ** 256 - 1
# The intent fields the workflow's GuardRequestSchema reads, with its length caps.
INTENT_FIELDS = {"kind": 32, "summary": 500, "token": 64, "amount": 80, "to": 64}
RISKS = ("low", "medium", "high")
ZERO_ADDRESS = "0x" + "0" * 40


class InvalidRequest(ValueError):
    """The caller's input is malformed (the bridge answers 400). Nothing was written."""


def workflow_binding(config_path: Path = CRE_DIR / "exo" / "config.mainnet.json") -> tuple[int, str]:
    """(chainId, module) from the same config file the workflow runs with, so both sides hash the same way."""
    cfg = json.loads(Path(config_path).read_text())
    return int(cfg["chainId"]), str(cfg["module"])


def _clean_text(s: str, limit: int) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", s)).strip()[:limit]


def _parse_request(req) -> dict:
    if not isinstance(req, dict):
        raise InvalidRequest("request must be an object")
    tx, frm, intent, source = req.get("tx"), req.get("from"), req.get("intent"), req.get("source", "agent")
    if not isinstance(tx, dict):
        raise InvalidRequest("tx required")
    to, value, data, chain_id = tx.get("to"), tx.get("value", "0"), tx.get("data", "0x"), tx.get("chain_id")
    if not isinstance(to, str) or not ADDRESS.match(to):
        raise InvalidRequest("tx.to must be a 0x address")
    if isinstance(value, bool) or not ((isinstance(value, str) and UINT.match(value)) or (isinstance(value, int) and value >= 0)):
        raise InvalidRequest("tx.value must be a decimal wei amount")
    value = int(value)
    if value > UINT256_MAX:
        raise InvalidRequest("tx.value exceeds uint256")
    if not isinstance(data, str) or not HEXBYTES.match(data):
        raise InvalidRequest("tx.data must be 0x-prefixed hex bytes")
    if isinstance(chain_id, bool) or not isinstance(chain_id, int) or chain_id <= 0:
        raise InvalidRequest("tx.chain_id must be a positive integer")
    if not isinstance(frm, str) or not ADDRESS.match(frm):
        raise InvalidRequest("from must be a 0x address")
    if not isinstance(source, str) or len(source) > 100:
        raise InvalidRequest("source must be a string")
    if not isinstance(intent, dict) or not isinstance(intent.get("kind"), str) or not isinstance(intent.get("summary"), str):
        raise InvalidRequest("intent needs kind and summary")
    clean_intent = {}
    for k, cap in INTENT_FIELDS.items():
        v = intent.get(k)
        if v is None:
            continue
        if not isinstance(v, str) or len(v) > cap:
            raise InvalidRequest(f"intent.{k} must be a string of at most {cap} characters")
        clean_intent[k] = v
    return {"source": source, "intent": clean_intent, "from": frm.lower(),
            "tx": {"chain_id": chain_id, "to": to.lower(), "value": str(value), "data": data.lower()}}


def _well_formed(r, pid: str) -> bool:
    return (isinstance(r, dict) and r.get("proposal_id") == pid and r.get("verdict") in ("approve", "refuse")
            and r.get("risk") in RISKS and isinstance(r.get("auto_eligible"), bool)
            and isinstance(r.get("explanation"), str) and isinstance(r.get("reasons"), list)
            and all(isinstance(x, str) for x in r["reasons"])
            and isinstance(r.get("expires_at"), int) and not isinstance(r.get("expires_at"), bool)
            and (r.get("tx_hash") is None or isinstance(r.get("tx_hash"), str))
            and (r.get("report_tx") is None or isinstance(r.get("report_tx"), str))
            and (r.get("usd_out") is None or (isinstance(r.get("usd_out"), (int, float)) and not isinstance(r.get("usd_out"), bool))))


class Guardian:
    def __init__(self, store, simulate=run_simulation, clock=time.time, salt=os.urandom, *,
                 module: str | None = None, chain_id: int | None = None, broadcast: bool = False):
        """`broadcast=False` (the default) runs the simulator as a dry run: approvals land as `simulated` and are
        never queued, and freeze reports `ok: false`. Only a broadcast run writes reports onchain (real
        transactions from the simulator key), so it has to be switched on deliberately."""
        if module is None or chain_id is None:
            cid, mod = workflow_binding()
            module, chain_id = module or mod, chain_id or cid
        if not ADDRESS.match(module):
            raise ValueError("module must be a 0x address")
        self.store, self.simulate, self.clock, self.salt = store, simulate, clock, salt
        self.module, self.chain_id, self.broadcast = module.lower(), int(chain_id), bool(broadcast)

    # ── guard ──────────────────────────────────────────────────────────────────────────────────────────────────
    def guard(self, req: dict) -> dict:
        r = _parse_request(req)
        salt = self.salt(32)
        if not isinstance(salt, (bytes, bytearray)) or len(salt) != 32:
            raise RuntimeError("salt source must return 32 bytes")
        pid, now = str(uuid.uuid4()), int(self.clock())
        tx = dict(r["tx"], salt="0x" + bytes(salt).hex())
        self.store.insert_proposal({"id": pid, "created_at": now, "source": r["source"], "intent": r["intent"],
                                    "chain": "ethereum", "to_addr": tx["to"], "value_wei": int(tx["value"]),
                                    "data": tx["data"], "salt": tx["salt"]})
        payload = {"proposal_id": pid, "source": r["source"], "intent": r["intent"], "tx": tx, "from": r["from"],
                   "requested_at": now, "context": {"spent_today_usd": float(self.store.spent_today_usd(now))}}

        t0 = time.monotonic()
        try:
            result, ms = self.simulate(payload, 0, self.broadcast)
        except Exception as exc:  # noqa: BLE001  (any failure is a refusal, never an approval)
            ms = int((time.monotonic() - t0) * 1000)
            kind = exc.kind if isinstance(exc, SimulationError) else "runner error"
            log.warning("guard %s: simulation failed (%s)", pid, kind)
            return self._refuse(pid, ms, ["guardian unavailable"], "Refused: Guardian unavailable.", unavailable=True)

        if not _well_formed(result, pid):
            log.warning("guard %s: malformed workflow result", pid)
            return self._refuse(pid, ms, ["malformed workflow result"], "Refused: the Guardian's answer was unreadable.",
                                base=result if isinstance(result, dict) else None)
        result = {k: result.get(k) for k in ("verdict", "risk", "auto_eligible", "explanation", "reasons", "tx_hash",
                                              "expires_at", "changes", "report_tx", "usd_out")}
        result.update(proposal_id=pid, report_tx=result["report_tx"] or "", tx_hash=result["tx_hash"] or "",
                      changes=result["changes"] if isinstance(result["changes"], list) else [])

        if result["verdict"] != "approve":
            return self._record(pid, result, ms, "refused")
        expected = "0x" + approval_hash(self.chain_id, self.module, tx["to"], int(tx["value"]),
                                        bytes.fromhex(tx["data"][2:]), bytes(salt)).hex()
        if not HASH.match(result["tx_hash"]) or result["tx_hash"].lower() != expected:
            log.warning("guard %s: approval hash mismatch", pid)
            return self._refuse(pid, ms, ["approval hash mismatch"],
                                "Refused: the Guardian's approval did not match this transaction.", base=result)
        result["tx_hash"] = expected
        if not self.broadcast:
            out = self._record(pid, result, ms, "simulated")
            return dict(out, note="simulated, not queued: a dry run writes no approval onchain")
        if not is_real_tx(result["report_tx"]):
            return self._refuse(pid, ms, ["the approval was not written onchain"],
                                "Refused: the approval was not written onchain.", base=result)
        if result["expires_at"] <= now:
            return self._refuse(pid, ms, ["the approval had already expired"],
                                "Refused: the approval had already expired.", base=result)
        result["report_tx"] = result["report_tx"].lower()
        return self._record(pid, result, ms, "waiting_key")

    def _record(self, pid, result, ms, status) -> dict:
        self.store.record_verdict(pid, result, ms, status)
        return dict(result, status=status, queued=status == "waiting_key")

    def _refuse(self, pid, ms, reasons, explanation, base=None, unavailable=False) -> dict:
        """A refusal decided here. The workflow's own tx_hash/report_tx are kept for the record: an approve report
        may already be onchain for that hash, but nothing here will ever queue it."""
        base = base or {}
        tx_hash = base.get("tx_hash") if isinstance(base.get("tx_hash"), str) else ""
        report_tx = base.get("report_tx") if isinstance(base.get("report_tx"), str) else ""
        result = {"proposal_id": pid, "verdict": "refuse", "risk": "high", "auto_eligible": False,
                  "explanation": explanation, "reasons": reasons, "tx_hash": tx_hash, "expires_at": 0,
                  "changes": [], "report_tx": report_tx, "usd_out": None}
        out = self._record(pid, result, ms, "refused")
        return dict(out, unavailable=True) if unavailable else out

    # ── queue ──────────────────────────────────────────────────────────────────────────────────────────────────
    def pending(self) -> list[dict]:
        return self.store.pending(self.clock())

    def executed(self, pid: str, tx_hash: str | None = None, error: str | None = None) -> bool:
        """The deck's key reports back. True if recorded (or the same tx hash was already recorded); False if the
        proposal isn't waiting for the key."""
        try:
            uuid.UUID(pid)
        except (ValueError, TypeError, AttributeError) as exc:
            raise InvalidRequest("unknown proposal id") from exc
        if (tx_hash is None) == (error is None):
            raise InvalidRequest("send exactly one of tx_hash or error")
        if tx_hash is not None:
            if not isinstance(tx_hash, str) or not HASH.match(tx_hash):
                raise InvalidRequest("tx_hash must be a 32-byte 0x hash")
            return self.store.mark_executed(pid, tx_hash.lower())
        if not isinstance(error, str) or not error.strip():
            raise InvalidRequest("error must be a non-empty string")
        return self.store.mark_failed(pid, _clean_text(error, 500))

    # ── freeze ─────────────────────────────────────────────────────────────────────────────────────────────────
    def freeze(self, reason: str) -> dict:
        reason = _clean_text(reason if isinstance(reason, str) else "", 200) or "panic"
        t0 = time.monotonic()
        try:
            result, ms = self.simulate({"reason": reason}, 1, self.broadcast)
        except Exception as exc:  # noqa: BLE001
            ms = int((time.monotonic() - t0) * 1000)
            log.warning("freeze: simulation failed (%s)", exc.kind if isinstance(exc, SimulationError) else "runner error")
            self.store.record_call("freeze", "error", "guardian unavailable", ms)
            return {"ok": False, "report_tx": "", "simulated": not self.broadcast, "unavailable": True}
        report_tx = result.get("report_tx") if isinstance(result, dict) and isinstance(result.get("report_tx"), str) else ""
        ok = isinstance(result, dict) and result.get("ok") is True and self.broadcast and is_real_tx(report_tx)
        verdict = "frozen" if ok else ("simulated" if not self.broadcast else "failed")
        self.store.record_call("freeze", verdict, reason, ms)
        return {"ok": ok, "report_tx": report_tx, "simulated": not self.broadcast}
