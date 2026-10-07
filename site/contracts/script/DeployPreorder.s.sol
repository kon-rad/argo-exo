// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console2} from "forge-std/Script.sol";
import {ExoPreorder} from "../src/ExoPreorder.sol";

/// @notice Deploys ExoPreorder with every tier closed (all prices 0). Opening the sale is a separate owner action.
/// @dev Never broadcast without Konrad's go-ahead at that moment. No RPC URL or key lives here: pass them on the
///      command line (`--rpc-url "$RPC_URL" --account <keystore>`). EXO_CONFIRM_CHAIN_ID must equal the target
///      chain's id, so a script pointed at the wrong network stops before deploying anything.
///      Env: BASE_USDC, EXO_TREASURY, EXO_PRESALE_OWNER, EXO_MAX_SUPPLY, EXO_CONFIRM_CHAIN_ID.
///      EXO_PRESALE_OWNER becomes owner directly in the constructor (no hand-off step). After deploy the owner sets
///      tier names, the description and, when it is time, prices from their own wallet (`cast send` or Basescan
///      "Write contract").
contract DeployPreorder is Script {
    /// Native USDC on Base mainnet (8453). On that chain BASE_USDC must be exactly this.
    address constant BASE_MAINNET_USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;

    function run() external returns (ExoPreorder p) {
        require(vm.envUint("EXO_CONFIRM_CHAIN_ID") == block.chainid, "EXO_CONFIRM_CHAIN_ID != target chain");
        address usdc = vm.envAddress("BASE_USDC");
        address treasury = vm.envAddress("EXO_TREASURY");
        address owner_ = vm.envAddress("EXO_PRESALE_OWNER");
        uint256 maxSupply = vm.envUint("EXO_MAX_SUPPLY");
        require(usdc != address(0) && treasury != address(0) && owner_ != address(0), "zero usdc/treasury/owner");
        require(maxSupply > 0, "EXO_MAX_SUPPLY must be > 0");
        if (block.chainid == 8453) require(usdc == BASE_MAINNET_USDC, "BASE_USDC is not Base native USDC");
        require(usdc.code.length > 0, "BASE_USDC has no code on this chain");

        vm.startBroadcast();
        p = new ExoPreorder(usdc, treasury, owner_, maxSupply);
        vm.stopBroadcast();
        require(p.owner() == owner_ && p.treasury() == treasury && address(p.usdc()) == usdc, "wiring mismatch");
        console2.log("ExoPreorder", address(p));
    }
}
