"""JSON-RPC, ERC-20 reads, and sending a transaction from the agent key. Stdlib HTTP only."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from eth_abi import decode, encode
from eth_account import Account
from eth_utils import to_checksum_address

from .safe import selector

NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"   # KyberSwap's address for native ETH
UA = "argo-fund/1.0"


class ChainError(RuntimeError):
    pass


def http_json(url: str, data=None, headers=None, retries: int = 4, timeout: int = 30):
    hdrs = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=hdrs), timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise ChainError(f"HTTP {e.code} from {url.split('?')[0]}: {e.read()[:300].decode(errors='replace')}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise ChainError(f"network error for {url.split('?')[0]}: {e}") from None


class Chain:
    def __init__(self, rpc_url: str, chain_id: int, post=http_json, sleep=time.sleep):
        self.url, self.chain_id, self._post, self._sleep = rpc_url, chain_id, post, sleep
        self._decimals: dict[str, int] = {}
        self._symbols: dict[str, str] = {}

    def call(self, method: str, params: list):
        res = self._post(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in res:
            raise ChainError(f"RPC {method}: {res['error']}")
        return res["result"]

    def eth_call(self, to: str, data: str, frm: str | None = None, value: int = 0) -> str:
        tx = {"to": to, "data": data}
        if frm:
            tx["from"] = frm
        if value:
            tx["value"] = hex(value)
        return self.call("eth_call", [tx, "latest"])

    def view(self, to: str, signature: str, out_types: list[str], arg_types=(), args=()):
        data = "0x" + (selector(signature) + encode(list(arg_types), list(args))).hex()
        return decode(out_types, bytes.fromhex(self.eth_call(to, data)[2:]))

    def balance(self, token: str, owner: str) -> int:
        if token.lower() == NATIVE.lower():
            return int(self.call("eth_getBalance", [owner, "latest"]), 16)
        return self.view(token, "balanceOf(address)", ["uint256"], ["address"], [owner])[0]

    def decimals(self, token: str) -> int:
        t = token.lower()
        if t == NATIVE.lower():
            return 18
        if t not in self._decimals:
            self._decimals[t] = self.view(token, "decimals()", ["uint8"])[0]
        return self._decimals[t]

    def symbol(self, token: str) -> str:
        t = token.lower()
        if t == NATIVE.lower():
            return "ETH"
        if t not in self._symbols:
            try:
                self._symbols[t] = self.view(token, "symbol()", ["string"])[0][:16]
            except Exception:  # noqa: BLE001 (bytes32 symbols, odd tokens)
                self._symbols[t] = token[:8]
        return self._symbols[t]

    def send(self, key: str, frm: str, to: str, data: str, value: int = 0, wait_s: int = 180) -> dict:
        """Sign an EIP-1559 tx with the agent key, broadcast, wait for the receipt. Raises on revert or timeout."""
        frm = to_checksum_address(frm)
        nonce = int(self.call("eth_getTransactionCount", [frm, "pending"]), 16)
        gas = int(int(self.call("eth_estimateGas", [{"from": frm, "to": to, "data": data, "value": hex(value)}]), 16) * 1.3)
        base = int(self.call("eth_getBlockByNumber", ["latest", False])["baseFeePerGas"], 16)
        prio = int(self.call("eth_maxPriorityFeePerGas", []), 16)
        signed = Account.sign_transaction({
            "type": 2, "chainId": self.chain_id, "nonce": nonce, "to": to_checksum_address(to), "value": value,
            "data": data, "gas": gas, "maxFeePerGas": base * 2 + prio, "maxPriorityFeePerGas": prio}, key)
        txh = self.call("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex().removeprefix("0x")])
        for _ in range(max(1, wait_s // 2)):
            rcpt = self.call("eth_getTransactionReceipt", [txh])
            if rcpt:
                if int(rcpt["status"], 16) != 1:
                    raise ChainError(f"transaction reverted: {txh}")
                return rcpt
            self._sleep(2)
        raise ChainError(f"no receipt after {wait_s}s: {txh}")
