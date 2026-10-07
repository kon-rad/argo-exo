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
# The freeze bids 2x the normal tip and maxFee so it replaces a same-nonce execute in flight; it may go over the
# normal ceiling, up to this one (the hot key holds gas only), and past it is clamped to it, never refused.
FREEZE_MAX_FEE_WEI = max(MAX_FEE_WEI, int(os.environ.get("EXO_FREEZE_MAX_FEE_GWEI", "1500")) * 10**9)
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


def build_tx(rpc, frm: str, to: str, data: bytes, value: int = 0, bid: int = 1,
             max_fee_wei: int | None = None, nonce_tag: str = "pending", clamp_fee: bool = False) -> dict:
    """An EIP-1559 tx: maxFee = 2×base + tip, gas = estimate × 1.2. All ints (wei). `bid` multiplies both tip and
    maxFee (the freeze uses 2). The nonce comes from `nonce_tag`: "pending" (the next free one: approvals) or
    "latest" (the freeze: the nonce of the oldest unmined tx, so it REPLACES an execute still in the mempool rather
    than queueing behind it). Over the fee ceiling the tx is refused (FeeTooHigh), or with `clamp_fee` priced at the
    ceiling instead (the panic button is never refused for price). The estimate runs as the hot key, so a revert
    (NotApproved, Expired, Frozen, caps…) stops here, unsigned."""
    if isinstance(bid, bool) or not isinstance(bid, int) or bid < 1:
        raise ValueError("bid must be an int >= 1")
    if nonce_tag not in ("pending", "latest"):
        raise ValueError("nonce_tag must be pending or latest")
    ceiling = MAX_FEE_WEI if max_fee_wei is None else max_fee_wei
    tip = _qty(rpc.call("eth_maxPriorityFeePerGas", []))
    block = rpc.call("eth_getBlockByNumber", ["latest", False])
    base = _qty(block.get("baseFeePerGas") if isinstance(block, dict) else None)
    to = to_checksum_address(to)
    tx = {"chainId": _qty(rpc.call("eth_chainId", [])), "type": 2, "to": to, "value": value, "data": data,
          "nonce": _qty(rpc.call("eth_getTransactionCount", [frm, nonce_tag])),
          "maxPriorityFeePerGas": bid * tip, "maxFeePerGas": bid * (2 * base + tip)}
    gas = _qty(rpc.call("eth_estimateGas", [{"from": frm, "to": to, "value": hex(value), "data": "0x" + data.hex()}]))
    tx["gas"] = gas * 12 // 10
    if tx["gas"] > MAX_GAS:
        raise FeeTooHigh("fee or gas over the deck's ceiling")
    if tx["maxFeePerGas"] > ceiling:
        if not clamp_fee:
            raise FeeTooHigh("fee or gas over the deck's ceiling")
        tx["maxFeePerGas"] = ceiling
        tx["maxPriorityFeePerGas"] = min(tx["maxPriorityFeePerGas"], ceiling)
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
