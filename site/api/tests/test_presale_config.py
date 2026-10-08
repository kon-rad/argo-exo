import json

import pytest
from exo_presale import config

ADDR = "0x" + "ab" * 20


def write(tmp_path, countries=("Japan", "TODO(konrad)"), contract=None):
    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "tiers.json").write_text(json.dumps([{"tier": 1}, {"tier": 2}]))
    (tmp_path / "content" / "copy.json").write_text(json.dumps({"terms": {"countries": list(countries)}}))
    if contract is not None:
        (tmp_path / "preorder.json").write_text(json.dumps({"contract": contract}))
    return {"EXO_SITE_CONTENT": str(tmp_path / "content"), "EXO_PREORDER_JSON": str(tmp_path / "preorder.json")}


def test_load_defaults_and_todo_countries_dropped(tmp_path):
    c = config.load(write(tmp_path, contract=ADDR))
    assert c["contract"] == ADDR and c["tiers"] == [1, 2] and c["countries"] == ("Japan",)
    assert c["host"] == "127.0.0.1" and c["db"] == "/var/lib/exo-presale/claims.db"


def test_missing_or_zero_contract_means_not_deployed(tmp_path):
    assert int(config.load(write(tmp_path))["contract"], 16) == 0


def test_env_overrides_contract_and_rejects_garbage(tmp_path):
    env = write(tmp_path, contract=ADDR)
    assert config.load({**env, "EXO_PREORDER": "0x" + "cd" * 20})["contract"] == "0x" + "cd" * 20
    with pytest.raises(SystemExit):
        config.load({**env, "EXO_PREORDER": "nope"})


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.5", "example.org"])
def test_refuses_non_loopback_bind(tmp_path, host):
    with pytest.raises(SystemExit, match="loopback"):
        config.load({**write(tmp_path, contract=ADDR), "EXO_PRESALE_HOST": host})


def test_loopback_hosts_ok(tmp_path):
    env = write(tmp_path, contract=ADDR)
    for host in ("127.0.0.1", "::1", "localhost"):
        assert config.load({**env, "EXO_PRESALE_HOST": host})["host"] == host


def test_waitress_body_cap(monkeypatch, tmp_path):
    import exo_presale.__main__ as m
    seen = {}
    monkeypatch.setattr(m.config, "load", lambda: {"contract": "0x" + "00" * 20, "tiers": [1], "countries": (),
                                                  "db": str(tmp_path / "c.db"), "waitlist": str(tmp_path / "w.md"),
                                                  "rate": 10, "host": "127.0.0.1", "port": 5310})
    monkeypatch.setattr(m, "serve", lambda app, **kw: seen.update(kw))
    m.main()
    assert seen["max_request_body_size"] == 8192 and seen["host"] == "127.0.0.1"
