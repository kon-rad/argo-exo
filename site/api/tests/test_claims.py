import os
import stat

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from exo_presale.claims import ClaimError, ClaimStore, claim_message, details_hash, verify_claim

acct = Account.create()


def signed(n=42, name="Ada", email="ada@example.com", country="Japan", at=1000, key=acct.key):  # public-ok
    msg = claim_message(n, details_hash(name, email, country), at)
    sig = Account.sign_message(encode_defunct(text=msg), key).signature.hex()
    return {"device_number": n, "name": name, "email": email, "country": country, "signed_at": at, "signature": "0x" + sig.removeprefix("0x")}


def test_message_format_exact():
    assert claim_message(7, "ab" * 32, 1700000000) == (
        "Argo Exo pre-order No. 7\nShipping details: " + "ab" * 32 + "\nSigned at: 1700000000")
    h = "efa51c856d09880b33cea2f1ab697f9767d4bfbc51ab42f0da3cbd7201b381ff"
    assert details_hash("Ada", "ada@example.com", "Japan") == h  # public-ok


def test_known_signature_recovers():
    # Fixed key (Hardhat/anvil account #0, public test key) so the JS side can reproduce this vector.
    key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    addr = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    body = signed(n=1, at=1000, key=key)
    # Deterministic (RFC 6979): Task 5's personal_sign must produce exactly this over the same message.
    assert body["signature"] == ("0x0ed5a8ce8ccfaa1f8f0ecbe262100614bca1e5395c80eb72f8d6e369eb3ee53e"
                                 "6886258c1b24977b62ad13141ff8be455d1d797ea952b27c98c98bcc6ef89f391b")
    assert verify_claim(body, lambda n: addr.lower(), now=1000) == addr


def test_valid_claim_returns_signer():
    assert verify_claim(signed(), lambda n: acct.address, now=1100) == acct.address


def test_claim_requires_current_owner():
    with pytest.raises(ClaimError, match="holder"):
        verify_claim(signed(), lambda n: "0x" + "cc" * 20, now=1100)
    with pytest.raises(ClaimError, match="no receipt"):
        verify_claim(signed(), lambda n: None, now=1100)
    # Signed for a different device number: recovers a different signer for this message.
    body = signed(n=43)
    body["device_number"] = 42
    with pytest.raises(ClaimError, match="holder"):
        verify_claim(body, lambda n: acct.address, now=1100)


def test_stale_signature_rejected():
    with pytest.raises(ClaimError, match="expired"):
        verify_claim(signed(at=1000), lambda n: acct.address, now=1000 + 601)
    assert verify_claim(signed(at=1000), lambda n: acct.address, now=1000 + 600) == acct.address
    # From the future beyond the clock skew allowance.
    with pytest.raises(ClaimError, match="expired"):
        verify_claim(signed(at=1000), lambda n: acct.address, now=1000 - 61)
    assert verify_claim(signed(at=1000), lambda n: acct.address, now=1000 - 60) == acct.address


def test_bad_signature_rejected():
    body = signed()
    body["signature"] = "0x" + "11" * 65
    with pytest.raises(ClaimError, match="signature"):
        verify_claim(body, lambda n: acct.address, now=1100)


def test_tampered_details_rejected():
    body = signed()
    body["country"] = "Singapore"
    with pytest.raises(ClaimError, match="holder"):
        verify_claim(body, lambda n: acct.address, now=1100)


def test_store_keeps_newest(tmp_path):
    st = ClaimStore(tmp_path / "c.db")
    assert st.save(42, acct.address, "Ada", "a@example.com", "Japan", 1000)  # public-ok
    assert not st.save(42, acct.address, "Old", "o@example.com", "Japan", 900)  # public-ok
    assert not st.save(42, acct.address, "Same", "s@example.com", "Japan", 1000)  # public-ok
    assert st.save(42, acct.address, "Ada L.", "a@example.com", "Japan", 1100)  # public-ok
    assert st.all()[0]["name"] == "Ada L."
    assert len(st.all()) == 1


def test_store_file_mode_600(tmp_path):
    p = tmp_path / "c.db"
    old = os.umask(0o022)
    try:
        st = ClaimStore(p)
        st.save(1, acct.address, "Ada", "a@example.com", "Japan", 1000)  # public-ok
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    # An existing file with loose permissions is tightened on open.
    os.chmod(p, 0o644)
    ClaimStore(p)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
