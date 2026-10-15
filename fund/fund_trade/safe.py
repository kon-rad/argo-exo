"""Safe v1.4.1 primitives: creation calldata, SafeTx hashing, signatures, MultiSend batches, execTransaction.

Pure functions only (no RPC). Addresses are the canonical v1.4.1 deployments, the same on every chain; checked on
Base against safe-global/safe-deployments and eth_getCode on 2026-10-14."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from eth_abi import encode
from eth_account import Account
from eth_utils import keccak, to_checksum_address

PROXY_FACTORY = "0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67"
SAFE_L2_SINGLETON = "0x29fcB43b46531BcA003ddC8FCB67FFE91900C762"
FALLBACK_HANDLER = "0xfd0732Dc9E303f09fCEf3a7388Ad10A83459Ec99"
MULTI_SEND_CALL_ONLY = "0x9641d764fc13c8B624c04430C7356C1C7C8102e2"
ZERO = "0x" + "00" * 20

DOMAIN_TYPEHASH = keccak(text="EIP712Domain(uint256 chainId,address verifyingContract)")
SAFE_TX_TYPEHASH = keccak(text="SafeTx(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,"
                               "uint256 baseGas,uint256 gasPrice,address gasToken,address refundReceiver,uint256 nonce)")
CALL, DELEGATECALL = 0, 1


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def _bytes(h: str) -> bytes:
    return bytes.fromhex(h[2:] if h.startswith("0x") else h)


@dataclass(frozen=True)
class SafeTx:
    """A Safe transaction. gas/refund fields stay 0: the submitter (the agent) pays gas itself."""
    to: str
    value: int
    data: str
    operation: int
    nonce: int
    safe_tx_gas: int = 0
    base_gas: int = 0
    gas_price: int = 0
    gas_token: str = ZERO
    refund_receiver: str = ZERO

    def to_json(self) -> dict:
        d = asdict(self)
        d["value"], d["nonce"] = str(self.value), str(self.nonce)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "SafeTx":
        return cls(to=to_checksum_address(d["to"]), value=int(d["value"]), data=d["data"], operation=int(d["operation"]),
                   nonce=int(d["nonce"]), safe_tx_gas=int(d.get("safe_tx_gas", 0)), base_gas=int(d.get("base_gas", 0)),
                   gas_price=int(d.get("gas_price", 0)), gas_token=d.get("gas_token", ZERO),
                   refund_receiver=d.get("refund_receiver", ZERO))


def domain_separator(chain_id: int, safe: str) -> bytes:
    return keccak(encode(["bytes32", "uint256", "address"], [DOMAIN_TYPEHASH, chain_id, safe]))


def safe_tx_hash(chain_id: int, safe: str, tx: SafeTx) -> bytes:
    struct = keccak(encode(
        ["bytes32", "address", "uint256", "bytes32", "uint8", "uint256", "uint256", "uint256", "address", "address", "uint256"],
        [SAFE_TX_TYPEHASH, tx.to, tx.value, keccak(_bytes(tx.data)), tx.operation, tx.safe_tx_gas, tx.base_gas,
         tx.gas_price, tx.gas_token, tx.refund_receiver, tx.nonce]))
    return keccak(b"\x19\x01" + domain_separator(chain_id, safe) + struct)


def sign_hash(key: bytes | str, h: bytes) -> bytes:
    """An owner's ECDSA signature over the SafeTx hash itself (v = 27/28), the form Safe checks with ecrecover."""
    s = Account.unsafe_sign_hash(h, key)
    return s.r.to_bytes(32, "big") + s.s.to_bytes(32, "big") + bytes([s.v])


def recover(h: bytes, sig: bytes) -> str:
    if len(sig) != 65 or sig[64] not in (27, 28):
        raise ValueError("signature must be 65 bytes with v 27/28")
    v, r, s = sig[64], int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:64], "big")
    return Account._recover_hash(h, vrs=(v, r, s))


def pack_signatures(sigs: dict[str, bytes]) -> bytes:
    """Safe requires signatures ordered by owner address, ascending."""
    return b"".join(sigs[a] for a in sorted(sigs, key=lambda a: int(a, 16)))


def multisend(calls: list[dict]) -> str:
    """multiSend(bytes) calldata for MultiSendCallOnly: each call is op(0) ‖ to ‖ value ‖ len ‖ data, packed."""
    packed = b""
    for c in calls:
        data = _bytes(c["data"])
        packed += bytes([CALL]) + _bytes(to_checksum_address(c["to"])) + int(c["value"]).to_bytes(32, "big") \
            + len(data).to_bytes(32, "big") + data
    return _hex(selector("multiSend(bytes)") + encode(["bytes"], [packed]))


def batch(calls: list[dict], nonce: int) -> SafeTx:
    """One call goes out directly; several are batched through MultiSendCallOnly (delegatecall to it only)."""
    if not calls:
        raise ValueError("no calls")
    if len(calls) == 1:
        c = calls[0]
        return SafeTx(to=to_checksum_address(c["to"]), value=int(c["value"]), data=c["data"], operation=CALL, nonce=nonce)
    if any(int(c["value"]) for c in calls[:-1]):
        raise ValueError("only the last call of a batch may carry ETH")
    value = int(calls[-1]["value"])
    if value:
        raise ValueError("MultiSendCallOnly is delegatecalled: a batch cannot carry ETH; swap from ETH with one call")
    return SafeTx(to=MULTI_SEND_CALL_ONLY, value=0, data=multisend(calls), operation=DELEGATECALL, nonce=nonce)


def exec_calldata(tx: SafeTx, signatures: bytes) -> str:
    sig = "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)"
    return _hex(selector(sig) + encode(
        ["address", "uint256", "bytes", "uint8", "uint256", "uint256", "uint256", "address", "address", "bytes"],
        [tx.to, tx.value, _bytes(tx.data), tx.operation, tx.safe_tx_gas, tx.base_gas, tx.gas_price, tx.gas_token,
         tx.refund_receiver, signatures]))


def setup_calldata(owners: list[str], threshold: int) -> bytes:
    if not 1 <= threshold <= len(owners) or len({o.lower() for o in owners}) != len(owners):
        raise ValueError("owners must be distinct and 1 <= threshold <= len(owners)")
    sig = "setup(address[],uint256,address,bytes,address,address,uint256,address)"
    return selector(sig) + encode(
        ["address[]", "uint256", "address", "bytes", "address", "address", "uint256", "address"],
        [[to_checksum_address(o) for o in owners], threshold, ZERO, b"", FALLBACK_HANDLER, ZERO, 0, ZERO])


def create_calldata(owners: list[str], threshold: int, salt_nonce: int) -> str:
    return _hex(selector("createProxyWithNonce(address,bytes,uint256)") + encode(
        ["address", "bytes", "uint256"], [SAFE_L2_SINGLETON, setup_calldata(owners, threshold), salt_nonce]))


def erc20_transfer(to: str, amount: int) -> str:
    return _hex(selector("transfer(address,uint256)") + encode(["address", "uint256"], [to, amount]))


def erc20_approve(spender: str, amount: int) -> str:
    return _hex(selector("approve(address,uint256)") + encode(["address", "uint256"], [spender, amount]))
