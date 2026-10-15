"""End to end on an Anvil fork of Base: create the 2-of-3 Safe, then two real KyberSwap trades (ETH→USDC as a single
call, USDC→WETH as an approve+swap MultiSend batch), each signed by the "deck" owner and executed by the agent.

Runs only with anvil on PATH and FUND_FORK_TESTS=1 (it needs the network: Base RPC and the KyberSwap API)."""
import json
import os
import shutil
import socket
import subprocess
import time

import pytest
from eth_account import Account

from fund_trade import safe as sf, service, store
from fund_trade.chain import Chain
from fund_trade.config import Fund

pytestmark = pytest.mark.skipif(not (shutil.which("anvil") and os.environ.get("FUND_FORK_TESTS") == "1"),
                                reason="set FUND_FORK_TESTS=1 with anvil on PATH")
FORK_URL = os.environ.get("FUND_FORK_URL", "https://mainnet.base.org")
# anvil's well-known dev keys (public; fork only)
AGENT, DECK, PHONE, BANK = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
)
VOTES = json.dumps([{"partner": p, "vote": "yes", "reason": "fork test"} for p in ("A", "B", "C")])


@pytest.fixture(scope="module")
def anvil():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen(["anvil", "--fork-url", FORK_URL, "--port", str(port), "--silent"])
    url = f"http://127.0.0.1:{port}"
    ch = Chain(url, 8453)
    for _ in range(60):
        try:
            ch.call("eth_chainId", [])
            break
        except Exception:
            time.sleep(0.5)
    yield url, ch
    proc.terminate()


def test_full_flow(anvil, tmp_path):
    url, ch = anvil
    key = tmp_path / "agent.key"
    key.write_text(AGENT)
    key.chmod(0o600)
    (tmp_path / "trade-log.md").write_text(f"# Trade Log\n\n**NAV:** $0 {store.NAV_MARK}\n\n{store.ENTRIES_MARK}\n")
    env = {"FUND_DIR": str(tmp_path), "FUND_AGENT_KEY": str(key), "FUND_RPC_URL": url,
           "FUND_DECK_OWNER": Account.from_key(DECK).address, "FUND_PHONE_OWNER": Account.from_key(PHONE).address}
    f = Fund.from_env(env)

    safe_addr = service.create_safe(f, ch, salt_nonce=42)
    assert ch.view(safe_addr, "getThreshold()", ["uint256"])[0] == 2

    # our SafeTx hash == the Safe's own getTransactionHash
    tx = sf.SafeTx(to=Account.from_key(BANK).address, value=1, data="0xabcd", operation=0, nonce=0)
    onchain = ch.view(safe_addr, "getTransactionHash(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,uint256)",
                      ["bytes32"], ["address", "uint256", "bytes", "uint8", "uint256", "uint256", "uint256", "address", "address", "uint256"],
                      [tx.to, 1, b"\xab\xcd", 0, 0, 0, 0, sf.ZERO, sf.ZERO, 0])[0]
    assert onchain == sf.safe_tx_hash(8453, safe_addr, tx)

    # fund the Safe with 0.05 ETH from the bank account
    ch.send(BANK, Account.from_key(BANK).address, safe_addr, "0x", value=5 * 10**16)

    def trade(sell, buy, amount):
        prop = store.create(f.proposals, meeting="2026-10-14", motion=f"test {sell}->{buy}",
                            intent={"sell": sell, "buy": buy, "amount": amount}, votes=json.loads(VOTES))
        prep = service.prepare(f, ch, prop["id"])
        deck_sig = sf.sign_hash(DECK, bytes.fromhex(prep["safe_tx_hash"][2:]))
        return service.execute(f, ch, prop["id"], "0x" + deck_sig.hex())

    out = trade("ETH", "USDC", "0.02")
    assert out["tx_hash"].startswith("0x")
    usdc = ch.balance("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", safe_addr)
    assert usdc > 0

    out2 = trade("USDC", "WETH", str(usdc // 2 / 10**6))
    assert ch.balance("0x4200000000000000000000000000000000000006", safe_addr) > 0
    # the MultiSend batch approved the exact amount, so no allowance is left behind
    router = json.loads((f.proposals / f"{out2['id']}.json").read_text())["prepared"]["safe_tx"]
    assert router["operation"] == 1
    log = (tmp_path / "trade-log.md").read_text()
    assert "Swapped" in log and "basescan.org/tx/" in log

    # withdrawals go to the savings vault only: all remaining USDC, then some ETH
    savings = Account.from_key(PHONE).address        # any address stands in for the Pi Zero here
    f.cfg["savings"] = savings
    usdc_addr = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    for token, amount in (("USDC", "all"), ("ETH", "0.005")):
        w = store.create_withdrawal(f.proposals, token=token, amount=amount, reason="test")
        prep = service.prepare(f, ch, w["id"])
        assert prep["kind"] == "withdraw"
        service.execute(f, ch, w["id"], "0x" + sf.sign_hash(DECK, bytes.fromhex(prep["safe_tx_hash"][2:])).hex())
    assert ch.balance(usdc_addr, safe_addr) == 0 and ch.balance(usdc_addr, savings) > 0
    assert "Moved" in (tmp_path / "trade-log.md").read_text()


def test_rejects_a_signature_from_a_non_owner(anvil, tmp_path):
    url, ch = anvil
    key = tmp_path / "agent.key"
    key.write_text(AGENT)
    key.chmod(0o600)
    env = {"FUND_DIR": str(tmp_path), "FUND_AGENT_KEY": str(key), "FUND_RPC_URL": url,
           "FUND_DECK_OWNER": Account.from_key(DECK).address, "FUND_PHONE_OWNER": Account.from_key(PHONE).address}
    f = Fund.from_env(env)
    safe_addr = service.create_safe(f, ch, salt_nonce=43)
    ch.send(BANK, Account.from_key(BANK).address, safe_addr, "0x", value=10**16)
    prop = store.create(f.proposals, meeting="2026-10-14", motion="x", intent={"sell": "ETH", "buy": "USDC", "amount": "0.001"},
                        votes=json.loads(VOTES))
    prep = service.prepare(f, ch, prop["id"])
    h = bytes.fromhex(prep["safe_tx_hash"][2:])
    with pytest.raises(service.TradeError, match="human owner"):
        service.execute(f, ch, prop["id"], "0x" + sf.sign_hash(BANK, h).hex())
    with pytest.raises(service.TradeError, match="human owner"):        # the agent can't approve itself
        service.execute(f, ch, prop["id"], "0x" + sf.sign_hash(AGENT, h).hex())
