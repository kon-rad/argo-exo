# Named test_presale_app.py, not test_app.py: agents/bridge/tests/test_app.py already owns that module name.
import json
import logging

from eth_account import Account
from exo_presale.app import create_app
from exo_presale.chain import SaleUnavailable
from exo_presale.claims import ClaimStore
from test_claims import acct, signed


class FakeSale:
    def __init__(self, fail=False):
        self.fail, self.owner_calls = fail, 0

    def state(self):
        if self.fail:
            raise SaleUnavailable("boom secret-detail")
        return {"open": True, "paused": False, "minted": 1, "max_supply": 500, "tiers": []}

    def owner_of(self, n):
        self.owner_calls += 1
        if self.fail:
            raise SaleUnavailable("boom secret-detail")
        return acct.address


def make(tmp_path, rate=3, now=1100, sale=None):
    store = ClaimStore(tmp_path / "c.db")
    app = create_app(sale or FakeSale(), store, countries=("Japan",), rate_per_hour=rate, clock=lambda: now)
    return app, store


def client(tmp_path, **kw):
    return make(tmp_path, **kw)[0].test_client()


def test_sale(tmp_path):
    assert client(tmp_path).get("/api/sale").json["open"] is True


def test_sale_unavailable_is_fixed_503(tmp_path):
    r = client(tmp_path, sale=FakeSale(fail=True)).get("/api/sale")
    assert r.status_code == 503 and r.json == {"error": "sale state unavailable"}


