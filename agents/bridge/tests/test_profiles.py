from pathlib import Path

AGENTS = Path(__file__).resolve().parents[2]
PROFILES = AGENTS / "profiles"
EXPECTED = {"librarian", "trader", "portfolio", "wallet", "builder", "researcher"}


def test_every_agent_has_a_soul_and_a_description():
    assert {p.name for p in PROFILES.iterdir() if p.is_dir()} == EXPECTED
    for name in EXPECTED:
        soul = (PROFILES / name / "SOUL.md").read_text()
        desc = (PROFILES / name / "description.txt").read_text().strip()
        assert soul.startswith(f"# {name}") and 20 <= len(desc) <= 300


def test_money_agents_never_sign():
    for name in ("trader", "portfolio", "wallet"):
        soul = (PROFILES / name / "SOUL.md").read_text()
        assert "never sign" in soul.lower() and "guardian" in soul.lower()
        assert "You never sign transactions and you hold no keys." in soul
        assert "Every transaction you propose goes to the CRE Transaction Guardian; a refusal is final." in soul


def test_no_profile_holds_a_dsn_or_secret():
    for f in PROFILES.rglob("*"):
        if f.is_file():
            text = f.read_text().lower()
            assert "postgres://" not in text and "postgresql://" not in text and "private key:" not in text


def test_router_and_bridge_agree_on_names():
    from exo_bridge.config import DEFAULT_AGENTS
    assert set(DEFAULT_AGENTS.split(",")) == EXPECTED


def test_scripts_keep_the_constraints():
    prof = (AGENTS / "install-profiles.sh").read_text()
    bridge = (AGENTS / "install-bridge.sh").read_text()
    assert "--force" in prof and "gateway start" not in prof + bridge
    assert "tailscale ip -4" in bridge and "die " in bridge
    assert "EXO_BRIDGE_HOST=$TS_IP" in bridge  # never a wildcard or public IP
    assert "umask 077" in bridge and "chmod 600" in bridge
    assert "echo $TOKEN" not in bridge and 'echo "$TOKEN"' not in bridge
