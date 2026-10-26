import pytest
from exo_bridge.config import Config

BASE = {"EXO_BRIDGE_TOKEN": "t" * 40, "EXO_BRIDGE_HOST": "100.64.0.5", "API_SERVER_KEY": "k"}  # public-ok


def test_valid_config():
    c = Config.from_env(BASE)
    assert c.port == 8765 and c.board == "exo" and "researcher" in c.agents
    assert c.api_server_url == "http://127.0.0.1:8642" and c.agent_map == {"researcher": "vega", "librarian": "vega", "builder": "rune"}
    assert c.cli_board_bin == "cli-board" and c.notify_cmd == ""


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
    assert Config.from_env(BASE).agents == ("librarian", "builder", "researcher",
                                            "cc-coder", "cc-research", "cc-writer", "cc-reviewer")


def test_agent_map_default_targets_exist_on_the_roster_names():
    assert set(Config.from_env(BASE).agent_map.values()) <= {"vega", "tau", "rune"}
    assert Config.from_env(dict(BASE, BRIDGE_AGENT_MAP="researcher=tau")).agent_map == {"researcher": "tau"}


@pytest.mark.parametrize("raw", ["researcher", "=vega", "researcher=", "trader=vega", "researcher=wallet"])
def test_bad_agent_map_is_refused(raw):
    with pytest.raises(ValueError, match="BRIDGE_AGENT_MAP"):
        Config.from_env(dict(BASE, BRIDGE_AGENT_MAP=raw))


def test_default_cc_lanes_match_lanes_toml():
    # Voice delegation to Claude Code lanes: the bridge's list must name exactly the lanes cc-dispatcher runs.
    import tomllib
    from pathlib import Path
    lanes = tomllib.loads((Path(__file__).resolve().parents[2] / "cc-lanes/lanes.toml").read_text())["lanes"]
    assert {a for a in Config.from_env(BASE).agents if a.startswith("cc-")} == set(lanes)


@pytest.mark.parametrize("host", ["127.0.0.1", "100.64.0.5", "fd7a:115c:a1e0::1"], ids=["loopback", "v4", "v6"])  # public-ok
def test_allows_loopback_and_real_addresses(host):
    assert Config.from_env(dict(BASE, EXO_BRIDGE_HOST=host)).host == host


def test_guardian_setup_error_names_the_missing_module():
    from exo_bridge.__main__ import guardian_setup_error
    e = ModuleNotFoundError("No module named 'eth_abi'", name="eth_abi")
    assert guardian_setup_error(e) == ("guardian setup failed: ModuleNotFoundError: cannot import eth_abi "
                                       "(install agents/bridge/requirements.txt into the bridge venv)")
    e = ModuleNotFoundError("No module named 'exo_guardian'", name="exo_guardian")
    assert "cannot import exo_guardian (is chain/runner on PYTHONPATH?)" in guardian_setup_error(e)
    assert guardian_setup_error(ValueError("postgres://u:secret@h/db")) == "guardian setup failed: ValueError"   # public-ok


def test_bridge_requirements_cover_the_guardian_imports():
    """exo_guardian runs inside the bridge venv: every third-party module it imports must be pinned there."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    reqs = (root / "agents/bridge/requirements.txt").read_text().lower()
    pinned = {re.split(r"[=<>\[]", ln)[0].strip().replace("_", "-") for ln in reqs.splitlines() if ln.strip() and not ln.startswith("#")}
    assert all("==" in ln for ln in reqs.splitlines() if ln.strip() and not ln.startswith("#"))
    for mod in ("eth-abi", "eth-utils", "requests", "psycopg"):
        assert mod in pinned, mod
