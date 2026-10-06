// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {DeployExoModule} from "../script/DeployExoModule.s.sol";
import {ExoModule} from "../src/ExoModule.sol";

/// In-memory run of the deploy script (no RPC, nothing broadcast): checks the wiring and the hand-off.
/// One test only: vm.setEnv is process-global and forge runs tests in parallel, so separate tests would race.
contract DeployExoModuleTest is Test {
    function _env(uint256 confirmChainId) internal {
        vm.setEnv("CRE_FORWARDER", vm.toString(address(0xF0)));
        vm.setEnv("EXO_SAFE", vm.toString(address(0x5AFE)));
        vm.setEnv("EXO_DECK", vm.toString(address(0xDE)));
        vm.setEnv("EXO_OWNER", vm.toString(address(0x0C)));
        vm.setEnv("CRE_SIMULATOR", vm.toString(address(0x5E)));
        vm.setEnv("EXO_MAX_NATIVE_PER_TX", "1000000000000000");
        vm.setEnv("EXO_MAX_NATIVE_PER_DAY", "5000000000000000");
        vm.setEnv("EXO_CONFIRM_CHAIN_ID", vm.toString(confirmChainId));
    }

    function test_deploy_script_wires_hands_off_and_refuses_bad_env() public {
        _env(block.chainid);
        ExoModule m = new DeployExoModule().run();
        assertEq(m.owner(), address(0x0C));
        assertEq(address(m.safe()), address(0x5AFE));
        assertEq(m.deck(), address(0xDE));
        assertEq(m.getForwarderAddress(), address(0xF0));
        assertEq(m.demoReporter(), address(0x5E));
        assertEq(m.maxNativePerTx(), 0.001 ether);
        assertEq(m.maxNativePerDay(), 0.005 ether);
        assertFalse(m.frozen());

        // pointed at the wrong chain, it stops before deploying anything
        _env(block.chainid + 1);
        DeployExoModule s = new DeployExoModule();
        vm.expectRevert(bytes("EXO_CONFIRM_CHAIN_ID != target chain"));
        s.run();

        // a zero simulator is refused rather than deploying a module that rejects every report
        _env(block.chainid);
        vm.setEnv("CRE_SIMULATOR", vm.toString(address(0)));
        vm.expectRevert(bytes("CRE_SIMULATOR must be set"));
        s.run();
    }
}
