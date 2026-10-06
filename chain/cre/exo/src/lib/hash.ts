import { encodeAbiParameters, keccak256, parseAbiParameters } from "viem";
import type { Hex } from "./types";

/** keccak256(abi.encode(chainid, module, to, value, keccak256(data), salt)), the one definition shared with
 *  ExoModule.sol and the Python helper; pinned by chain/test-vectors/approval-hash.json. A salt that is not exactly
 *  32 bytes throws (viem's bytes32 size check) rather than being padded. */
export const approvalHash = (chainId: bigint, module: Hex, to: Hex, value: bigint, data: Hex, salt: Hex): Hex =>
  keccak256(encodeAbiParameters(parseAbiParameters("uint256, address, address, uint256, bytes32, bytes32"),
    [chainId, module, to, value, keccak256(data), salt]));
