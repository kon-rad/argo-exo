"""Shipping claims: the receipt's current holder signs (EIP-191 personal_sign) a hash of their shipping details."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct

FUTURE_SKEW_S = 60


class ClaimError(ValueError):
    """Messages are fixed strings, safe to return to the client; they never contain the submitted values."""


def details_hash(name: str, email: str, country: str) -> str:
    return hashlib.sha256(f"{name}\n{email}\n{country}".encode()).hexdigest()


def claim_message(n: int, dhash: str, signed_at: int) -> str:
    return f"Argo Exo pre-order No. {n}\nShipping details: {dhash}\nSigned at: {signed_at}"


def verify_claim(body: dict, owner_of, now: int, max_age_s: int = 600) -> str:
    n, at = int(body["device_number"]), int(body["signed_at"])
    if now - at > max_age_s or at - now > FUTURE_SKEW_S:
        raise ClaimError("signature expired; sign again")
    msg = claim_message(n, details_hash(body["name"], body["email"], body["country"]), at)
    try:
        signer = Account.recover_message(encode_defunct(text=msg), signature=body["signature"])
    except Exception as exc:
        raise ClaimError("bad signature") from exc
    owner = owner_of(n)                          # read fresh onchain at claim time
    if owner is None:
        raise ClaimError("no receipt with that number")
    if signer.lower() != owner.lower():
        raise ClaimError("only the receipt's current holder can set its shipping details")
    return signer


class ClaimStore:
    def __init__(self, path):
        path = Path(path)
        os.close(os.open(path, os.O_RDWR | os.O_CREAT, 0o600))   # created 600 regardless of umask...
        os.chmod(path, 0o600)                                     # ...and tightened if it already existed looser
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE IF NOT EXISTS claims (device_number INTEGER PRIMARY KEY, owner TEXT NOT NULL,"
                          " name TEXT NOT NULL, email TEXT NOT NULL, country TEXT NOT NULL, signed_at INTEGER NOT NULL)")

    def save(self, n, owner, name, email, country, signed_at) -> bool:
        """False when a claim for n with an equal or newer signed_at already exists (replay or out-of-order)."""
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO claims VALUES (?,?,?,?,?,?) ON CONFLICT(device_number) DO UPDATE SET owner=excluded.owner,"
                " name=excluded.name, email=excluded.email, country=excluded.country, signed_at=excluded.signed_at"
                " WHERE excluded.signed_at > claims.signed_at", (n, owner, name, email, country, signed_at))
            return cur.rowcount > 0

    def all(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM claims ORDER BY device_number")]
