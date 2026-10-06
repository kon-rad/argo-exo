// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

contract MockSafe {
    address public module;
    event ModuleCall(address to, uint256 value, bytes data);

    function enableModule(address m) external { module = m; }

    function execTransactionFromModule(address to, uint256 value, bytes calldata data, uint8 operation) external returns (bool) {
        require(msg.sender == module, "not module");
        require(operation == 0, "no delegatecall");
        emit ModuleCall(to, value, data);
        (bool ok,) = to.call{value: value}(data);
        return ok;
    }

    receive() external payable {}
}
