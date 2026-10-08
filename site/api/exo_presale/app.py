"""Same-origin sale API behind Caddy. Personal data is never logged and never echoed back."""
from __future__ import annotations

import ipaddress
import logging
import re
import threading
import time
import unicodedata
from collections import defaultdict, deque

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .chain import SaleUnavailable
from .claims import ClaimError, verify_claim
from .waitlist import WAITLIST_EMAIL_RE

log = logging.getLogger("exo-presale")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
SIG_RE = re.compile(r"^0x[0-9a-fA-F]{130}$")
MAX_BODY = 4096
MAX_DEVICE = 2 ** 32
MAX_TIME = 2 ** 40
WINDOW_S = 3600
MAX_BUCKETS = 10_000
WAITLIST_RATE = 60
# Cc control, Cs surrogate, Zl/Zp line/paragraph separators, Cf format (bidi overrides, zero-width joiners, BOM):
# none belong in a name or an address label, and Cf ones can make an export row read differently from what it holds.
BAD_CATEGORIES = ("Cc", "Cs", "Zl", "Zp", "Cf")
FIXED = {400: "bad request", 404: "not found", 405: "method not allowed", 413: "request too large",
         415: "unsupported media type", 500: "internal error"}


def _uint(v, lo: int, hi: int) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, str) and v.isascii() and v.isdigit() and len(v) <= 15:
        v = int(v)
    return v if isinstance(v, int) and lo <= v < hi else None


def _clean(v, max_len: int) -> str | None:
    """The string exactly as sent, 1..max_len long, with no leading/trailing whitespace and no control, surrogate,
    separator or format characters; else None. Never stripped here: the signature covers the exact bytes, and the
    page (wallet.mjs claimFields) trims and rejects the same way before hashing, so a field that would need
    normalising is refused rather than saved differently from what was signed (e.g. a trailing \x1c, which Python's
    strip() removes but JS trim() keeps)."""
    if not isinstance(v, str) or v != v.strip():
        return None
    if not 1 <= len(v) <= max_len or any(unicodedata.category(c) in BAD_CATEGORIES for c in v):
        return None
    return v


def _loopback(addr: str | None) -> bool:
    try:
        return ipaddress.ip_address(addr or "").is_loopback
    except ValueError:
        return False


def rate_key(addr: str) -> str:
    """One bucket per IPv4 address, and per /64 for IPv6 (one host usually holds a whole /64)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return addr
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def create_app(sale, store, countries: tuple, rate_per_hour: int = 10, clock=time.time,
               max_buckets: int = MAX_BUCKETS, waitlist=None, waitlist_rate_per_hour: int = WAITLIST_RATE) -> Flask:
    app = Flask("exo-presale")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    countries = tuple(countries)
    hits: dict[str, deque] = defaultdict(deque)
    app.config["EXO_RATE_BUCKETS"] = hits
    hits_lock = threading.Lock()
    # The waitlist gets its own, looser buckets: a conference hall shares one IP, and signing up there
    # must not use up the shipping-claim quota (or the other way round).
    wl_hits: dict[str, deque] = defaultdict(deque)

    def client_ip() -> str:
        # Trust X-Forwarded-For only from the local Caddy, and only its rightmost hop (the address Caddy saw);
        # anything to the left is whatever the client sent.
        if _loopback(request.remote_addr):
            xff = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            if xff:
                return xff
        return request.remote_addr or "?"

    def limited(hits=hits, rate=rate_per_hour) -> bool:
        now, ip = clock(), rate_key(client_ip())
        with hits_lock:
            for k in [k for k, q in hits.items() if not q or q[-1] < now - WINDOW_S]:
                del hits[k]                       # keep the table from growing without bound
            if ip not in hits and len(hits) >= max_buckets:
                # Full (many sources inside one hour): drop the least recently seen bucket rather than refuse
                # everyone new. Worst case an attacker resets someone else's quota; it never locks buyers out.
                del hits[min(hits, key=lambda k: hits[k][-1])]
            q = hits[ip]
            while q and q[0] < now - WINDOW_S:
                q.popleft()
            if len(q) >= rate:
                return True
            q.append(now)
            return False

    def err(code: int, msg: str):
        return jsonify(error=msg), code

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return err(exc.code or 500, FIXED.get(exc.code, "error"))

    @app.errorhandler(Exception)
    def any_error(exc):
        log.error("unhandled %s", type(exc).__name__)   # type only: messages could carry submitted values
        return err(500, FIXED[500])

    @app.get("/api/sale")
    def sale_state():
        try:
            return jsonify(sale.state())
        except SaleUnavailable:
            log.warning("sale state unavailable")
            return err(503, "sale state unavailable")

    @app.post("/api/claims")
    def claims():
        b = request.get_json(silent=True)
        if not isinstance(b, dict):
            log.info("claim device=? outcome=bad-json")
            return err(400, "check: body")
        n = _uint(b.get("device_number"), 1, MAX_DEVICE)
        at = _uint(b.get("signed_at"), 1, MAX_TIME)
        name, email = _clean(b.get("name"), 80), _clean(b.get("email"), 120)
        country, sig = b.get("country"), b.get("signature")
        bad = []
        if name is None: bad.append("name")
        if email is None or not EMAIL_RE.match(email): bad.append("email")
        if not isinstance(country, str) or country not in countries: bad.append("country")
        if n is None: bad.append("device_number")
        if at is None or not isinstance(sig, str) or not SIG_RE.match(sig): bad.append("signature")
        dev = n if n is not None else "?"
        if bad:
            log.info("claim device=%s outcome=invalid fields=%s", dev, ",".join(bad))
            return err(400, "check: " + ", ".join(bad))
        if limited():
            log.info("claim device=%s outcome=rate-limited", dev)
            return err(429, "too many requests; try again later")
        body = {"device_number": n, "signed_at": at, "name": name, "email": email, "country": country, "signature": sig}
        try:
            owner = verify_claim(body, sale.owner_of, int(clock()))
        except ClaimError as exc:
            log.info("claim device=%s outcome=rejected", dev)
            return err(403, str(exc))
        except SaleUnavailable:
            log.warning("claim device=%s outcome=chain-unavailable", dev)
            return err(503, "could not check the receipt right now; try again shortly")
        if not store.save(n, owner, name, email, country, at):
            log.info("claim device=%s outcome=stale", dev)
            return err(409, "a newer claim for this receipt is already saved; sign again")
        log.info("claim device=%s outcome=saved", dev)
        return jsonify(ok=True)

    @app.post("/api/waitlist")
    def join_waitlist():
        if waitlist is None:
            return err(404, FIXED[404])
        b = request.get_json(silent=True)
        email = _clean(b.get("email"), 120) if isinstance(b, dict) else None
        if email is None or not WAITLIST_EMAIL_RE.match(email):
            log.info("waitlist outcome=invalid")
            return err(400, "check: email")
        if limited(wl_hits, waitlist_rate_per_hour):
            log.info("waitlist outcome=rate-limited")
            return err(429, "too many requests; try again later")
        added = waitlist.add(email)
        log.info("waitlist outcome=%s", "added" if added else "already")
        return jsonify(ok=True)   # same answer either way: the page never reveals who is already on the list

    return app
