from eth_abi import encode
from eth_utils import keccak, to_checksum_address


def approval_hash(chain_id: int, module: str, to: str, value: int, data: bytes, salt: bytes) -> bytes:
    return keccak(encode(["uint256", "address", "address", "uint256", "bytes32", "bytes32"],
                         [chain_id, to_checksum_address(module), to_checksum_address(to), value, keccak(data), salt]))
