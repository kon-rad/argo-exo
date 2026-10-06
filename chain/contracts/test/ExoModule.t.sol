// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {ExoModule} from "../src/ExoModule.sol";
import {ReceiverTemplate} from "../src/cre/ReceiverTemplate.sol";
import {IReceiver} from "../src/cre/IReceiver.sol";
import {IERC165} from "../src/cre/IERC165.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {MockSafe} from "./mocks/MockSafe.sol";

contract Sink { uint256 public hits; function ping() external payable { hits++; } }

contract Reverter { function ping() external payable { revert("nope"); } }

contract ExoModuleTest is Test {
    // mirrors ExoModule's, for expectEmit
    event Approved(bytes32 indexed txHash, uint64 expiresAt, bytes32 reasonHash);
    event Refused(bytes32 indexed txHash, bytes32 reasonHash);
    event Executed(bytes32 indexed txHash, address to, uint256 value);
    event Frozen(address by);
    event Unfrozen();

    address forwarder = address(0xF0);
    address deck = address(0xDE);
    address owner = address(0x0C);
    address reporter = address(0x5E);
    MockSafe safe;
    ExoModule m;
    Sink sink;
    bytes data;
    bytes32 salt = bytes32(uint256(0xaa));

    function setUp() public {
        safe = new MockSafe();
        m = new ExoModule(forwarder, address(safe), deck, owner, 1 ether, 2 ether);
        safe.enableModule(address(m));
        vm.deal(address(safe), 10 ether);
        sink = new Sink();
        data = abi.encodeCall(Sink.ping, ());
        vm.prank(owner);
        m.setDemoReporter(reporter);
    }

    function _report(uint8 kind, bytes32 h, uint64 exp) internal {
        vm.prank(forwarder, reporter);
        m.onReport("", abi.encode(kind, h, exp, keccak256("reason")));
    }

    function _approve(uint256 value) internal returns (bytes32 h) {
        h = m.approvalHash(address(sink), value, data, salt);
        _report(1, h, uint64(block.timestamp + 600));
    }

    // ---------------------------------------------------------------- shared vector

    /// The cast-made vector (chain/test-vectors/approval-hash.json) must equal the module's own approvalHash
    /// when the module sits at the vector's address on the vector's chain.
    function test_vector_matches_cast() public {
        string memory j = vm.readFile("../test-vectors/approval-hash.json");
        uint256 chainId = vm.parseJsonUint(j, ".chain_id");
        address moduleAddr = vm.parseJsonAddress(j, ".module");
        address to = vm.parseJsonAddress(j, ".to");
        uint256 value = vm.parseJsonUint(j, ".value");
        bytes memory vdata = vm.parseJsonBytes(j, ".data");
        bytes32 vsalt = vm.parseJsonBytes32(j, ".salt");
        bytes32 expected = vm.parseJsonBytes32(j, ".hash");
        assertGt(value, 0);
        assertGt(vdata.length, 0);

        vm.chainId(chainId);
        deployCodeTo(
            "ExoModule.sol:ExoModule",
            abi.encode(forwarder, address(safe), deck, owner, uint256(1 ether), uint256(2 ether)),
            moduleAddr
        );
        assertEq(ExoModule(moduleAddr).approvalHash(to, value, vdata, vsalt), expected);
        // and the formula written out longhand agrees
        assertEq(keccak256(abi.encode(chainId, moduleAddr, to, value, keccak256(vdata), vsalt)), expected);
    }

    function test_approval_hash_binds_chain_and_module() public {
        bytes32 h = m.approvalHash(address(sink), 0, data, salt);
        ExoModule other = new ExoModule(forwarder, address(safe), deck, owner, 1 ether, 2 ether);
        assertTrue(other.approvalHash(address(sink), 0, data, salt) != h);
        vm.chainId(block.chainid + 1);
        assertTrue(m.approvalHash(address(sink), 0, data, salt) != h);
    }

    // ---------------------------------------------------------------- execute

    function test_execute_approved() public {
        bytes32 h = _approve(0.5 ether);
        vm.expectEmit(true, false, false, true);
        emit Executed(h, address(sink), 0.5 ether);
        vm.prank(deck);
        bytes32 ret = m.execute(address(sink), 0.5 ether, data, salt);
        assertEq(ret, h);
        assertEq(sink.hits(), 1);
        assertTrue(m.used(h));
        assertEq(address(sink).balance, 0.5 ether);
        assertEq(m.spentOnDay(block.timestamp / 1 days), 0.5 ether);
    }

    function test_execute_at_exact_expiry_succeeds() public {
        _approve(0);
        vm.warp(block.timestamp + 600);
        vm.prank(deck);
        m.execute(address(sink), 0, data, salt);
        assertEq(sink.hits(), 1);
    }

    function test_only_deck() public {
        _approve(0);
        vm.expectRevert(ExoModule.NotDeck.selector);
        m.execute(address(sink), 0, data, salt);
        vm.prank(owner);
        vm.expectRevert(ExoModule.NotDeck.selector);
        m.execute(address(sink), 0, data, salt);
        vm.prank(forwarder);
        vm.expectRevert(ExoModule.NotDeck.selector);
        m.execute(address(sink), 0, data, salt);
        assertEq(sink.hits(), 0);
    }

    function test_unapproved_reverts() public {
        vm.prank(deck);
        vm.expectRevert(ExoModule.NotApproved.selector);
        m.execute(address(sink), 0, data, salt);
    }

    function test_changed_calldata_is_not_approved() public {
        _approve(0);
        vm.startPrank(deck);
        vm.expectRevert(ExoModule.NotApproved.selector);
        m.execute(address(sink), 1, data, salt);                       // value changed
        vm.expectRevert(ExoModule.NotApproved.selector);
        m.execute(address(sink), 0, abi.encodePacked(data, uint8(0)), salt); // data changed
        vm.expectRevert(ExoModule.NotApproved.selector);
        m.execute(address(0xBEEF), 0, data, salt);                    // target changed
        vm.expectRevert(ExoModule.NotApproved.selector);
        m.execute(address(sink), 0, data, bytes32(uint256(0xab)));    // salt changed
        vm.stopPrank();
        assertEq(sink.hits(), 0);
    }

    function test_expired_reverts() public {
        _approve(0);
        vm.warp(block.timestamp + 601);
        vm.prank(deck);
        vm.expectRevert(ExoModule.Expired.selector);
        m.execute(address(sink), 0, data, salt);
    }

    function test_approval_already_expired_on_arrival_reverts() public {
        vm.warp(10_000);
        bytes32 h = m.approvalHash(address(sink), 0, data, salt);
        _report(1, h, uint64(block.timestamp - 1));
        vm.prank(deck);
        vm.expectRevert(ExoModule.Expired.selector);
        m.execute(address(sink), 0, data, salt);
    }

    function test_replay_reverts() public {
        bytes32 h = _approve(0);
        vm.startPrank(deck);
        m.execute(address(sink), 0, data, salt);
        vm.expectRevert(ExoModule.AlreadyUsed.selector);
        m.execute(address(sink), 0, data, salt);
        vm.stopPrank();
        // a fresh approval of the same hash does not resurrect it
        _report(1, h, uint64(block.timestamp + 600));
        vm.prank(deck);
        vm.expectRevert(ExoModule.AlreadyUsed.selector);
        m.execute(address(sink), 0, data, salt);
        assertEq(sink.hits(), 1);
    }

    function test_safe_call_failure_reverts_and_leaves_hash_unused() public {
        Reverter r = new Reverter();
        bytes memory d = abi.encodeCall(Reverter.ping, ());
        bytes32 h = m.approvalHash(address(r), 0.1 ether, d, salt);
        _report(1, h, uint64(block.timestamp + 600));
        vm.prank(deck);
        vm.expectRevert(ExoModule.SafeCallFailed.selector);
        m.execute(address(r), 0.1 ether, d, salt);
        assertFalse(m.used(h));
        assertEq(m.spentOnDay(block.timestamp / 1 days), 0);
    }

    // ---------------------------------------------------------------- caps

    function test_caps() public {
        _approve(1.5 ether);
        vm.prank(deck);
        vm.expectRevert(ExoModule.OverTxCap.selector);
        m.execute(address(sink), 1.5 ether, data, salt);
        bytes32 salt2 = bytes32(uint256(0xbb));
        for (uint256 i = 0; i < 2; i++) {
            bytes32 s = bytes32(uint256(0xc0 + i));
            _report(1, m.approvalHash(address(sink), 1 ether, data, s), uint64(block.timestamp + 600));
            vm.prank(deck);
            m.execute(address(sink), 1 ether, data, s);
        }
        _report(1, m.approvalHash(address(sink), 1 wei, data, salt2), uint64(block.timestamp + 600));
        vm.prank(deck);
        vm.expectRevert(ExoModule.OverDayCap.selector);
        m.execute(address(sink), 1 wei, data, salt2);
    }

    function test_tx_cap_boundary_and_day_rollover() public {
        vm.warp(1 days * 20_000 + 100);                               // start of a UTC day
        _report(1, m.approvalHash(address(sink), 1 ether, data, bytes32(uint256(1))), uint64(block.timestamp + 2 days));
        _report(1, m.approvalHash(address(sink), 1 ether, data, bytes32(uint256(2))), uint64(block.timestamp + 2 days));
        _report(1, m.approvalHash(address(sink), 1 ether, data, bytes32(uint256(3))), uint64(block.timestamp + 2 days));
        vm.startPrank(deck);
        m.execute(address(sink), 1 ether, data, bytes32(uint256(1)));   // exactly the per-tx cap is allowed
        m.execute(address(sink), 1 ether, data, bytes32(uint256(2)));   // exactly the per-day cap is allowed
        vm.expectRevert(ExoModule.OverDayCap.selector);
        m.execute(address(sink), 1 ether, data, bytes32(uint256(3)));
        vm.warp(block.timestamp + 1 days);                             // next UTC day: budget resets
        m.execute(address(sink), 1 ether, data, bytes32(uint256(3)));
        vm.stopPrank();
        assertEq(address(sink).balance, 3 ether);
    }

    function test_set_caps_only_owner_and_applies() public {
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, address(this)));
        m.setCaps(5 ether, 5 ether);
        vm.prank(owner);
        m.setCaps(0, 0);
        _approve(1 wei);
        vm.prank(deck);
        vm.expectRevert(ExoModule.OverTxCap.selector);
        m.execute(address(sink), 1 wei, data, salt);
    }

    // ---------------------------------------------------------------- freeze

    function test_freeze_by_report_and_by_deck_and_unfreeze_by_owner() public {
        vm.expectEmit(false, false, false, true);
        emit Frozen(forwarder);
        _report(3, bytes32(0), 0);
        assertTrue(m.frozen());
        _approve(0);
        vm.prank(deck);
        vm.expectRevert(ExoModule.IsFrozen.selector);
        m.execute(address(sink), 0, data, salt);
        vm.expectRevert();
        m.unfreeze();                                   // not owner
        vm.prank(deck);
        vm.expectRevert();
        m.unfreeze();                                   // the deck cannot unfreeze either
        vm.prank(owner);
        m.unfreeze();
        assertFalse(m.frozen());
        vm.prank(deck);
        m.execute(address(sink), 0, data, salt);        // works again once unfrozen
        vm.prank(deck);
        m.freeze();                                     // the panic buttons, no cloud needed
        assertTrue(m.frozen());
        vm.prank(owner);
        m.unfreeze();
        vm.prank(owner);
        m.freeze();
        assertTrue(m.frozen());
    }

    function test_freeze_by_stranger_reverts() public {
        vm.prank(address(0xBAD));
        vm.expectRevert(ExoModule.NotDeck.selector);
        m.freeze();
        assertFalse(m.frozen());
    }

    // ---------------------------------------------------------------- reports

    function test_approve_report_records_and_emits() public {
        bytes32 h = m.approvalHash(address(sink), 0, data, salt);
        uint64 exp = uint64(block.timestamp + 600);
        vm.expectEmit(true, false, false, true);
        emit Approved(h, exp, keccak256("reason"));
        _report(1, h, exp);
        assertEq(m.approvedUntil(h), exp);
    }

    function test_refuse_is_recorded_only() public {
        bytes32 h = m.approvalHash(address(sink), 0, data, salt);
        vm.expectEmit(true, false, false, true);
        emit Refused(h, keccak256("reason"));
        _report(2, h, uint64(block.timestamp + 600));   // even with an expiry attached
        assertEq(m.approvedUntil(h), 0);
        assertFalse(m.frozen());
        vm.prank(deck);
        vm.expectRevert(ExoModule.NotApproved.selector);
        m.execute(address(sink), 0, data, salt);
    }

    function test_report_from_wrong_forwarder_reverts() public {
        bytes32 h = bytes32(uint256(1));
        vm.prank(address(0xBAD), reporter);
        vm.expectRevert(abi.encodeWithSelector(ReceiverTemplate.InvalidSender.selector, address(0xBAD), forwarder));
        m.onReport("", abi.encode(uint8(1), h, uint64(block.timestamp + 600), bytes32(0)));
        // the deck itself cannot self-approve either
        vm.prank(deck, deck);
        vm.expectRevert(abi.encodeWithSelector(ReceiverTemplate.InvalidSender.selector, deck, forwarder));
        m.onReport("", abi.encode(uint8(3), h, uint64(0), bytes32(0)));
        assertEq(m.approvedUntil(h), 0);
        assertFalse(m.frozen());
    }

    function test_report_with_wrong_origin_reverts() public {
        bytes32 h = bytes32(uint256(1));
        vm.prank(forwarder, address(0xBAD));
        vm.expectRevert(ExoModule.BadReporter.selector);
        m.onReport("", abi.encode(uint8(1), h, uint64(block.timestamp + 600), bytes32(0)));
        assertEq(m.approvedUntil(h), 0);
    }

    function test_demo_reporter_zero_disables_origin_check() public {
        vm.prank(owner);
        m.setDemoReporter(address(0));
        bytes32 h = bytes32(uint256(1));
        vm.prank(forwarder, address(0x1234));
        m.onReport("", abi.encode(uint8(1), h, uint64(block.timestamp + 600), bytes32(0)));
        assertEq(m.approvedUntil(h), uint64(block.timestamp + 600));
    }

    function test_unknown_kind_reverts() public {
        vm.startPrank(forwarder, reporter);
        vm.expectRevert(abi.encodeWithSelector(ExoModule.UnknownKind.selector, uint8(9)));
        m.onReport("", abi.encode(uint8(9), bytes32(0), uint64(0), bytes32(0)));
        vm.expectRevert(abi.encodeWithSelector(ExoModule.UnknownKind.selector, uint8(0)));
        m.onReport("", abi.encode(uint8(0), bytes32(uint256(1)), uint64(block.timestamp + 600), bytes32(0)));
        vm.stopPrank();
        assertEq(m.approvedUntil(bytes32(uint256(1))), 0);
    }

    function test_malformed_report_reverts() public {
        vm.prank(forwarder, reporter);
        vm.expectRevert();
        m.onReport("", hex"01");
    }

    // ---------------------------------------------------------------- admin

    function test_constructor_wiring() public view {
        assertEq(address(m.safe()), address(safe));
        assertEq(m.deck(), deck);
        assertEq(m.owner(), owner);
        assertEq(m.getForwarderAddress(), forwarder);
        assertEq(m.maxNativePerTx(), 1 ether);
        assertEq(m.maxNativePerDay(), 2 ether);
        assertEq(m.demoReporter(), reporter);
        assertFalse(m.frozen());
    }

    function test_constructor_rejects_zero_forwarder() public {
        vm.expectRevert(ReceiverTemplate.InvalidForwarderAddress.selector);
        new ExoModule(address(0), address(safe), deck, owner, 1 ether, 2 ether);
    }

    function test_admin_setters_only_owner() public {
        vm.startPrank(deck);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, deck));
        m.setDeck(deck);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, deck));
        m.setDemoReporter(address(0));
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, deck));
        m.setForwarderAddress(deck);
        vm.stopPrank();
    }

    function test_set_deck_moves_execute_rights() public {
        address newDeck = address(0xD2);
        vm.prank(owner);
        m.setDeck(newDeck);
        _approve(0);
        vm.prank(deck);
        vm.expectRevert(ExoModule.NotDeck.selector);
        m.execute(address(sink), 0, data, salt);
        vm.prank(newDeck);
        m.execute(address(sink), 0, data, salt);
        assertEq(sink.hits(), 1);
    }

    function test_supports_receiver_interface() public view {
        assertTrue(m.supportsInterface(type(IReceiver).interfaceId));
        assertTrue(m.supportsInterface(type(IERC165).interfaceId));
        assertFalse(m.supportsInterface(0xffffffff));
    }
}
