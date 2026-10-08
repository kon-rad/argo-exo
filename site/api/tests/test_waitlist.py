import logging
from datetime import datetime, timezone

import pytest
from exo_presale.app import create_app
from exo_presale.claims import ClaimStore
from exo_presale.waitlist import HEADER, Waitlist
from test_presale_app import FakeSale

AT = datetime(2026, 10, 7, 14, 5, tzinfo=timezone.utc)


def make(tmp_path, rate=60, waitlist=True):
    wl = Waitlist(tmp_path / "waitlist.md", clock=lambda: AT) if waitlist else None
    app = create_app(FakeSale(), ClaimStore(tmp_path / "c.db"), countries=("Japan",), clock=lambda: 1100,
                     waitlist=wl, waitlist_rate_per_hour=rate)
    return app.test_client(), tmp_path / "waitlist.md"


def test_join_writes_one_markdown_line(tmp_path):
    c, md = make(tmp_path)
    assert c.post("/api/waitlist", json={"email": "ada@example.com"}).json == {"ok": True}  # public-ok
    assert md.read_text(encoding="utf-8") == HEADER + "- ada@example.com · 2026-10-07 14:05\n"  # public-ok


def test_duplicate_is_ok_but_not_written_twice(tmp_path):
    c, md = make(tmp_path)
    for e in ("ada@example.com", "ADA@example.com"):  # public-ok
        assert c.post("/api/waitlist", json={"email": e}).json == {"ok": True}
    assert md.read_text(encoding="utf-8").count("\n- ") == 1


@pytest.mark.parametrize("body", [
    {"email": "nope"}, {"email": ""}, {"email": " ada@example.com"}, {"email": "a|b@example.com"},  # public-ok
    {"email": "ada@example.com\n- evil@x.io"}, {"email": "[x](y)@example.com"}, {"email": 5}, {},  # public-ok
    ["ada@example.com"], {"email": ("a" * 65) + "@example.com"},  # public-ok
])
def test_bad_input_is_400_and_writes_nothing(tmp_path, body):
    c, md = make(tmp_path)
    r = c.post("/api/waitlist", json=body)
    assert r.status_code == 400 and r.json == {"error": "check: email"}
    assert not md.exists()


def test_rate_limited_separately_from_claims(tmp_path):
    c, md = make(tmp_path, rate=2)
    codes = [c.post("/api/waitlist", json={"email": f"u{i}@example.com"}).status_code for i in range(3)]  # public-ok
    assert codes == [200, 200, 429]
    assert md.read_text(encoding="utf-8").count("\n- ") == 2


def test_email_never_logged(tmp_path, caplog):
    c, _ = make(tmp_path)
    with caplog.at_level(logging.INFO):
        c.post("/api/waitlist", json={"email": "secret-person@example.com"})  # public-ok
    assert "secret-person" not in caplog.text and "waitlist outcome=added" in caplog.text


def test_no_waitlist_configured_is_404(tmp_path):
    c, _ = make(tmp_path, waitlist=False)
    assert c.post("/api/waitlist", json={"email": "ada@example.com"}).status_code == 404  # public-ok


def test_waitlist_refuses_bad_email_directly(tmp_path):
    with pytest.raises(ValueError):
        Waitlist(tmp_path / "w.md").add("not an email")
