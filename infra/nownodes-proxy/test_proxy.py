import json
import threading
import urllib.error
import urllib.request

import pytest
import nownodes_proxy as np


def serve(forward):
    srv = np.make_server("127.0.0.1", 0, forward)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_forwards_by_chain_alias():
    seen = []
    srv, port = serve(lambda chain, body: (seen.append((chain, body)) or (200, b'{"result":"0x1"}')))
    assert post(port, "/eth", b'{"id":1}') == (200, b'{"result":"0x1"}')
    assert post(port, "/base", b"{}")[0] == 200
    assert post(port, "/ethereum", b"{}")[0] == 200
    assert seen == [("ethereum", b'{"id":1}'), ("base", b"{}"), ("ethereum", b"{}")]
    srv.shutdown()


def raw(port, path, body=b"{}", headers=None):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    return r.status, r.read()


def test_browser_defences():
    seen = []
    srv, port = serve(lambda c, b: (seen.append(b) or (200, b"{}")))
    J = "application/json"
    assert raw(port, "/eth", headers={"Host": "evil.com", "Content-Type": J})[0] == 403
    assert raw(port, "/eth", headers={"Host": f"127.0.0.1:{port + 1}", "Content-Type": J})[0] == 403
    assert raw(port, "/eth", headers={"Host": f"127.0.0.1:{port}", "Origin": "http://evil.com", "Content-Type": J})[0] == 403
    assert raw(port, "/eth", headers={"Host": f"127.0.0.1:{port}", "Content-Type": "text/plain"})[0] == 415
    assert seen == []
    for h in (f"127.0.0.1:{port}", "127.0.0.1", f"localhost:{port}", f"[::1]:{port}"):
        assert raw(port, "/eth", headers={"Host": h, "Content-Type": "application/json; charset=utf-8"})[0] == 200
    srv.shutdown()


def test_path_handling():
    srv, port = serve(lambda c, b: (200, b"{}"))
    assert post(port, "/eth?x=1", b"{}")[0] == 200
    assert post(port, "/eth/extra", b"{}")[0] == 404
    assert post(port, "/", b"{}")[0] == 404
    srv.shutdown()


def test_unknown_chain_404():
    srv, port = serve(lambda c, b: (200, b""))
    assert post(port, "/solana", b"{}")[0] == 404
    srv.shutdown()


def test_refuses_non_loopback_bind():
    with pytest.raises(ValueError):
        np.make_server("0.0.0.0", 0, lambda c, b: (200, b""))


def test_oversize_body_413_and_not_forwarded():
    seen = []
    srv, port = serve(lambda c, b: (seen.append(b) or (200, b"{}")))
    assert post(port, "/eth", b"x" * (np.MAX_BODY + 1))[0] == 413
    assert seen == []
    srv.shutdown()


def test_forward_failure_is_502_without_leaking_key():
    def boom(chain, body):
        raise RuntimeError("secret-key-123 exploded")
    srv, port = serve(boom)
    status, out = post(port, "/eth", b"{}")
    assert status == 502 and b"secret-key-123" not in out
    srv.shutdown()


def test_nownodes_forward_sends_api_key_and_redacts(monkeypatch):
    monkeypatch.setenv("NOWNODES_API_KEY", "secret-key-123")
    captured = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"echo":"secret-key-123"}'

    def fake_open(req, timeout):
        captured.update(url=req.full_url, key=req.get_header("Api-key"), timeout=timeout)
        return Resp()

    monkeypatch.setattr(np.urllib.request, "urlopen", fake_open)
    status, out = np.nownodes_forward("polygon", b"{}")
    assert captured["url"] == "https://matic.nownodes.io" and captured["key"] == "secret-key-123"
    assert captured["timeout"] and b"secret-key-123" not in out


def test_nownodes_forward_missing_key(monkeypatch):
    monkeypatch.delenv("NOWNODES_API_KEY", raising=False)
    status, out = np.nownodes_forward("base", b"{}")
    assert status == 503 and b"NOWNODES_API_KEY" in out


def test_public_redact_helper():
    from exo_nownodes.rpc import redact
    assert redact("a k1 b", "k1") == "a *** b" and redact("a", "") == "a"
