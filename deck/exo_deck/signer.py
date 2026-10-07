"""The deck's hot key (gas only) and the transactions it signs.

The key is a keystore v3 file; the passphrase is a tmpfs file that bin/exo-unlock writes after boot (mode 0600).
Neither the passphrase nor the key is ever logged, put in argv, or included in an exception message."""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from eth_account import Account
from eth_utils import to_checksum_address

from .exo_module import execute_calldata, freeze_calldata   # noqa: F401  (re-exported: the brief's interface)

MAX_GAS = 2_000_000                     # ExoModule.execute of a token transfer through the Safe is ~100k
MAX_FEE_WEI = int(os.environ.get("EXO_MAX_FEE_GWEI", "300")) * 10**9
QTY = re.compile(r"^0x[0-9a-fA-F]{1,64}$")


class SignerLocked(RuntimeError):
    """The hot key can't be loaded: exo-unlock hasn't run, the passphrase is wrong, or its file is too open."""


class FeeTooHigh(RuntimeError):
    pass


class HotSigner:
    """The deck's gas-only key, decrypted from a keystore with the passphrase exo-unlock put in tmpfs."""

    def __init__(self, keystore: Path, passfile: Path):
        try:
            st = os.stat(passfile)
            if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise SignerLocked("passphrase file must be mode 0600")
            secret = Path(passfile).read_text().strip()
            key = Account.decrypt(json.loads(Path(keystore).read_text()), secret)
            acct = Account.from_key(key)
        except SignerLocked:
            raise
        except Exception:
            raise SignerLocked("hot key locked") from None   # no chained exception: nothing secret in a traceback
        finally:
            secret = key = None   # noqa: F841
        self._acct = acct
        self.address = acct.address

    def __repr__(self) -> str:
        return f"HotSigner({self.address})"

    def sign(self, tx: dict) -> bytes:
        return bytes(self._acct.sign_transaction(tx).raw_transaction)


def _qty(v) -> int:
    if not isinstance(v, str) or not QTY.match(v):
        raise ValueError("malformed quantity")
    return int(v, 16)


def build_tx(rpc, frm: str, to: str, data: bytes, value: int = 0) -> dict:
    """An EIP-1559 tx: nonce from the pending pool, maxFee = 2×base + tip, gas = estimate × 1.2. All ints (wei).
    The estimate runs as the hot key, so a revert (NotApproved, Expired, Frozen, caps…) stops here, unsigned."""
    tip = _qty(rpc.call("eth_maxPriorityFeePerGas", []))
    block = rpc.call("eth_getBlockByNumber", ["latest", False])
    base = _qty(block.get("baseFeePerGas") if isinstance(block, dict) else None)
    to = to_checksum_address(to)
    tx = {"chainId": _qty(rpc.call("eth_chainId", [])), "type": 2, "to": to, "value": value, "data": data,
          "nonce": _qty(rpc.call("eth_getTransactionCount", [frm, "pending"])),
          "maxPriorityFeePerGas": tip, "maxFeePerGas": 2 * base + tip}
    gas = _qty(rpc.call("eth_estimateGas", [{"from": frm, "to": to, "value": hex(value), "data": "0x" + data.hex()}]))
    tx["gas"] = gas * 12 // 10
    if tx["maxFeePerGas"] > MAX_FEE_WEI or tx["gas"] > MAX_GAS:
        raise FeeTooHigh("fee or gas over the deck's ceiling")
    return tx


@contextmanager
def key_lock(state: Path, wait_s: float | None = None):
    """One signer at a time per deck (state/.hot-key.lock): two hooks building txs together would pick the same
    pending nonce. wait_s=None waits as long as it takes; otherwise give up waiting after wait_s and go ahead
    unlocked (the freeze must not queue behind a slow approve). Yields True if the lock is held."""
    Path(state).mkdir(parents=True, exist_ok=True)
    fd = os.open(Path(state) / ".hot-key.lock", os.O_RDWR | os.O_CREAT, 0o600)
    held = False
    try:
        if wait_s is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
            held = True
        else:
            deadline = time.monotonic() + wait_s
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
        yield held
    finally:
        if held:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
