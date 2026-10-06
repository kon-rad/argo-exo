// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console2} from "forge-std/Script.sol";
import {ExoModule} from "../src/ExoModule.sol";

/// @notice Deploys ExoModule, sets the simulation reporter, then hands ownership to EXO_OWNER.
/// @dev Never broadcast without Konrad's go-ahead at that moment. No RPC URL or key lives here: pass them on the
///      command line (`--rpc-url "$RPC_URL" --account <keystore>`). EXO_CONFIRM_CHAIN_ID must equal the target
///      chain's id, so a script pointed at the wrong network stops before deploying anything.
///      Env: CRE_FORWARDER, EXO_SAFE, EXO_DECK, EXO_OWNER, EXO_MAX_NATIVE_PER_TX, EXO_MAX_NATIVE_PER_DAY,
///      CRE_SIMULATOR, EXO_CONFIRM_CHAIN_ID.
///      CRE_SIMULATOR must be non-zero: it is the tx.origin every report must come from while reports go through
///      the permissionless MockKeystoneForwarder. With it zero and no workflow identity set, ExoModule would refuse
///      every report anyway (NoReportAuth). Moving to a real DON is an owner action after deploy, in the exact
///      order of the checklist in chain/README.md ("Moving ExoModule from simulation to a DON"):
///      1. setForwarderAddress(real KeystoneForwarder for the chain, from Chainlink's docs, not the mock);
///      2. setExpectedWorkflowId and/or setExpectedAuthor;
///      3. only then setDemoReporter(address(0)).
///      A workflow identity behind the mock forwarder is forgeable by anyone. Never clear demoReporter while the
///      mock is set, and never call setForwarderAddress(address(0)).
contract DeployExoModule is Script {
    function run() external returns (ExoModule m) {
        require(vm.envUint("EXO_CONFIRM_CHAIN_ID") == block.chainid, "EXO_CONFIRM_CHAIN_ID != target chain");
        address owner_ = vm.envAddress("EXO_OWNER");
        address safe_ = vm.envAddress("EXO_SAFE");
        address deck_ = vm.envAddress("EXO_DECK");
        address simulator = vm.envAddress("CRE_SIMULATOR");
        require(owner_ != address(0) && safe_ != address(0) && deck_ != address(0), "zero owner/safe/deck");
        require(simulator != address(0), "CRE_SIMULATOR must be set");

        vm.startBroadcast();
        (, address deployer,) = vm.readCallers();      // the broadcasting key, owner until the hand-off below
        m = new ExoModule(vm.envAddress("CRE_FORWARDER"), safe_, deck_, deployer,
            vm.envUint("EXO_MAX_NATIVE_PER_TX"), vm.envUint("EXO_MAX_NATIVE_PER_DAY"));
        m.setDemoReporter(simulator);
        m.transferOwnership(owner_);
        vm.stopBroadcast();
        console2.log("ExoModule", address(m));
    }
}
