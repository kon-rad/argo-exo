"""ExoModule from the deck: the approval hash, calldata, the three views, and strict queue-item parsing.

The approval hash is the same definition as chain/runner/exo_guardian/hashing.py (which is not deployed to the Pi),
pinned by the shared vector chain/test-vectors/approval-hash.json (deck/tests/test_exo_module.py)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from eth_abi import encode
from eth_utils import keccak, to_checksum_address

EXECUTE = keccak(text="execute(address,uint256,bytes,bytes32)")[:4]   # 0x88aa4c12
FREEZE = keccak(text="freeze()")[:4]                                  # 0x62a5af3b
APPROVED_UNTIL = keccak(text="approvedUntil(bytes32)")[:4]            # 0xbfc3b08f
USED = keccak(text="used(bytes32)")[:4]                               # 0xb07c411f
FROZEN = keccak(text="frozen()")[:4]                                  # 0x054f7d9c

CHAIN_IDS = {"ethereum": 1}           # ExoModule lives on Ethereum mainnet only (00-architecture §4.5)
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
HEX = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
WORD = re.compile(r"^0x[0-9a-fA-F]{64}$")
DIGITS = re.compile(r"^[0-9]{1,78}$")


class ItemError(ValueError):
    """A queue item that must never be signed."""


@dataclass(frozen=True)
class Call:
    to: str
    value: int
    data: bytes
    salt: bytes


@dataclass(frozen=True)
class ModuleState:
    approved_until: int
    used: bool
    frozen: bool


def approval_hash(chain_id: int, module: str, to: str, value: int, data: bytes, salt: bytes) -> bytes:
    for name, n in (("chain_id", chain_id), ("value", value)):
        if isinstance(n, bool) or not isinstance(n, int):
            raise TypeError(f"{name} must be an int")
        if n < 0:
            raise ValueError(f"{name} must be non-negative")
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")
    if not isinstance(salt, (bytes, bytearray)) or len(salt) != 32:
        raise ValueError("salt must be exactly 32 bytes")
    return keccak(encode(["uint256", "address", "address", "uint256", "bytes32", "bytes32"],
                         [chain_id, to_checksum_address(module), to_checksum_address(to), value, keccak(bytes(data)),
                          bytes(salt)]))


def execute_calldata(to: str, value: int, data: bytes, salt: bytes) -> bytes:
    return EXECUTE + encode(["address", "uint256", "bytes", "bytes32"], [to_checksum_address(to), value, data, salt])


def freeze_calldata() -> bytes:
    return FREEZE


def parse_item(item: dict, module: str) -> Call:
    """The fields execute() needs, validated strictly. Anything odd is an ItemError (never signed)."""
    if not isinstance(item, dict):
        raise ItemError("not an object")
    pid = item.get("id")
    if not isinstance(pid, str) or not pid:
        raise ItemError("id")
    if item.get("chain") not in CHAIN_IDS:
        raise ItemError("chain")
    to, value, data, salt = item.get("to"), item.get("value"), item.get("data"), item.get("salt")
    if not isinstance(to, str) or not ADDRESS.match(to):
        raise ItemError("to")
    if to.lower() == module.lower():
        raise ItemError("self call")              # the module reverts SelfCall; never spend gas on it
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not DIGITS.match(value) or int(value) >= 2**256:
        raise ItemError("value")
    if not isinstance(data, str) or not HEX.match(data):
        raise ItemError("data")
    if not isinstance(salt, str) or not WORD.match(salt):
        raise ItemError("salt")
    return Call(to, int(value), bytes.fromhex(data[2:]), bytes.fromhex(salt[2:]))


def _word(result) -> int:
    if not isinstance(result, str) or not WORD.match(result):
        raise ValueError("malformed eth_call result")
    return int(result, 16)


def _bool(result) -> bool:
    n = _word(result)
    if n not in (0, 1):
        raise ValueError("malformed bool")
    return n == 1


def read_state(rpc, module: str, h: bytes) -> ModuleState:
    """approvedUntil(h), used(h), frozen() in one batch, at the latest block. Odd replies raise (fail closed)."""
    def call(data: bytes):
        return ("eth_call", [{"to": module, "data": "0x" + data.hex()}, "latest"])

    until, used, frozen = rpc.batch([call(APPROVED_UNTIL + h), call(USED + h), call(FROZEN)])
    return ModuleState(_word(until), _bool(used), _bool(frozen))


def chain_id(rpc) -> int:
    r = rpc.call("eth_chainId", [])
    if not isinstance(r, str) or not re.match(r"^0x[0-9a-fA-F]{1,16}$", r):
        raise ValueError("malformed chain id")
    return int(r, 16)
