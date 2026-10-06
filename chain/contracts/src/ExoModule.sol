// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ReceiverTemplate} from "./cre/ReceiverTemplate.sol";

interface ISafe {
    function execTransactionFromModule(address to, uint256 value, bytes calldata data, uint8 operation) external returns (bool);
}

/// @title ExoModule — executes only transactions the Exo CRE Guardian approved, only for the deck.
/// @notice Reports: abi.encode(uint8 kind, bytes32 txHash, uint64 expiresAt, bytes32 reasonHash).
///         kind 1 = approve, 2 = refuse (recorded), 3 = freeze.
/// @dev Approval hash: keccak256(abi.encode(block.chainid, address(this), to, value, keccak256(data), salt)),
///      pinned by chain/test-vectors/approval-hash.json. Calls go through the Safe as CALL (operation 0), never
///      DELEGATECALL, and never to the Safe or this module (no owner/module/guard/threshold/cap changes).
///      Threat model:
///      - Native value is capped per tx and per UTC calendar day (block.timestamp / 1 days), so up to twice the
///        day cap can leave across a UTC midnight. ERC-20 amounts are NOT capped onchain; they are bounded by
///        the Guardian's rules and the human key.
///      - An approved approve/permit/setApprovalForAll grant outlives the single transaction: the spender keeps
///        the allowance until it is revoked.
///      - Reports are accepted only if the demo reporter (simulation, tx.origin) or a workflow identity
///        (expected workflow ID or author, from ReceiverTemplate) is configured; otherwise they revert.
///        A workflow identity only authenticates behind the real KeystoneForwarder: behind the mock forwarder the
///        metadata is caller-supplied. Migration order is in chain/README.md (real forwarder, then identity,
///        then clear demoReporter); never setForwarderAddress(address(0)).
///      - Approvals live at most MAX_APPROVAL_TTL; a refuse revokes a pending approval of the same hash.
contract ExoModule is ReceiverTemplate {
    uint8 internal constant APPROVE = 1;
    uint8 internal constant REFUSE = 2;
    uint8 internal constant FREEZE = 3;
    /// @notice Longest an approval may live from the moment its report lands.
    uint64 public constant MAX_APPROVAL_TTL = 1 hours;

    ISafe public immutable safe;
    address public deck;
    /// @notice Simulation only: reports must come from this tx.origin (the CRE simulator key). Zero disables.
    address public demoReporter;
    bool public frozen;
    uint256 public maxNativePerTx;
    uint256 public maxNativePerDay;
    /// @notice Native value executed per UTC day index (block.timestamp / 1 days).
    mapping(uint256 => uint256) public spentOnDay;
    /// @notice Approval expiry (unix seconds, inclusive) per approval hash; zero means not approved.
    mapping(bytes32 => uint64) public approvedUntil;
    /// @notice True once an approval hash has executed; it can never execute again.
    mapping(bytes32 => bool) public used;

    event Approved(bytes32 indexed txHash, uint64 expiresAt, bytes32 reasonHash);
    event Refused(bytes32 indexed txHash, bytes32 reasonHash);
    event Executed(bytes32 indexed txHash, address to, uint256 value);
    event Frozen(address by);
    event Unfrozen();
    event CapsSet(uint256 maxNativePerTx, uint256 maxNativePerDay);
    event DeckSet(address deck);
    event DemoReporterSet(address reporter);

    error NotDeck();
    error NotApproved();
    error Expired();
    error AlreadyUsed();
    error IsFrozen();
    error OverTxCap();
    error OverDayCap();
    error BadReporter();
    error UnknownKind(uint8 kind);
    error SafeCallFailed();
    error SelfCall();
    error NoReportAuth();
    error BadExpiry();
    error ZeroAddress();
    error BadCaps();

    constructor(address forwarder, address safe_, address deck_, address owner_, uint256 perTx, uint256 perDay)
        ReceiverTemplate(forwarder)
    {
        if (safe_ == address(0) || deck_ == address(0) || owner_ == address(0)) revert ZeroAddress();
        safe = ISafe(safe_);
        deck = deck_;
        _setCaps(perTx, perDay);
        _transferOwnership(owner_);
    }

    function approvalHash(address to, uint256 value, bytes calldata data, bytes32 salt) public view returns (bytes32) {
        return keccak256(abi.encode(block.chainid, address(this), to, value, keccak256(data), salt));
    }

    /// @dev Called by ReceiverTemplate.onReport after the forwarder (and any workflow identity) checks pass.
    function _processReport(bytes calldata report) internal override {
        if (demoReporter != address(0)) {
            if (tx.origin != demoReporter) revert BadReporter();
        } else if (this.getExpectedWorkflowId() == bytes32(0) && this.getExpectedAuthor() == address(0)) {
            // the template keeps these private behind external getters, hence the self-staticcall
            // fail closed: with no reporter and no workflow identity, anyone who can reach the forwarder could approve
            revert NoReportAuth();
        }
        (uint8 kind, bytes32 h, uint64 exp, bytes32 reason) = abi.decode(report, (uint8, bytes32, uint64, bytes32));
        if (kind == APPROVE) {
            // rejects already-expired approvals and ones that would outlive MAX_APPROVAL_TTL (e.g. a ms timestamp)
            if (exp <= block.timestamp || exp > block.timestamp + MAX_APPROVAL_TTL) revert BadExpiry();
            approvedUntil[h] = exp;
            emit Approved(h, exp, reason);
        } else if (kind == REFUSE) {
            delete approvedUntil[h]; // revokes a pending approval; a used hash stays used
            emit Refused(h, reason);
        } else if (kind == FREEZE) {
            frozen = true;
            emit Frozen(msg.sender);
        } else {
            revert UnknownKind(kind);
        }
    }

    /// @notice Execute exactly one Guardian-approved transaction through the Safe. Deck hot key only.
    function execute(address to, uint256 value, bytes calldata data, bytes32 salt) external returns (bytes32 h) {
        if (msg.sender != deck) revert NotDeck();
        if (frozen) revert IsFrozen();
        if (to == address(safe) || to == address(this)) revert SelfCall();
        h = approvalHash(to, value, data, salt);
        uint64 exp = approvedUntil[h];
        if (exp == 0) revert NotApproved();
        if (block.timestamp > exp) revert Expired();
        if (used[h]) revert AlreadyUsed();
        if (value > maxNativePerTx) revert OverTxCap();
        uint256 day = block.timestamp / 1 days;
        if (spentOnDay[day] + value > maxNativePerDay) revert OverDayCap();
        // effects before the external call: a reentrant path can never reuse h or the day budget
        used[h] = true;
        spentOnDay[day] += value;
        if (!safe.execTransactionFromModule(to, value, data, 0)) revert SafeCallFailed();
        emit Executed(h, to, value);
    }

    /// @notice Panic button: the deck or the owner can freeze without the cloud.
    function freeze() external {
        if (msg.sender != deck && msg.sender != owner()) revert NotDeck();
        frozen = true;
        emit Frozen(msg.sender);
    }

    function unfreeze() external onlyOwner {
        frozen = false;
        emit Unfrozen();
    }

    function setCaps(uint256 perTx, uint256 perDay) external onlyOwner {
        _setCaps(perTx, perDay);
    }

    function setDeck(address d) external onlyOwner {
        deck = d;
        emit DeckSet(d);
    }

    function setDemoReporter(address r) external onlyOwner {
        demoReporter = r;
        emit DemoReporterSet(r);
    }

    function _setCaps(uint256 perTx, uint256 perDay) internal {
        if (perTx > perDay) revert BadCaps();
        maxNativePerTx = perTx;
        maxNativePerDay = perDay;
        emit CapsSet(perTx, perDay);
    }
}
