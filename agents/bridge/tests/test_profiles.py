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
    assert "EXO_GUARD_TOKEN=$GUARD_TOKEN" in bridge and 'echo "$GUARD_TOKEN"' not in bridge and "unset TOKEN GUARD_TOKEN" in bridge
    assert "EXO_GUARD_TOKEN" in (AGENTS / "docs" / "board-conventions.md").read_text()


def _run_install(tmp_path, *args):
    import os
    import subprocess
    home = tmp_path / "home"
    for d in ("argo-wallet", "researcher"):
        (home / "profiles" / d).mkdir(parents=True)
        (home / "profiles" / d / "SOUL.md").write_text("old")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    stub = bindir / "hermes"
    stub.write_text(f"""#!/usr/bin/env bash
echo "$@" >> {log}
[[ "$*" == *--help* ]] && exit 0
if [[ "$1 $2" == "profile create" ]]; then mkdir -p "{home}/profiles/${{@: -1}}"; fi
if [[ "$1 $2 $3" == "kanban boards list" ]]; then echo "exo-old"; fi
exit 0
""")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "HERMES_HOME": str(home)}
    global _install_env
    _install_env = env
    r = subprocess.run(["bash", str(AGENTS / "install-profiles.sh"), *args], env=env, capture_output=True, text=True)
    return r, home, log.read_text()


def test_install_profiles_checks_directory_not_table(tmp_path):
    r, home, calls = _run_install(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "profile create --no-alias" in calls and "wallet" in calls
    assert (home / "profiles" / "wallet" / "SOUL.md").read_text().startswith("# wallet")
    assert (home / "profiles" / "researcher" / "SOUL.md").read_text() == "old"
    assert "create --no-alias --description Reads balances" in calls
    assert "boards create exo" in calls  # exo-old must not satisfy the check


def test_install_profiles_force_backs_up(tmp_path):
    r, home, _ = _run_install(tmp_path, "--force")
    assert r.returncode == 0, r.stderr
    assert (home / "profiles" / "researcher" / "SOUL.md").read_text().startswith("# researcher")
    assert list((home / "profiles" / "researcher").glob("SOUL.md.bak-*"))


def test_bridge_deps_are_pinned():
    reqs = (AGENTS / "bridge" / "requirements.txt").read_text().lower()
    for pkg in ("flask", "waitress", "requests"):
        assert f"{pkg}==" in reqs
    assert "requirements.txt" in (AGENTS / "install-bridge.sh").read_text()


def test_install_links_wallet_skill_without_touching_gateways(tmp_path):
    r, home, calls = _run_install(tmp_path)
    assert r.returncode == 0, r.stderr
    for name in ("trader", "portfolio", "wallet"):
        link = home / "profiles" / name / "skills" / "exo-wallet"
        assert link.is_symlink() and (link / "wallet.py").is_file()
    assert (home / "skills" / "exo-wallet").is_symlink()
    assert not (home / "profiles" / "researcher" / "skills").exists()
    assert "gateway" not in calls
    import subprocess
    again = subprocess.run(["bash", str(AGENTS / "install-profiles.sh")], capture_output=True, text=True,
                           env=_install_env)  # idempotent re-run over existing links
    assert again.returncode == 0, again.stderr


def test_install_links_nownodes_chain_into_wallet_and_default_only(tmp_path):
    r, home, calls = _run_install(tmp_path)
    assert r.returncode == 0, r.stderr
    for d in (home / "profiles" / "wallet" / "skills", home / "skills"):
        assert (d / "nownodes-chain").is_symlink() and (d / "nownodes-chain" / "chain.py").is_file()
    for name in ("trader", "portfolio"):
        assert not (home / "profiles" / name / "skills" / "nownodes-chain").exists()
