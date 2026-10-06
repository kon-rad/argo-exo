// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console2} from "forge-std/Script.sol";
import {ExoModule} from "../src/ExoModule.sol";

/// @notice Deploys ExoModule, sets the simulation reporter, then hands ownership to EXO_OWNER.
/// @dev Never broadcast without Konrad's go-ahead at that moment. No RPC URL or key lives here: pass them on the
///      command line (`--rpc-url "$RPC_URL" --account <keystore>`). EXO_CONFIRM_CHAIN_ID must equal the target
///      chain's id, so a script pointed at the wrong network stops before deploying anything.
///      Env: CRE_FORWARDER, EXO_SAFE, EXO_DECK, EXO_OWNER, EXO_MAX_NATIVE_PER_TX, EXO_MAX_NATIVE_PER_DAY,
///      CRE_SIMULATOR (address(0) disables the tx.origin check), EXO_CONFIRM_CHAIN_ID.
contract DeployExoModule is Script {
    function run() external returns (ExoModule m) {
        require(vm.envUint("EXO_CONFIRM_CHAIN_ID") == block.chainid, "EXO_CONFIRM_CHAIN_ID != target chain");
        address owner_ = vm.envAddress("EXO_OWNER");
        address safe_ = vm.envAddress("EXO_SAFE");
        address deck_ = vm.envAddress("EXO_DECK");
        require(owner_ != address(0) && safe_ != address(0) && deck_ != address(0), "zero owner/safe/deck");

        vm.startBroadcast();
        (, address deployer,) = vm.readCallers();      // the broadcasting key, owner until the hand-off below
        m = new ExoModule(vm.envAddress("CRE_FORWARDER"), safe_, deck_, deployer,
            vm.envUint("EXO_MAX_NATIVE_PER_TX"), vm.envUint("EXO_MAX_NATIVE_PER_DAY"));
        m.setDemoReporter(vm.envAddress("CRE_SIMULATOR"));
        m.transferOwnership(owner_);
        vm.stopBroadcast();
        console2.log("ExoModule", address(m));
    }
}
