from eth_abi import encode
from eth_utils import keccak, to_checksum_address


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
                         [chain_id, to_checksum_address(module), to_checksum_address(to), value, keccak(bytes(data)), bytes(salt)]))
