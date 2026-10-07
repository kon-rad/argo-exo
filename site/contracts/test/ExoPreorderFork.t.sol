// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ExoPreorder} from "../src/ExoPreorder.sol";

interface IFiatToken {
    function DOMAIN_SEPARATOR() external view returns (bytes32);
    function nonces(address) external view returns (uint256);
    function name() external view returns (string memory);
    function version() external view returns (string memory);
    function permit(address, address, uint256, uint256, uint8, bytes32, bytes32) external;
}

/// Against real Base USDC (FiatToken) on a fork. Skipped unless BASE_RPC_URL is set, so CI needs no network:
///   BASE_RPC_URL=<base mainnet rpc> forge test --match-contract Fork -vv
contract ExoPreorderForkTest is Test {
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    bytes32 constant OBSERVED_DOMAIN = 0x02fa7265e7c5d81118673727957699e4d68f74cd74b7db77da710fe8a2c7834f;
    uint256 constant PRICE = 499e6;
    address treasury = address(0x7E);
    // a fresh key: the unit tests' 0xB0B address has code on Base (an EIP-7702 delegation), which routes USDC
    // permit through ERC-1271 instead of ecrecover
    uint256 buyerKey = uint256(keccak256("argo exo fork-test buyer"));
    address buyer;
    ExoPreorder p;
    bool live;

    function setUp() public {
        string memory rpc = vm.envOr("BASE_RPC_URL", string(""));
        if (bytes(rpc).length == 0) return;
        vm.createSelectFork(rpc);
        live = true;
        buyer = vm.addr(buyerKey);
        require(buyer.code.length == 0, "fork buyer has code");
        p = new ExoPreorder(USDC, treasury, address(0x0C), 10);
        vm.prank(address(0x0C));
        p.setPrice(1, PRICE);
        deal(USDC, buyer, 1_000e6);
    }

    function _sig(uint256 value, uint256 deadline) internal view returns (uint8 v, bytes32 r, bytes32 s) {
        IFiatToken t = IFiatToken(USDC);
        bytes32 h = keccak256(abi.encode(
            keccak256("Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)"),
            buyer, address(p), value, t.nonces(buyer), deadline));
        (v, r, s) = vm.sign(buyerKey, keccak256(abi.encodePacked("\x19\x01", t.DOMAIN_SEPARATOR(), h)));
    }

    function test_fork_real_usdc_permit_buy_and_frontrun() public {
        if (!live) { vm.skip(true); return; }
        assertEq(block.chainid, 8453);
        assertEq(IFiatToken(USDC).DOMAIN_SEPARATOR(), OBSERVED_DOMAIN);
        assertEq(IFiatToken(USDC).name(), "USD Coin");
        assertEq(IFiatToken(USDC).version(), "2");
        uint256 t0 = IERC20(USDC).balanceOf(treasury);
        uint256 deadline = block.timestamp + 1 hours;

        (uint8 v, bytes32 r, bytes32 s) = _sig(PRICE, deadline);
        vm.prank(buyer);
        assertEq(p.preorderWithPermit(1, PRICE, deadline, v, r, s), 1);

        (v, r, s) = _sig(PRICE, deadline);
        IFiatToken(USDC).permit(buyer, address(p), PRICE, deadline, v, r, s);   // front-run
        vm.prank(buyer);
        assertEq(p.preorderWithPermit(1, PRICE, deadline, v, r, s), 2);

        assertEq(IERC20(USDC).balanceOf(treasury) - t0, 2 * PRICE);
        assertEq(IERC20(USDC).balanceOf(buyer), 1_000e6 - 2 * PRICE);
        assertEq(IERC20(USDC).balanceOf(address(p)), 0);
    }
}