def test_claim_ok_and_bad_input(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/claims", json=signed()).json == {"ok": True}
    for patch in ({"country": "Atlantis"}, {"email": "nope"}, {"name": ""}, {"device_number": "x"}):
        assert c.post("/api/claims", json={**signed(), **patch}).status_code == 400


def test_more_bad_input_is_400(tmp_path):
    c = client(tmp_path, rate=100)
    patches = [
        {"name": "A" * 81}, {"name": "Ada\x00"}, {"name": "Ada\nEvil"}, {"email": "a\u0007@example.com"},  # public-ok
        {"email": ("a" * 120) + "@example.com"}, {"country": ["Japan"]}, {"name": 5},  # public-ok
        {"device_number": 0}, {"device_number": -1}, {"device_number": True}, {"device_number": "²"},
        {"device_number": 2 ** 40}, {"signed_at": "1e3"}, {"signed_at": None},
        {"signature": "0x1234"}, {"signature": "zz" * 66},
    ]
    for patch in patches:
        r = c.post("/api/claims", json={**signed(), **patch})
        assert r.status_code == 400, patch
        assert set(r.json) == {"error"}
    assert c.post("/api/claims", data="[1,2]", content_type="application/json").status_code == 400
    assert c.post("/api/claims", data="not json", content_type="application/json").status_code == 400
    assert c.post("/api/claims", data=json.dumps(signed()), content_type="text/plain").status_code == 400


def test_body_size_limit(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/claims", data=json.dumps({**signed(), "pad": "x" * 10_000}), content_type="application/json")
    assert r.status_code == 413 and set(r.json) == {"error"}


def test_claim_wrong_signer_is_403(tmp_path):
    other = Account.create()
    r = client(tmp_path).post("/api/claims", json=signed(key=other.key))
    assert r.status_code == 403 and "holder" in r.json["error"]


def test_replay_is_409(tmp_path):
    c = client(tmp_path, rate=10)
    assert c.post("/api/claims", json=signed(at=1000)).status_code == 200
    assert c.post("/api/claims", json=signed(at=1000)).status_code == 409
    assert c.post("/api/claims", json=signed(at=999)).status_code == 409
    assert c.post("/api/claims", json=signed(at=1001, name="Ada L.")).status_code == 200


def test_owner_lookup_unavailable_is_503_and_nothing_stored(tmp_path):
    app, store = make(tmp_path, sale=FakeSale(fail=True))
    r = app.test_client().post("/api/claims", json=signed())
    assert r.status_code == 503 and "secret" not in r.get_data(as_text=True)
    assert store.all() == []


def test_rate_limit(tmp_path):
    c = client(tmp_path, rate=2)
    codes = [c.post("/api/claims", json=signed()).status_code for _ in range(3)]
    assert codes[-1] == 429


def test_rate_limit_window_slides(tmp_path):
    now = [1100]
    app = create_app(FakeSale(), ClaimStore(tmp_path / "c.db"), countries=("Japan",), rate_per_hour=1, clock=lambda: now[0])
    c = app.test_client()
    assert c.post("/api/claims", json=signed(at=1100)).status_code == 200
    assert c.post("/api/claims", json=signed(at=1100)).status_code == 429
    now[0] += 3601
    assert c.post("/api/claims", json=signed(at=now[0])).status_code == 200


def test_forwarded_for_trusted_only_from_loopback(tmp_path):
    c = client(tmp_path, rate=1)
    post = lambda ip, xff: c.post("/api/claims", json=signed(), environ_base={"REMOTE_ADDR": ip},
                                  headers={"X-Forwarded-For": xff} if xff else {})
    # Behind the local Caddy: distinct clients get separate buckets (rightmost hop is what Caddy saw).
    assert post("127.0.0.1", "203.0.113.1").status_code == 200
    assert post("127.0.0.1", "203.0.113.2").status_code != 429
    assert post("127.0.0.1", "203.0.113.1").status_code == 429
    # A client spoofing a leftmost entry does not escape its bucket.
    assert post("127.0.0.1", "198.51.100.9, 203.0.113.1").status_code == 429
    # Not from loopback: the header is ignored, the socket address is the key.
    assert post("192.0.2.5", "203.0.113.50").status_code != 429
    assert post("192.0.2.5", "203.0.113.51").status_code == 429


def test_no_personal_data_in_logs_or_responses(tmp_path, caplog):
    c = client(tmp_path, rate=100)
    caplog.set_level(logging.DEBUG)
    bodies = []
    body = signed(name="Zelda Quux", email="zq@example.com")  # public-ok
    for b in (body, body, {**body, "country": "Atlantis"}, signed(name="Zelda Quux", key=Account.create().key)):
        bodies.append(c.post("/api/claims", json=b).get_data(as_text=True))
    text = caplog.text + "".join(bodies)
    assert "Zelda" not in text and "zq@" not in text and "Japan" not in text and "Atlantis" not in text
    assert "device=42" in caplog.text


def test_no_cors_headers(tmp_path):
    r = client(tmp_path).get("/api/sale", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_unknown_route_fixed_json(tmp_path):
    r = client(tmp_path).get("/api/nope")
    assert r.status_code == 404 and r.json == {"error": "not found"}


def test_unicode_format_chars_rejected(tmp_path):
    c = client(tmp_path, rate=100)
    for patch in ({"name": "Ada\u202eevil"}, {"name": "A\u200bda"}, {"email": "a\u200d@example.com"},  # public-ok
                  {"name": "\ufeffAda"}, {"name": "Ada\u2066x"}):
        r = c.post("/api/claims", json={**signed(), **patch})
        assert r.status_code == 400, patch


def test_ipv6_rate_limit_keyed_by_64(tmp_path):
    c = client(tmp_path, rate=1)
    post = lambda xff: c.post("/api/claims", json=signed(), environ_base={"REMOTE_ADDR": "127.0.0.1"},
                              headers={"X-Forwarded-For": xff})
    assert post("2001:db8:1:2::1").status_code == 200
    assert post("2001:db8:1:2:ffff::9").status_code == 429       # same /64, new address: same bucket
    assert post("2001:db8:1:3::1").status_code != 429            # next /64: its own bucket
    assert post("::ffff:203.0.113.7").status_code != 429         # v4-mapped stays a single v4 address
    assert post("203.0.113.7").status_code == 429


def test_bucket_table_is_capped(tmp_path):
    from exo_presale import app as appmod
    now = [1100]
    store = ClaimStore(tmp_path / "c.db")
    a = create_app(FakeSale(), store, countries=("Japan",), rate_per_hour=1, clock=lambda: now[0], max_buckets=3)
    c = a.test_client()
    post = lambda ip: c.post("/api/claims", json=signed(at=1100), environ_base={"REMOTE_ADDR": "127.0.0.1"},
                             headers={"X-Forwarded-For": ip}).status_code
    for i in range(10):
        post(f"198.51.100.{i}")  # public-ok
        now[0] += 1
    assert len(a.config["EXO_RATE_BUCKETS"]) <= 3
    assert post("198.51.100.9") == 429                         # the newest bucket survived eviction  # public-ok


def test_untrimmed_or_separator_fields_are_rejected_not_stripped(tmp_path):
    """The signature covers the exact fields: the API refuses anything it would have had to normalise. \\x1c-\\x1f
    are whitespace to Python's strip() but not to JS trim(), so stripping here would save something other than what
    the page hashed."""
    c = client(tmp_path, rate=100)
    for patch in ({"name": " Ada"}, {"name": "Ada "}, {"name": "Ada\x1c"}, {"name": "\x1fAda"}, {"name": "Ada\t"},
                  {"email": "ada@example.com\n"}, {"email": "\u00a0ada@example.com"},  # public-ok
                  {"name": "Ada\u3000"}, {"name": "Ada\x85"}):
        r = c.post("/api/claims", json={**signed(), **patch})
        assert r.status_code == 400, patch
    assert c.post("/api/claims", json=signed(name="Ada L.")).status_code == 200   # an inner space is fine
