"""Argo Fund approval from the deck: the approve key on the Fund panel signs the top proposal as a Safe owner.

hooks/fund-approve runs `python -m exo_deck.fund_sign`. The flow:
  1. GET  /fund/proposals             → the top open proposal (the one the kiosk shows)
  2. POST /fund/proposals/<id>/prepare → a fresh quote as a Safe transaction + its hash
  3. recompute the SafeTx hash HERE from the returned fields, for EXO_FUND_SAFE on Base only; any mismatch → no signature
  4. sign the hash with the deck's hot key (an owner of the Safe)
  5. POST /fund/proposals/<id>/approve {signature} → the agent adds its signature and executes

The hash code mirrors fund/fund_trade/safe.py; both are pinned by fund/tests/vectors/safe-tx.json."""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

from eth_abi import encode
from eth_utils import keccak, to_checksum_address

log = logging.getLogger("hooks/fund-approve")

CHAIN_ID = 8453
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
DOMAIN_TYPEHASH = keccak(text="EIP712Domain(uint256 chainId,address verifyingContract)")
SAFE_TX_TYPEHASH = keccak(text="SafeTx(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,"
                               "uint256 baseGas,uint256 gasPrice,address gasToken,address refundReceiver,uint256 nonce)")


class FundError(RuntimeError):
    pass


def safe_tx_hash(chain_id: int, safe: str, t: dict) -> bytes:
    data = bytes.fromhex(t["data"].removeprefix("0x"))
    struct = keccak(encode(
        ["bytes32", "address", "uint256", "bytes32", "uint8", "uint256", "uint256", "uint256", "address", "address", "uint256"],
        [SAFE_TX_TYPEHASH, to_checksum_address(t["to"]), int(t["value"]), keccak(data), int(t["operation"]),
         int(t.get("safe_tx_gas", 0)), int(t.get("base_gas", 0)), int(t.get("gas_price", 0)),
         to_checksum_address(t.get("gas_token", "0x" + "00" * 20)), to_checksum_address(t.get("refund_receiver", "0x" + "00" * 20)),
         int(t["nonce"])]))
    domain = keccak(encode(["bytes32", "uint256", "address"], [DOMAIN_TYPEHASH, chain_id, to_checksum_address(safe)]))
    return keccak(b"\x19\x01" + domain + struct)


TRANSFER = "a9059cbb"          # transfer(address,uint256)
APPROVE = "095ea7b3"           # approve(address,uint256)


def check_destination(prep: dict, savings: str | None) -> None:
    """A plain value transfer out of the Safe may only go to the savings vault (EXO_FUND_SAVINGS, set on the deck,
    so a hacked server can't change it). A proposal labelled "withdraw" must be exactly such a transfer. A lone
    approve is never signed (swaps batch approve + swap through MultiSendCallOnly)."""
    t = prep["safe_tx"]
    data = str(t.get("data", "0x")).removeprefix("0x").lower()
    to = str(t.get("to", "")).lower()
    single = int(t.get("operation", 0)) == 0
    recipient = None
    if single and data == "" and int(t.get("value", 0)) > 0:
        recipient = to                                         # ETH send
    elif single and data.startswith(TRANSFER) and len(data) == 8 + 128:
        recipient = "0x" + data[8 + 24:8 + 64]                 # ERC-20 transfer: the address inside the calldata
    elif single and data.startswith(APPROVE):
        raise FundError("a lone token approval is never signed")
    if recipient is not None:
        if not savings or recipient != savings.lower():
            raise FundError("a transfer out of the fund may only go to the savings vault")
    elif prep.get("kind") == "withdraw":
        raise FundError("a withdrawal must be a single transfer to the savings vault")


def check_prepared(prep: dict, safe: str, savings: str | None = None) -> bytes:
    """The digest to sign, recomputed locally. Refuses another chain, another Safe, a gas refund, a bad hash, or a
    transfer to anywhere but the savings vault."""
    if not isinstance(prep, dict) or not isinstance(prep.get("safe_tx"), dict):
        raise FundError("malformed prepare reply")
    if prep.get("chain_id") != CHAIN_ID or str(prep.get("safe", "")).lower() != safe.lower():
        raise FundError("prepared for another chain or Safe")
    t = prep["safe_tx"]
    if any(int(t.get(k, 0)) for k in ("safe_tx_gas", "base_gas", "gas_price")):
        raise FundError("a gas refund is never part of a fund trade")
    h = safe_tx_hash(CHAIN_ID, safe, t)
    if "0x" + h.hex() != str(prep.get("safe_tx_hash", "")).lower():
        raise FundError("hash mismatch")
    check_destination(prep, savings)
    return h


def approve_top(bridge, signer, safe: str, speak, savings: str | None = None) -> str | None:
    props = [p for p in bridge.fund_proposals() if p.get("status") in ("proposed", "prepared")]
    if not props:
        speak("No fund proposals waiting.")
        return None
    pid = props[0]["id"]
    prep = bridge.fund_prepare(pid)
    h = check_prepared(prep, safe, savings)
    sig = signer.sign_hash(h)
    out = bridge.fund_approve(pid, "0x" + sig.hex())
    headline = out.get("headline") if isinstance(out, dict) else None
    speak(f"Done. {headline}." if headline else "Trade sent.")
    return out.get("tx_hash") if isinstance(out, dict) else None


def main(argv: list[str]) -> int:
    from .approve_hook import _speak
    from .bridge_client import Bridge, BridgeError
    from .config import Settings
    from .signer import HotSigner, SignerLocked

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    safe = os.environ.get("EXO_FUND_SAFE", "")
    if not ADDRESS.match(safe):
        log.error("EXO_FUND_SAFE is not set; nothing signed")
        _speak("The fund's Safe address isn't configured on the deck.")
        return 2
    try:
        signer = HotSigner(Path(os.environ.get("EXO_HOT_KEYSTORE", "/srv/deck/keys/hot.json")),
                           Path(os.environ.get("EXO_HOT_PASSFILE", "/run/exo/hot.pass")))
    except SignerLocked:
        _speak("The hot key is locked. Run exo-unlock, then press approve again.")
        return 1
    s = Settings.from_env()
    try:
        savings = os.environ.get("EXO_FUND_SAVINGS", "")
        savings = savings if ADDRESS.match(savings) else None    # unset: the deck signs swaps but no withdrawals
        return 0 if approve_top(Bridge(s.bridge_url, s.bridge_token), signer, safe, _speak, savings) else 1
    except FundError as exc:
        log.error("refused to sign: %s", exc)
        _speak("I didn't sign it: the prepared trade didn't check out.")
    except BridgeError as exc:
        log.error("bridge: %s", exc)
        _speak("The trade didn't go through. Check the Fund panel.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
