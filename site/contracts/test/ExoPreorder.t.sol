// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {IERC721Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";
import {IERC721Receiver} from "@openzeppelin/contracts/token/ERC721/IERC721Receiver.sol";
import {ExoPreorder} from "../src/ExoPreorder.sol";
import {MockUSDC} from "./MockUSDC.sol";

/// A buyer contract that buys again from inside onERC721Received (the only external callback in preorder).
contract ReentrantBuyer is IERC721Receiver {
    ExoPreorder immutable p;
    MockUSDC immutable usdc;
    uint256 public received;

    constructor(ExoPreorder p_, MockUSDC usdc_) { p = p_; usdc = usdc_; }

    function buy(uint256 price) external returns (uint256) {
        usdc.approve(address(p), type(uint256).max);
        return p.preorder(1, price);
    }

    function onERC721Received(address, address, uint256, bytes calldata) external returns (bytes4) {
        if (++received == 1) p.preorder(1, p.price(1));
        return IERC721Receiver.onERC721Received.selector;
    }
}

contract ExoPreorderTest is Test {
    event Preordered(uint256 indexed deviceNumber, address indexed buyer, uint8 tier, uint256 price);

    MockUSDC usdc;
    ExoPreorder p;
    address owner = address(0x0C);
    address treasury = address(0x7E);
    uint256 buyerKey = 0xB0B;
    address buyer;
    uint256 constant PRICE = 499e6;
    uint256 constant FUNDS = 10_000e6;

    function setUp() public {
        usdc = new MockUSDC();
        p = new ExoPreorder(address(usdc), treasury, owner, 3);
        buyer = vm.addr(buyerKey);
        usdc.mint(buyer, FUNDS);
        vm.startPrank(owner);
        p.setPrice(1, PRICE);
        p.setTierName(1, "The Apparatus");
        vm.stopPrank();
    }

    function _buy(address who) internal returns (uint256) {
        vm.startPrank(who);
        usdc.approve(address(p), PRICE);
        uint256 id = p.preorder(1, PRICE);
        vm.stopPrank();
        return id;
    }

    /// Nothing moved: buyer still has everything, treasury and the contract hold nothing.
    function _assertNoCharge() internal view {
        assertEq(usdc.balanceOf(buyer), FUNDS);
        assertEq(usdc.balanceOf(treasury), 0);
        assertEq(usdc.balanceOf(address(p)), 0);
        assertEq(p.totalMinted(), 0);
    }

    // ---- buying -------------------------------------------------------------

    function test_preorder_pays_treasury_and_mints_sequential_numbers() public {
        vm.startPrank(buyer);
        usdc.approve(address(p), PRICE);
        vm.expectEmit(true, true, false, true, address(p));   // expectEmit binds to the next call, so approve first
        emit Preordered(1, buyer, 1, PRICE);
        assertEq(p.preorder(1, PRICE), 1);
        vm.stopPrank();
        assertEq(usdc.balanceOf(treasury), PRICE);
        assertEq(usdc.balanceOf(buyer), FUNDS - PRICE);
        assertEq(usdc.balanceOf(address(p)), 0);
        assertEq(p.ownerOf(1), buyer);
        assertEq(p.paidOf(1), PRICE);
        assertEq(p.tierOf(1), 1);
        assertEq(uint8(p.statusOf(1)), uint8(ExoPreorder.Status.Preordered));
        assertEq(_buy(buyer), 2);
        assertEq(p.totalMinted(), 2);
        assertEq(usdc.balanceOf(treasury), 2 * PRICE);
        assertEq(usdc.balanceOf(address(p)), 0);
    }

    function test_not_for_sale() public {
        vm.startPrank(buyer);
        usdc.approve(address(p), type(uint256).max);
        vm.expectRevert(abi.encodeWithSelector(ExoPreorder.NotForSale.selector, uint8(2)));
        p.preorder(2, 1e12);
        vm.stopPrank();
        // closing a tier again (price 0) stops sales of it
        vm.prank(owner);
        p.setPrice(1, 0);
        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(ExoPreorder.NotForSale.selector, uint8(1)));
        p.preorder(1, 1e12);
        _assertNoCharge();
    }

    function test_max_price_protects_buyer() public {
        vm.prank(owner);
        p.setPrice(1, PRICE + 1);
        vm.startPrank(buyer);
        usdc.approve(address(p), type(uint256).max);       // even an unlimited allowance cannot be overcharged
        vm.expectRevert(abi.encodeWithSelector(ExoPreorder.PriceAboveMax.selector, PRICE + 1, PRICE));
        p.preorder(1, PRICE);
        vm.stopPrank();
        _assertNoCharge();
    }

    function test_max_price_above_price_charges_only_price() public {
        vm.startPrank(buyer);
        usdc.approve(address(p), 2 * PRICE);
        p.preorder(1, 2 * PRICE);
        vm.stopPrank();
        assertEq(usdc.balanceOf(treasury), PRICE);
        assertEq(usdc.balanceOf(buyer), FUNDS - PRICE);
        assertEq(p.paidOf(1), PRICE);
    }

    /// For any price and maxPrice: either exactly the price moves buyer -> treasury, or nothing moves at all.
    function testFuzz_charge_is_exactly_price_or_nothing(uint256 price, uint256 maxPrice) public {
        price = bound(price, 1, FUNDS);
        vm.prank(owner);
        p.setPrice(1, price);
        vm.startPrank(buyer);
        usdc.approve(address(p), type(uint256).max);
        if (price > maxPrice) {
            vm.expectRevert(abi.encodeWithSelector(ExoPreorder.PriceAboveMax.selector, price, maxPrice));
            p.preorder(1, maxPrice);
            vm.stopPrank();
            _assertNoCharge();
        } else {
            p.preorder(1, maxPrice);
            vm.stopPrank();
            assertEq(usdc.balanceOf(treasury), price);
            assertEq(usdc.balanceOf(buyer), FUNDS - price);
            assertEq(usdc.balanceOf(address(p)), 0);
            assertEq(p.paidOf(1), price);
        }
    }

    function test_insufficient_allowance_or_balance_reverts() public {
        vm.prank(buyer);
        vm.expectRevert();                                   // no allowance
        p.preorder(1, PRICE);
        address poor = address(0xB00);
        usdc.mint(poor, PRICE - 1);
        vm.startPrank(poor);
        usdc.approve(address(p), PRICE);
        vm.expectRevert();                                   // not enough USDC
        p.preorder(1, PRICE);
        vm.stopPrank();
        _assertNoCharge();
        assertEq(usdc.balanceOf(poor), PRICE - 1);
    }

    function test_sold_out() public {
        _buy(buyer); _buy(buyer); _buy(buyer);
        vm.startPrank(buyer);
        usdc.approve(address(p), PRICE);
        vm.expectRevert(ExoPreorder.SoldOut.selector);
        p.preorder(1, PRICE);
        vm.stopPrank();
        assertEq(usdc.balanceOf(treasury), 3 * PRICE);
        assertEq(usdc.balanceOf(buyer), FUNDS - 3 * PRICE);
        // a refund retires a number; it does not free a slot
        vm.prank(owner);
        p.markRefunded(3);
        vm.prank(buyer);
        vm.expectRevert(ExoPreorder.SoldOut.selector);
        p.preorder(1, PRICE);
    }

    function test_paused() public {
        vm.prank(owner);
        p.pause();
        assertTrue(p.paused());
        vm.startPrank(buyer);
        usdc.approve(address(p), PRICE);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        p.preorder(1, PRICE);
        (uint8 v, bytes32 r, bytes32 s) = _permitSig(PRICE, block.timestamp + 1 hours);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        p.preorderWithPermit(1, PRICE, block.timestamp + 1 hours, v, r, s);
        vm.stopPrank();
        _assertNoCharge();
        vm.prank(owner);
        p.unpause();
        assertEq(_buy(buyer), 1);
    }

    function test_treasury_change_redirects_payment() public {
        address t2 = address(0x7E2);
        vm.prank(owner);
        p.setTreasury(t2);
        _buy(buyer);
        assertEq(usdc.balanceOf(t2), PRICE);
        assertEq(usdc.balanceOf(treasury), 0);
        assertEq(usdc.balanceOf(address(p)), 0);
    }

    /// The only external callback is onERC721Received on a contract buyer. Re-entering from it buys another,
    /// paid, number: every minted number is backed by exactly its price at the treasury.
    function test_reentrant_buyer_pays_for_every_number() public {
        ReentrantBuyer rb = new ReentrantBuyer(p, usdc);
        usdc.mint(address(rb), 2 * PRICE);
        uint256 id = rb.buy(PRICE);
        assertEq(id, 1);
        assertEq(p.totalMinted(), 2);
        assertEq(p.ownerOf(1), address(rb));
        assertEq(p.ownerOf(2), address(rb));
        assertEq(usdc.balanceOf(treasury), 2 * PRICE);
        assertEq(usdc.balanceOf(address(rb)), 0);
        assertEq(usdc.balanceOf(address(p)), 0);
    }

    // ---- permit -------------------------------------------------------------

    function _permitSig(uint256 value, uint256 deadline) internal view returns (uint8 v, bytes32 r, bytes32 s) {
        bytes32 structHash = keccak256(abi.encode(
            keccak256("Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)"),
            buyer, address(p), value, usdc.nonces(buyer), deadline));
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", usdc.DOMAIN_SEPARATOR(), structHash));
        (v, r, s) = vm.sign(buyerKey, digest);
    }

    function test_preorder_with_permit_in_one_tx() public {
        (uint8 v, bytes32 r, bytes32 s) = _permitSig(PRICE, block.timestamp + 1 hours);
        vm.prank(buyer);
        assertEq(p.preorderWithPermit(1, PRICE, block.timestamp + 1 hours, v, r, s), 1);
        assertEq(usdc.balanceOf(treasury), PRICE);
        assertEq(usdc.balanceOf(address(p)), 0);
        assertEq(p.ownerOf(1), buyer);
        assertEq(usdc.allowance(buyer, address(p)), 0);
    }

    function test_permit_frontrun_still_buys() public {
        uint256 deadline = block.timestamp + 1 hours;
        (uint8 v, bytes32 r, bytes32 s) = _permitSig(PRICE, deadline);
        vm.prank(address(0xBAD));
        usdc.permit(buyer, address(p), PRICE, deadline, v, r, s);     // an attacker submits the permit first
        vm.prank(buyer);
        assertEq(p.preorderWithPermit(1, PRICE, deadline, v, r, s), 1);
        assertEq(p.ownerOf(1), buyer);
        assertEq(usdc.balanceOf(treasury), PRICE);
        assertEq(usdc.balanceOf(address(p)), 0);
    }

    /// A permit for someone else (or a bad signature) is ignored; without an allowance the buy reverts, unpaid.
    function test_bad_permit_without_allowance_reverts_without_charge() public {
        uint256 deadline = block.timestamp + 1 hours;
        (uint8 v, bytes32 r, bytes32 s) = _permitSig(PRICE, deadline);
        vm.prank(address(0xBAD));                             // the signature is the buyer's, not 0xBAD's
        vm.expectRevert();
        p.preorderWithPermit(1, PRICE, deadline, v, r, s);
        vm.prank(buyer);                                      // expired permit
        vm.warp(deadline + 1);
        vm.expectRevert();
        p.preorderWithPermit(1, PRICE, deadline, v, r, s);
        _assertNoCharge();
    }

    /// The permit covers maxPrice and so does the signature: a raised price still reverts, never overcharges.
    function test_permit_price_raised_reverts() public {
        uint256 deadline = block.timestamp + 1 hours;
        (uint8 v, bytes32 r, bytes32 s) = _permitSig(PRICE, deadline);
        vm.prank(owner);
        p.setPrice(1, PRICE + 1);
        vm.prank(buyer);
        vm.expectRevert(abi.encodeWithSelector(ExoPreorder.PriceAboveMax.selector, PRICE + 1, PRICE));
        p.preorderWithPermit(1, PRICE, deadline, v, r, s);
        _assertNoCharge();
    }

    // ---- metadata -----------------------------------------------------------

    function test_metadata_and_svg() public {
        _buy(buyer);
        string memory j = p.metadataJson(1);
        assertTrue(_contains(j, "Apparatus No. 0001"));
        assertTrue(_contains(j, '"trait_type":"Device number","value":1'));
        assertTrue(_contains(j, '"trait_type":"Paid (USDC)","value":"499.00"'));
        assertTrue(_contains(p.svg(1), "No. 0001"));
        assertTrue(_contains(p.svg(1), "The Apparatus"));
        assertTrue(_contains(p.tokenURI(1), "data:application/json;base64,"));
    }

    function test_tokenURI_is_valid_base64_json() public {
        vm.startPrank(owner);
        p.setDescription(unicode"Placeholder · café it's fine");
        p.setPrice(1, 1_234_567_891);                         // 1234.567891 USDC shows as 1234.56
        vm.stopPrank();
        vm.startPrank(buyer);
        usdc.approve(address(p), 1_234_567_891);
        p.preorder(1, 1_234_567_891);
        vm.stopPrank();
        vm.prank(owner);
        p.markShipped(1);

        string memory uri = p.tokenURI(1);
        string memory prefix = "data:application/json;base64,";
        assertTrue(_startsWith(uri, prefix));
        string memory json = string(_b64decode(_slice(uri, bytes(prefix).length)));
        assertEq(json, p.metadataJson(1));

        // parseJson* reverts on malformed JSON, so these also prove it parses
        assertEq(vm.parseJsonString(json, ".name"), unicode"Argo Exo · Apparatus No. 0001");
        assertEq(vm.parseJsonString(json, ".description"), unicode"Placeholder · café it's fine");
        assertEq(vm.parseJsonString(json, ".attributes[0].trait_type"), "Device number");
        assertEq(vm.parseJsonUint(json, ".attributes[0].value"), 1);
        assertEq(vm.parseJsonString(json, ".attributes[1].value"), "The Apparatus");
        assertEq(vm.parseJsonString(json, ".attributes[2].value"), "1234.56");
        assertEq(vm.parseJsonString(json, ".attributes[3].value"), "SHIPPED");

        string memory img = vm.parseJsonString(json, ".image");
        string memory svgPrefix = "data:image/svg+xml;base64,";
        assertTrue(_startsWith(img, svgPrefix));
        string memory art = string(_b64decode(_slice(img, bytes(svgPrefix).length)));
        assertEq(art, p.svg(1));
        assertTrue(_startsWith(art, "<svg "));
        assertTrue(_contains(art, unicode"PRE-ORDER RECEIPT · SHIPPED"));
        assertTrue(_contains(art, "No. 0001"));
    }

    function test_usdc_formatting() public {
        uint256[4] memory amounts = [uint256(5e4), 499e6, 1, 10_000_990_000];
        string[4] memory shown = ["0.05", "499.00", "0.00", "10000.99"];
        for (uint256 i = 0; i < 4; i++) {
            vm.prank(owner);
            p.setMaxSupply(i + 1);
            vm.prank(owner);
            p.setPrice(1, amounts[i]);
            usdc.mint(buyer, amounts[i]);
            vm.startPrank(buyer);
            usdc.approve(address(p), amounts[i]);
            uint256 id = p.preorder(1, amounts[i]);
            vm.stopPrank();
            assertEq(vm.parseJsonString(p.metadataJson(id), ".attributes[2].value"), shown[i]);
        }
    }

    function test_tokenURI_of_missing_or_refunded_reverts() public {
        vm.expectRevert(abi.encodeWithSelector(IERC721Errors.ERC721NonexistentToken.selector, 1));
        p.tokenURI(1);
        _buy(buyer);
        vm.prank(owner);
        p.markRefunded(1);
        vm.expectRevert(abi.encodeWithSelector(IERC721Errors.ERC721NonexistentToken.selector, 1));
        p.tokenURI(1);
    }

    function test_unsafe_strings_rejected() public {
        string[16] memory bad = [
            'Bad "name"', "back\\slash", "<script>", "a>b", "Tom & Jerry", "line\nbreak", "tab\there",
            _s(hex"00"), _s(hex"7f"),                              // NUL, DEL
            _s(hex"c0af"), _s(hex"80"), _s(hex"41e282"),           // overlong, lone continuation, truncated at end
            _s(hex"eda080"), _s(hex"f4908080"), _s(hex"f5"),       // UTF-16 surrogate, above U+10FFFF, bad lead
            _s(hex"e2824141")                                      // continuation byte missing mid-string
        ];
        vm.startPrank(owner);
        for (uint256 i = 0; i < bad.length; i++) {
            vm.expectRevert(ExoPreorder.UnsafeString.selector);
            p.setTierName(1, bad[i]);
            vm.expectRevert(ExoPreorder.UnsafeString.selector);
            p.setDescription(bad[i]);
        }
        assertEq(p.tierName(1), "The Apparatus");
        assertEq(p.description(), "");
        // ordinary text, apostrophes and well-formed UTF-8 are fine
        p.setTierName(1, string.concat(unicode"Founders' Edition · — é ", string(hex"f09f9a80")));
        p.setDescription("A plain sentence, with (punctuation): 1-2; ok?");
        vm.stopPrank();
        assertEq(p.tierName(1), string.concat(unicode"Founders' Edition · — é ", string(hex"f09f9a80")));
    }

    // ---- owner --------------------------------------------------------------

    function test_owner_only_and_refund_burns() public {
        address s = address(0x5778);
        bytes memory unauth = abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, s);
        vm.startPrank(s);
        vm.expectRevert(unauth); p.setPrice(1, 1);
        vm.expectRevert(unauth); p.setTierName(1, "x");
        vm.expectRevert(unauth); p.setDescription("x");
        vm.expectRevert(unauth); p.setTreasury(s);
        vm.expectRevert(unauth); p.setMaxSupply(100);
        vm.expectRevert(unauth); p.pause();
        vm.expectRevert(unauth); p.unpause();
        vm.expectRevert(unauth); p.markShipped(1);
        vm.expectRevert(unauth); p.markRefunded(1);
        vm.stopPrank();

        _buy(buyer);
        vm.prank(owner);
        p.markRefunded(1);
        vm.expectRevert(abi.encodeWithSelector(IERC721Errors.ERC721NonexistentToken.selector, 1));
        p.ownerOf(1);
        assertEq(uint8(p.statusOf(1)), uint8(ExoPreorder.Status.Refunded));
        assertEq(p.paidOf(1), PRICE);                        // the record of what was paid stays
        assertEq(p.balanceOf(buyer), 0);
        assertEq(_buy(buyer), 2);                            // numbers are never reused
        vm.startPrank(owner);
        vm.expectRevert(abi.encodeWithSelector(IERC721Errors.ERC721NonexistentToken.selector, 1));
        p.markRefunded(1);                                   // no double refund record
        vm.expectRevert(abi.encodeWithSelector(IERC721Errors.ERC721NonexistentToken.selector, 1));
        p.markShipped(1);
        vm.expectRevert(abi.encodeWithSelector(IERC721Errors.ERC721NonexistentToken.selector, 9));
        p.markShipped(9);
        vm.stopPrank();
    }

    function test_max_supply_and_treasury_guards() public {
        _buy(buyer); _buy(buyer);
        vm.startPrank(owner);
        vm.expectRevert(ExoPreorder.BelowMinted.selector);
        p.setMaxSupply(1);
        vm.expectRevert(ExoPreorder.ZeroAddress.selector);
        p.setTreasury(address(0));
        p.setMaxSupply(2);                                   // equal to minted closes the sale
        vm.stopPrank();
        assertEq(p.maxSupply(), 2);
        vm.startPrank(buyer);
        usdc.approve(address(p), PRICE);
        vm.expectRevert(ExoPreorder.SoldOut.selector);
        p.preorder(1, PRICE);
        vm.stopPrank();
        vm.prank(owner);
        p.setMaxSupply(10);
        assertEq(_buy(buyer), 3);
    }

    function test_constructor_rejects_zero_addresses() public {
        vm.expectRevert(ExoPreorder.ZeroAddress.selector);
        new ExoPreorder(address(0), treasury, owner, 3);
        vm.expectRevert(ExoPreorder.ZeroAddress.selector);
        new ExoPreorder(address(usdc), address(0), owner, 3);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableInvalidOwner.selector, address(0)));
        new ExoPreorder(address(usdc), treasury, address(0), 3);
    }

    function test_ownable2step_and_no_renounce() public {
        address next = address(0x2C);
        vm.prank(owner);
        p.transferOwnership(next);
        assertEq(p.owner(), owner);                          // nothing changes until accepted
        assertEq(p.pendingOwner(), next);
        vm.prank(address(0xBAD));
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, address(0xBAD)));
        p.acceptOwnership();
        vm.prank(next);
        p.acceptOwnership();
        assertEq(p.owner(), next);
        // renouncing would leave a live sale nobody can pause or close
        vm.prank(next);
        vm.expectRevert(ExoPreorder.RenounceDisabled.selector);
        p.renounceOwnership();
        assertEq(p.owner(), next);
    }

    function test_transferable() public {
        _buy(buyer);
        vm.prank(buyer);
        p.transferFrom(buyer, address(0xA11CE), 1);
        assertEq(p.ownerOf(1), address(0xA11CE));
    }

    // ---- helpers ------------------------------------------------------------

    function _contains(string memory a, string memory b) internal pure returns (bool) {
        bytes memory x = bytes(a); bytes memory y = bytes(b);
        if (y.length > x.length) return false;
        for (uint256 i = 0; i <= x.length - y.length; i++) {
            bool ok = true;
            for (uint256 k = 0; k < y.length; k++) if (x[i + k] != y[k]) { ok = false; break; }
            if (ok) return true;
        }
        return false;
    }

    function _s(bytes memory b) internal pure returns (string memory) { return string(b); }

    function _startsWith(string memory a, string memory b) internal pure returns (bool) {
        bytes memory x = bytes(a); bytes memory y = bytes(b);
        if (y.length > x.length) return false;
        for (uint256 i = 0; i < y.length; i++) if (x[i] != y[i]) return false;
        return true;
    }

    function _slice(string memory a, uint256 from) internal pure returns (string memory) {
        bytes memory x = bytes(a);
        bytes memory out = new bytes(x.length - from);
        for (uint256 i = 0; i < out.length; i++) out[i] = x[from + i];
        return string(out);
    }

    function _b64val(bytes1 c) internal pure returns (uint256) {
        if (c >= "A" && c <= "Z") return uint8(c) - 65;
        if (c >= "a" && c <= "z") return uint8(c) - 71;
        if (c >= "0" && c <= "9") return uint8(c) + 4;
        if (c == "+") return 62;
        if (c == "/") return 63;
        revert("bad base64 char");
    }

    /// Strict standard-alphabet decoder: length must be a multiple of 4, '=' only as trailing padding.
    function _b64decode(string memory s) internal pure returns (bytes memory out) {
        bytes memory b = bytes(s);
        require(b.length % 4 == 0, "base64 length");
        uint256 pad = b.length > 0 && b[b.length - 1] == "=" ? (b[b.length - 2] == "=" ? 2 : 1) : 0;
        out = new bytes(b.length / 4 * 3 - pad);
        uint256 o;
        for (uint256 i = 0; i < b.length; i += 4) {
            uint256 n;
            for (uint256 k = 0; k < 4; k++) {
                bytes1 c = b[i + k];
                if (c == "=") { require(i + 4 == b.length && k >= 4 - pad, "bad padding"); n <<= 6; }
                else n = (n << 6) | _b64val(c);
            }
            for (uint256 k = 0; k < 3 && o < out.length; k++) out[o++] = bytes1(uint8(n >> (16 - 8 * k)));
        }
    }
}
