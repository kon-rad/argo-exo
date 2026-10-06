import pytest
from exo_bridge.config import Config

BASE = {"EXO_BRIDGE_TOKEN": "t" * 40, "EXO_BRIDGE_HOST": "100.64.0.5", "API_SERVER_KEY": "k"}  # public-ok


def test_valid_config():
    c = Config.from_env(BASE)
    assert c.port == 8765 and c.board == "exo" and "researcher" in c.agents
    assert c.api_server_url == "http://127.0.0.1:8642" and c.max_runtime == "30m"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "[::]", "0:0:0:0:0:0:0:0", "0000:0000::", "not-an-ip"])
def test_refuses_wildcard_or_missing_host(host):
    with pytest.raises(ValueError, match="EXO_BRIDGE_HOST"):
        Config.from_env(dict(BASE, EXO_BRIDGE_HOST=host))


def test_refuses_short_token():
    with pytest.raises(ValueError, match="EXO_BRIDGE_TOKEN"):
        Config.from_env(dict(BASE, EXO_BRIDGE_TOKEN="short"))


def test_refuses_missing_api_key():
    with pytest.raises(ValueError, match="API_SERVER_KEY"):
        Config.from_env({k: v for k, v in BASE.items() if k != "API_SERVER_KEY"})


def test_agents_default_is_exact():
    assert Config.from_env(BASE).agents == ("librarian", "trader", "portfolio", "wallet", "builder", "researcher")


@pytest.mark.parametrize("host", ["127.0.0.1", "100.64.0.5", "fd7a:115c:a1e0::1"], ids=["loopback", "v4", "v6"])  # public-ok
def test_allows_loopback_and_real_addresses(host):
    assert Config.from_env(dict(BASE, EXO_BRIDGE_HOST=host)).host == host
