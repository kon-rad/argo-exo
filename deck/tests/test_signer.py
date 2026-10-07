import json
import os

import pytest
from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes
from exo_deck.signer import FeeTooHigh, HotSigner, SignerLocked, build_tx, execute_calldata, freeze_calldata


def keystore(tmp_path, pw="pw", mode=0o600):
    acct = Account.create()
    ks = tmp_path / "hot.json"
    ks.write_text(json.dumps(Account.encrypt(acct.key, pw, kdf="pbkdf2", iterations=2)))   # pbkdf2, cheap for tests
    pf = tmp_path / "pass"
    pf.write_text(pw + "\n")
    os.chmod(pf, mode)
    return acct, ks, pf


def test_calldata_reexported():
    assert execute_calldata("0x" + "22" * 20, 0, b"", b"\x00" * 32)[:4].hex() == "88aa4c12"
    assert freeze_calldata().hex() == "62a5af3b"


def test_keystore_roundtrip_and_sign(tmp_path):
    acct, ks, pf = keystore(tmp_path)
    s = HotSigner(ks, pf)
    assert s.address == acct.address
    raw = s.sign({"chainId": 1, "nonce": 0, "to": "0x" + "11" * 20, "value": 0, "data": b"", "gas": 21000,
                  "maxFeePerGas": 2, "maxPriorityFeePerGas": 1, "type": 2})
    assert raw[:1] == b"\x02"
    tx = TypedTransaction.from_bytes(HexBytes(raw))
    assert Account.recover_transaction(raw) == acct.address and tx.as_dict()["chainId"] == 1


def test_locked_when_passfile_missing_wrong_or_loose(tmp_path):
    acct, ks, pf = keystore(tmp_path)
    with pytest.raises(SignerLocked):
        HotSigner(ks, tmp_path / "nope")
    pf.write_text("wrong-pass\n")
    with pytest.raises(SignerLocked) as e:
        HotSigner(ks, pf)
    assert "wrong-pass" not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__
    pf.write_text("pw\n")
    os.chmod(pf, 0o644)                       # group/world-readable passphrase: refuse
    with pytest.raises(SignerLocked):
        HotSigner(ks, pf)


def test_key_not_in_repr(tmp_path):
    acct, ks, pf = keystore(tmp_path)
    s = HotSigner(ks, pf)
    assert acct.key.hex() not in repr(s) and acct.key.hex() not in repr(vars(s))


class R:
    def __init__(self, **over):
        self.r = {"eth_getTransactionCount": "0x5", "eth_estimateGas": "0x186a0",
                  "eth_maxPriorityFeePerGas": "0x3b9aca00",
                  "eth_getBlockByNumber": {"baseFeePerGas": "0x2540be400"}, "eth_chainId": "0x1"}
        self.r.update(over)
        self.calls = []

    def call(self, m, p):
        self.calls.append((m, p))
        return self.r[m]


def test_build_tx_fees_and_gas():
    rpc = R()
    tx = build_tx(rpc, "0x" + "aa" * 20, "0x" + "bb" * 20, b"\x01")
    assert tx["nonce"] == 5 and tx["gas"] == 120000 and tx["maxPriorityFeePerGas"] == 10**9
    assert tx["maxFeePerGas"] == 2 * 10**10 + 10**9 and tx["chainId"] == 1 and tx["type"] == 2
    assert tx["value"] == 0 and "from" not in tx and all(isinstance(tx[k], int) for k in
                                                          ("nonce", "gas", "maxFeePerGas", "maxPriorityFeePerGas"))
    assert ("eth_getTransactionCount", ["0x" + "aa" * 20, "pending"]) in rpc.calls
    est = [p for m, p in rpc.calls if m == "eth_estimateGas"][0][0]
    assert est["from"] == "0x" + "aa" * 20 and est["data"] == "0x01" and est["value"] == "0x0"


def test_build_tx_refuses_absurd_fees_and_gas():
    with pytest.raises(FeeTooHigh):
        build_tx(R(eth_getBlockByNumber={"baseFeePerGas": hex(10**13)}), "0x" + "aa" * 20, "0x" + "bb" * 20, b"")
    with pytest.raises(FeeTooHigh):
        build_tx(R(eth_estimateGas=hex(5_000_000)), "0x" + "aa" * 20, "0x" + "bb" * 20, b"")


def test_build_tx_rejects_malformed_numbers():
    with pytest.raises(ValueError):
        build_tx(R(eth_getTransactionCount=None), "0x" + "aa" * 20, "0x" + "bb" * 20, b"")
    with pytest.raises(ValueError):
        build_tx(R(eth_getBlockByNumber={}), "0x" + "aa" * 20, "0x" + "bb" * 20, b"")
