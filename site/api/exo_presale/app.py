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

log = logging.getLogger("exo-presale")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
SIG_RE = re.compile(r"^0x[0-9a-fA-F]{130}$")
MAX_BODY = 4096
MAX_DEVICE = 2 ** 32
MAX_TIME = 2 ** 40
WINDOW_S = 3600
FIXED = {400: "bad request", 404: "not found", 405: "method not allowed", 413: "request too large",
         415: "unsupported media type", 500: "internal error"}


def _uint(v, lo: int, hi: int) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, str) and v.isascii() and v.isdigit() and len(v) <= 15:
        v = int(v)
    return v if isinstance(v, int) and lo <= v < hi else None


def _clean(v, max_len: int) -> str | None:
    """Trimmed string without control/surrogate/line-separator characters, 1..max_len long; else None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not 1 <= len(v) <= max_len or any(unicodedata.category(c) in ("Cc", "Cs", "Zl", "Zp") for c in v):
        return None
    return v


def _loopback(addr: str | None) -> bool:
    try:
        return ipaddress.ip_address(addr or "").is_loopback
    except ValueError:
        return False


def create_app(sale, store, countries: tuple, rate_per_hour: int = 10, clock=time.time) -> Flask:
    app = Flask("exo-presale")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    countries = tuple(countries)
    hits: dict[str, deque] = defaultdict(deque)
    hits_lock = threading.Lock()

    def client_ip() -> str:
        # Trust X-Forwarded-For only from the local Caddy, and only its rightmost hop (the address Caddy saw);
        # anything to the left is whatever the client sent.
        if _loopback(request.remote_addr):
            xff = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            if xff:
                return xff
        return request.remote_addr or "?"

    def limited() -> bool:
        now, ip = clock(), client_ip()
        with hits_lock:
            for k in [k for k, q in hits.items() if not q or q[-1] < now - WINDOW_S]:
                del hits[k]                       # keep the table from growing without bound
            q = hits[ip]
            while q and q[0] < now - WINDOW_S:
                q.popleft()
            if len(q) >= rate_per_hour:
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

    return app
