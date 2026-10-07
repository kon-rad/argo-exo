// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Permit} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Permit.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Base64} from "@openzeppelin/contracts/utils/Base64.sol";
import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";

/// @title ExoPreorder: a numbered pre-order receipt for the Argo Exo, paid in USDC.
/// @notice Token ID = device number (from 1, never reused). USDC goes straight from the buyer to the treasury in
///         the same call; this contract never holds any. Prices are USDC base units set by the owner, 0 = closed.
///         The buyer always passes a maxPrice, so a price raised before inclusion reverts instead of overcharging.
contract ExoPreorder is ERC721, Ownable2Step, Pausable {
    using SafeERC20 for IERC20;
    using Strings for uint256;

    enum Status { Preordered, Shipped, Refunded }

    IERC20 public immutable usdc;
    address public treasury;
    uint256 public maxSupply;
    uint256 public totalMinted;
    string public description;
    mapping(uint8 => uint256) public price;
    mapping(uint8 => string) public tierName;
    mapping(uint256 => uint8) public tierOf;
    mapping(uint256 => uint256) public paidOf;
    mapping(uint256 => Status) public statusOf;

    event Preordered(uint256 indexed deviceNumber, address indexed buyer, uint8 tier, uint256 price);
    event PriceSet(uint8 indexed tier, uint256 price);
    event TierNameSet(uint8 indexed tier, string name);
    event DescriptionSet(string description);
    event MaxSupplySet(uint256 maxSupply);
    event StatusSet(uint256 indexed deviceNumber, Status status);
    event TreasurySet(address treasury);

    error NotForSale(uint8 tier);
    error PriceAboveMax(uint256 price, uint256 maxPrice);
    error SoldOut();
    error ZeroAddress();
    error UnsafeString();
    error BelowMinted();
    error RenounceDisabled();
    error TierNameTooLong(uint256 length, uint256 max);

    /// Longest tier name, in bytes, that fits the plate's tier line inside its border.
    uint256 public constant MAX_TIER_NAME_BYTES = 24;

    constructor(address usdc_, address treasury_, address owner_, uint256 maxSupply_)
        ERC721("Argo Exo Pre-order", "EXO") Ownable(owner_)
    {
        if (usdc_ == address(0) || treasury_ == address(0)) revert ZeroAddress();
        usdc = IERC20(usdc_);
        treasury = treasury_;
        maxSupply = maxSupply_;
    }

    // ---- buying -------------------------------------------------------------

    /// @notice Pay price(tier) in USDC (needs an allowance) and receive the next device number.
    /// @param maxPrice the most the buyer agrees to pay, in USDC base units; the call reverts above it.
    function preorder(uint8 tier, uint256 maxPrice) public whenNotPaused returns (uint256 id) {
        uint256 p = price[tier];
        if (p == 0) revert NotForSale(tier);
        if (p > maxPrice) revert PriceAboveMax(p, maxPrice);
        if (totalMinted >= maxSupply) revert SoldOut();
        // effects, then the payment, then the mint (whose onERC721Received callback is the only re-entry point)
        id = ++totalMinted;
        tierOf[id] = tier;
        paidOf[id] = p;
        emit Preordered(id, msg.sender, tier, p);
        usdc.safeTransferFrom(msg.sender, treasury, p);
        _safeMint(msg.sender, id);
    }

    /// @notice One transaction: USDC permit for maxPrice, then preorder. A permit that fails (already used by a
    ///         front-runner, expired, or not the caller's) is ignored; the purchase then needs the allowance to
    ///         already exist, and reverts unpaid if it does not.
    function preorderWithPermit(uint8 tier, uint256 maxPrice, uint256 deadline, uint8 v, bytes32 r, bytes32 s)
        external returns (uint256)
    {
        try IERC20Permit(address(usdc)).permit(msg.sender, address(this), maxPrice, deadline, v, r, s) {} catch {}
        return preorder(tier, maxPrice);
    }

    // ---- owner --------------------------------------------------------------

    function setPrice(uint8 tier, uint256 p) external onlyOwner { price[tier] = p; emit PriceSet(tier, p); }
    function setTierName(uint8 tier, string calldata n) external onlyOwner {
        if (bytes(n).length > MAX_TIER_NAME_BYTES) revert TierNameTooLong(bytes(n).length, MAX_TIER_NAME_BYTES);
        _safe(n);
        tierName[tier] = n;
        emit TierNameSet(tier, n);
    }
    function setDescription(string calldata d) external onlyOwner { _safe(d); description = d; emit DescriptionSet(d); }
    function setTreasury(address t) external onlyOwner { if (t == address(0)) revert ZeroAddress(); treasury = t; emit TreasurySet(t); }
    function setMaxSupply(uint256 n) external onlyOwner { if (n < totalMinted) revert BelowMinted(); maxSupply = n; emit MaxSupplySet(n); }
    function pause() external onlyOwner { _pause(); }
    function unpause() external onlyOwner { _unpause(); }
    function markShipped(uint256 id) external onlyOwner { _requireOwned(id); statusOf[id] = Status.Shipped; emit StatusSet(id, Status.Shipped); }

    /// @notice After the USDC has been returned off-contract: record it and burn the receipt. The number stays
    ///         retired (totalMinted never goes down) and paidOf keeps the amount that was paid.
    function markRefunded(uint256 id) external onlyOwner { _requireOwned(id); statusOf[id] = Status.Refunded; emit StatusSet(id, Status.Refunded); _burn(id); }

    /// @notice Disabled: an ownerless contract would keep selling at its last price with nobody able to pause it.
    ///         Hand over with transferOwnership + acceptOwnership instead.
    function renounceOwnership() public view override onlyOwner { revert RenounceDisabled(); }

    // ---- metadata (fully onchain) -------------------------------------------

    function tokenURI(uint256 id) public view override returns (string memory) {
        _requireOwned(id);
        return string.concat("data:application/json;base64,", Base64.encode(bytes(metadataJson(id))));
    }

    function metadataJson(uint256 id) public view returns (string memory) {
        string memory img = string.concat("data:image/svg+xml;base64,", Base64.encode(bytes(svg(id))));
        return string.concat(
            '{"name":"Argo Exo \u00b7 Apparatus No. ', _pad4(id), '","description":"', description, '","image":"', img,
            '","attributes":[{"trait_type":"Device number","value":', id.toString(),
            '},{"trait_type":"Tier","value":"', tierName[tierOf[id]],
            '"},{"trait_type":"Paid (USDC)","value":"', _usdc(paidOf[id]),
            '"},{"trait_type":"Status","value":"', _status(statusOf[id]), '"}]}');
    }

    /// @notice An engraved-plate receipt: ivory paper, ink rules, small caps, the Argo scientific-volume style.
    function svg(uint256 id) public view returns (string memory) {
        return string.concat(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 800" font-family="Georgia,serif">',
            '<rect width="600" height="800" fill="#F4F0E9"/>',
            '<rect x="28" y="28" width="544" height="744" fill="none" stroke="#2B2722" stroke-width="2"/>',
            '<rect x="38" y="38" width="524" height="724" fill="none" stroke="#2B2722" stroke-width="0.8"/>',
            '<text x="300" y="150" text-anchor="middle" font-size="44" letter-spacing="10" fill="#2B2722">ARGO EXO</text>',
            '<line x1="120" y1="178" x2="480" y2="178" stroke="#2B2722" stroke-width="2"/>',
            '<line x1="120" y1="185" x2="480" y2="185" stroke="#2B2722" stroke-width="0.8"/>',
            '<text x="300" y="300" text-anchor="middle" font-size="30" font-style="italic" fill="#2B2722">Apparatus</text>',
            '<text x="300" y="420" text-anchor="middle" font-size="96" fill="#2B2722">No. ', _pad4(id), '</text>',
            '<line x1="200" y1="460" x2="400" y2="460" stroke="#CE7F44" stroke-width="1.5"/>',
            '<text x="300" y="520" text-anchor="middle" font-size="24" letter-spacing="4" fill="#2B2722">', tierName[tierOf[id]], '</text>',
            '<text x="300" y="640" text-anchor="middle" font-size="18" letter-spacing="3" fill="#6B6158">PRE-ORDER RECEIPT \u00b7 ', _status(statusOf[id]), '</text>',
            '<text x="300" y="720" text-anchor="middle" font-size="14" fill="#6B6158">myargoquest.com</text></svg>');
    }

    // ---- helpers ------------------------------------------------------------

    function _pad4(uint256 n) internal pure returns (string memory s) {
        s = n.toString();
        while (bytes(s).length < 4) s = string.concat("0", s);
    }

    /// @dev USDC base units (6 decimals) as dollars and cents, truncated: 499000000 -> "499.00".
    function _usdc(uint256 units) internal pure returns (string memory) {
        uint256 cents = (units % 1e6) / 1e4;
        return string.concat((units / 1e6).toString(), ".", cents < 10 ? "0" : "", cents.toString());
    }

    function _status(Status s) internal pure returns (string memory) {
        return s == Status.Shipped ? "SHIPPED" : s == Status.Refunded ? "REFUNDED" : "PRE-ORDERED";
    }

    /// @dev Owner strings land raw inside JSON strings and SVG text, so refuse anything that could break either:
    ///      `"` `\` `<` `>` `&`, ASCII control characters and DEL, and any byte sequence that is not well-formed
    ///      UTF-8 (overlongs, surrogates, truncated or stray continuation bytes, code points above U+10FFFF).
    ///      Also refuses U+FFFE, U+FFFF (not legal XML characters, so they would break svg() for XML parsers) and
    ///      the noncharacters U+FDD0..U+FDEF.
    function _safe(string calldata v) internal pure {
        bytes calldata b = bytes(v);
        uint256 i;
        while (i < b.length) {
            uint8 c = uint8(b[i]);
            if (c < 0x80) {
                if (c < 0x20 || c == 0x7F || c == 0x22 || c == 0x5C || c == 0x3C || c == 0x3E || c == 0x26) {
                    revert UnsafeString();
                }
                i++;
                continue;
            }
            uint256 n;
            uint8 lo = 0x80;
            uint8 hi = 0xBF;
            if (c >= 0xC2 && c <= 0xDF) n = 1;
            else if (c == 0xE0) { n = 2; lo = 0xA0; }
            else if ((c >= 0xE1 && c <= 0xEC) || c == 0xEE || c == 0xEF) n = 2;
            else if (c == 0xED) { n = 2; hi = 0x9F; }
            else if (c == 0xF0) { n = 3; lo = 0x90; }
            else if (c >= 0xF1 && c <= 0xF3) n = 3;
            else if (c == 0xF4) { n = 3; hi = 0x8F; }
            else revert UnsafeString();
            if (i + n >= b.length) revert UnsafeString();
            for (uint256 k = 1; k <= n; k++) {
                uint8 d = uint8(b[i + k]);
                if (k == 1 ? (d < lo || d > hi) : (d < 0x80 || d > 0xBF)) revert UnsafeString();
            }
            if (c == 0xEF) {
                uint8 b1 = uint8(b[i + 1]);
                uint8 b2 = uint8(b[i + 2]);
                if ((b1 == 0xBF && b2 >= 0xBE) || (b1 == 0xB7 && b2 >= 0x90 && b2 <= 0xAF)) revert UnsafeString();
            }
            i += n + 1;
        }
    }
}
