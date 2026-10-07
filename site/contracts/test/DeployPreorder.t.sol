// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {DeployPreorder} from "../script/DeployPreorder.s.sol";
import {ExoPreorder} from "../src/ExoPreorder.sol";
import {MockUSDC} from "./MockUSDC.sol";

/// In-memory run of the deploy script (no RPC, nothing broadcast): checks the wiring and every refusal.
/// One test only: vm.setEnv is process-global and forge runs tests in parallel, so separate tests would race.
contract DeployPreorderTest is Test {
    function _env(address usdc, uint256 confirmChainId) internal {
        vm.setEnv("BASE_USDC", vm.toString(usdc));
        vm.setEnv("EXO_TREASURY", vm.toString(address(0x7E)));
        vm.setEnv("EXO_PRESALE_OWNER", vm.toString(address(0x0C)));
        vm.setEnv("EXO_MAX_SUPPLY", "250");
        vm.setEnv("EXO_CONFIRM_CHAIN_ID", vm.toString(confirmChainId));
    }

    function test_deploy_script_wires_and_refuses_bad_env() public {
        address usdc = address(new MockUSDC());
        _env(usdc, block.chainid);
        ExoPreorder p = new DeployPreorder().run();
        assertEq(p.owner(), address(0x0C));
        assertEq(p.treasury(), address(0x7E));
        assertEq(address(p.usdc()), usdc);
        assertEq(p.maxSupply(), 250);
        assertEq(p.totalMinted(), 0);
        for (uint256 t = 0; t < 256; t++) assertEq(p.price(uint8(t)), 0);   // every tier starts closed
        assertFalse(p.paused());

        DeployPreorder s = new DeployPreorder();

        _env(usdc, block.chainid + 1);
        vm.expectRevert(bytes("EXO_CONFIRM_CHAIN_ID != target chain"));
        s.run();

        _env(usdc, block.chainid);
        vm.setEnv("EXO_TREASURY", vm.toString(address(0)));
        vm.expectRevert(bytes("zero usdc/treasury/owner"));
        s.run();

        _env(usdc, block.chainid);
        vm.setEnv("EXO_PRESALE_OWNER", vm.toString(address(0)));
        vm.expectRevert(bytes("zero usdc/treasury/owner"));
        s.run();

        _env(address(0), block.chainid);
        vm.expectRevert(bytes("zero usdc/treasury/owner"));
        s.run();

        _env(usdc, block.chainid);
        vm.setEnv("EXO_MAX_SUPPLY", "0");
        vm.expectRevert(bytes("EXO_MAX_SUPPLY must be > 0"));
        s.run();

        _env(address(0xC0DE1E55), block.chainid);
        vm.expectRevert(bytes("BASE_USDC has no code on this chain"));
        s.run();

        // on Base mainnet, anything but native USDC is refused
        vm.chainId(8453);
        _env(usdc, 8453);
        vm.expectRevert(bytes("BASE_USDC is not Base native USDC"));
        s.run();
    }
}
