from exo_bridge.app import create_app
from exo_bridge.config import Config
from exo_bridge.fund_routes import fund_blueprint

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ENV = {"EXO_BRIDGE_TOKEN": TOKEN, "EXO_BRIDGE_HOST": "100.64.0.5", "API_SERVER_KEY": "k"}  # public-ok
SIG = "0x" + "ab" * 65


class Runner:
    def __init__(self, code=0, out=None):
        self.code, self.out, self.calls = code, out if out is not None else {"ok": True}, []

    def __call__(self, args):
        self.calls.append(args)
        return self.code, self.out


def client(run):
    return create_app(Config.from_env(ENV), None, None, blueprints=(fund_blueprint(run),)).test_client()


def test_every_route_needs_the_token():
    assert client(Runner()).get("/fund/proposals").status_code == 401


def test_unconfigured_is_503():
    assert client(None).get("/fund/proposals", headers=AUTH).status_code == 503


def test_routes_map_to_cli_args():
    r = Runner(out={"proposals": []})
    c = client(r)
    assert c.get("/fund/proposals", headers=AUTH).get_json() == {"proposals": []}
    c.get("/fund/log", headers=AUTH)
    c.post("/fund/proposals/2026-10-14-1/prepare", headers=AUTH, json={})
    c.post("/fund/proposals/2026-10-14-1/approve", headers=AUTH, json={"signature": SIG})
    c.post("/fund/proposals/2026-10-14-1/refuse", headers=AUTH, json={})
    assert r.calls == [["proposals"], ["log"], ["prepare", "2026-10-14-1"], ["execute", "2026-10-14-1", SIG],
                       ["refuse", "2026-10-14-1"]]


def test_bad_ids_and_signatures_never_reach_the_cli():
    r = Runner()
    c = client(r)
    assert c.post("/fund/proposals/--all/prepare", headers=AUTH, json={}).status_code in (400, 404)
    assert c.post("/fund/proposals/2026-10-14-1/approve", headers=AUTH, json={"signature": "0x12"}).status_code == 400
    assert c.post("/fund/proposals/2026-10-14-1/approve", headers=AUTH, json={"signature": SIG + " --x"}).status_code == 400
    assert r.calls == []


def test_cli_errors_are_409_with_the_message():
    c = client(Runner(code=1, out={"error": "the quote expired: prepare again"}))
    r = c.post("/fund/proposals/2026-10-14-1/prepare", headers=AUTH, json={})
    assert r.status_code == 409 and "expired" in r.get_json()["error"]
