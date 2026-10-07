"""C1: the Guardian (exo-bridge, guardian-run, cre, the NOWNodes proxy) runs as `exoguard` from its own checkout,
which the `hermes` user (every Hermes agent) can neither read nor write. Static checks on the units and installers,
plus install-profiles.sh run against a fake Hermes home."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AGENTS = ROOT / "agents"
UNIT = (AGENTS / "systemd" / "exo-bridge.service").read_text()
PROXY = (ROOT / "infra" / "nownodes-proxy" / "nownodes-proxy.service").read_text()
INSTALL = (AGENTS / "install-bridge.sh").read_text()
PROFILES = (AGENTS / "install-profiles.sh").read_text()
GUARD = "/srv/exo-guard"


def _lines(unit):
    return [ln.strip() for ln in unit.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_bridge_unit_runs_as_exoguard_from_its_checkout():
    lines = _lines(UNIT)
    assert "User=exoguard" in lines and "Group=exoguard" in lines
    assert not any(ln.startswith("User=hermes") for ln in lines)
    for key in ("WorkingDirectory=", "EnvironmentFile=", "ExecStart=", "Environment=PYTHONPATH=", "Environment=HOME="):
        [ln] = [x for x in lines if x.startswith(key)]
        assert GUARD in ln and "/home/hermes" not in ln, ln
    assert "EnvironmentFile=/srv/exo-guard/config/bridge.env" in lines
    assert "Environment=PYTHONPATH=/srv/exo-guard/argo-exo/chain/runner" in lines


def test_bridge_unit_path_finds_bun_and_cre_and_has_room_for_a_simulation():
    lines = _lines(UNIT)
    [path] = [x for x in lines if x.startswith("Environment=PATH=")]
    dirs = path.split("=", 2)[2].split(":")
    assert "/srv/exo-guard/.bun/bin" in dirs and "/srv/exo-guard/.cre/bin" in dirs
    assert not any(d.startswith("/home/") for d in dirs)
    assert "MemoryMax=600M" in lines
    assert "cre workflow simulate" in UNIT          # the MemoryMax comment says what it is sized for


def test_nownodes_proxy_runs_as_exoguard_too():
    lines = _lines(PROXY)
    assert "User=exoguard" in lines and "/home/hermes" not in PROXY.replace("# ", "")
    assert "EnvironmentFile=/srv/exo-guard/config/nownodes.env" in lines


def test_installer_creates_the_user_and_locks_the_files_down():
    assert re.search(r"useradd --system .*--shell /usr/sbin/nologin \"\$GUARD_USER\"", INSTALL)
    assert "GUARD_USER=exoguard" in INSTALL and "GUARD_HOME=/srv/exo-guard" in INSTALL
    assert 'chmod 600 "$ENV"' in INSTALL and 'chown "$GUARD_USER:$GUARD_USER" "$ENV"' in INSTALL
    assert 'chmod 600 "$CRE_ENV"' in INSTALL and 'chown "$GUARD_USER:$GUARD_USER" "$CRE_ENV"' in INSTALL
    assert 'chmod 750 "$CHECKOUT"' in INSTALL and 'install -d -m 700 -o "$GUARD_USER"' in INSTALL
    assert "ENV=$CONF/bridge.env" in INSTALL and "CONF=$GUARD_HOME/config" in INSTALL
    assert "CRE_ENV=$CHECKOUT/chain/cre/.env" in INSTALL
    # it proves hermes can't read or write the Guardian's code, and refuses a hermes in the exoguard group
    assert 'runuser -u "$HERMES_USER" -- test -w' in INSTALL and 'runuser -u "$HERMES_USER" -- test -r' in INSTALL
    assert 'grep -qx "$GUARD_USER"' in INSTALL


def test_installer_clones_from_the_remote_not_the_hermes_checkout():
    assert 'as_guard git clone --quiet --branch "$REF" "$REPO_URL" "$CHECKOUT"' in INSTALL
    assert 'REPO_URL="${EXO_GUARD_REPO_URL:-https://github.com/' in INSTALL
    assert "ln -s" not in INSTALL and '"$HOME/argo-exo"' not in INSTALL      # no link from hermes' home any more
    assert "rsync" not in INSTALL and "cp -r" not in INSTALL


def test_installer_prints_the_sudo_steps_and_never_runs_them():
    head = INSTALL[:INSTALL.index("cat <<MSG")]
    run = "\n".join(ln for ln in head.splitlines()
                    if not ln.lstrip().startswith(("#", "echo ")) and "Allowed by /etc/sudoers.d" not in ln)
    for cmd in ("systemctl", "visudo", "sudoers.d", "cre login", "tee "):
        assert cmd not in run, cmd
    msg = INSTALL[INSTALL.index("cat <<MSG"):]
    assert "systemctl enable --now nownodes-proxy exo-bridge" in msg and "visudo -c" in msg
    assert "NOPASSWD: $HERMES_HOME_DIR/.local/bin/hermes kanban *" in msg


def test_installer_never_prints_a_token():
    for bad in ('echo "$TOKEN"', 'echo "$GUARD_TOKEN"', "echo $TOKEN", 'echo "$KEY"', "cat $ENV", 'cat "$ENV"'):
        assert bad not in INSTALL, bad
    assert "unset TOKEN GUARD_TOKEN KEY" in INSTALL
    assert "grep '^EXO_GUARD_TOKEN=' $ENV >> $HERMES_ENV" in INSTALL    # copied, not shown


def test_bridge_env_template_has_the_guardian_keys():
    for key in ("EXO_BRIDGE_TOKEN=", "EXO_GUARD_TOKEN=", "EXO_LEDGER_WRITER_DSN=", "EXO_LEDGER_DSN=",
                "EXO_GUARDIAN_BROADCAST=0", "HERMES_BIN=$WRAPPER"):
        assert key in INSTALL, key


def test_env_examples_split_bridge_and_hermes():
    root = (ROOT / ".env.example").read_text()
    bridge = root[root.index("# --- bridge"):]
    for key in ("EXO_BRIDGE_TOKEN=", "EXO_GUARD_TOKEN=", "EXO_LEDGER_WRITER_DSN=", "EXO_LEDGER_DSN=",
                "EXO_GUARDIAN_BROADCAST=0", "PYTHONPATH="):
        assert key in bridge, key
    assert "EXO_FREEZE_MAX_FEE_GWEI" in root[:root.index("# --- bridge")]
    hermes = (AGENTS / "hermes.env.example").read_text()
    keys = {ln.lstrip("# ").split("=")[0] for ln in hermes.splitlines() if "=" in ln and re.match(r"#? ?[A-Z_]+=", ln)}
    assert {"EXO_SAFE", "EXO_AGENT_PROFILE", "EXO_ADDRESS_BOOK", "EXO_GUARD_TOKEN", "EXO_BRIDGE_URL"} <= keys
    assert not keys & {"EXO_BRIDGE_TOKEN", "EXO_LEDGER_WRITER_DSN", "EXO_LEDGER_DSN", "CRE_ETH_PRIVATE_KEY"}
    cre = (ROOT / "chain" / "cre" / ".env.example").read_text()
    assert "exoguard" in cre and "mode 600" in cre and "Guardian-equivalent" in cre


def test_skill_docs_use_the_skills_venv_not_the_guardians():
    for skill in ("exo-wallet", "nownodes-chain"):
        doc = (AGENTS / "skills" / skill / "SKILL.md").read_text()
        assert "~/.venvs/exo-skills/bin/python" in doc and ".venvs/exo-bridge" not in doc
        assert "/srv/exo-guard/argo-exo/agents" not in doc


# ---- install-profiles.sh, run for real against a fake Hermes home ------------------------------------------


def _hermes_stub(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "hermes"
    home = tmp_path / "home"
    stub.write_text(f"""#!/usr/bin/env bash
[[ "$*" == *--help* ]] && exit 0
if [[ "$1 $2" == "profile create" ]]; then mkdir -p "{home}/profiles/${{@: -1}}"; fi
exit 0
""")
    stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "HERMES_HOME": str(home)}, home


def test_install_profiles_refuses_the_guardian_checkout(tmp_path):
    env, home = _hermes_stub(tmp_path)
    guard = tmp_path / "srv-exo-guard"
    shutil.copytree(AGENTS, guard / "argo-exo" / "agents", ignore=shutil.ignore_patterns("__pycache__", "tests", "bridge"))
    env["EXO_GUARD_HOME"] = str(guard.resolve())
    r = subprocess.run(["bash", str(guard / "argo-exo" / "agents" / "install-profiles.sh"), "--no-venv"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 1 and "Guardian's checkout" in r.stderr
    assert not (home / "profiles").exists()                    # nothing created, nothing linked


def test_install_profiles_sets_each_profiles_agent_identity_and_links_from_its_own_checkout(tmp_path):
    env, home = _hermes_stub(tmp_path)
    (home / "profiles" / "wallet").mkdir(parents=True)
    (home / "profiles" / "wallet" / ".env").write_text("OTHER=1\nEXO_AGENT_PROFILE=stale\n")
    env["EXO_GUARD_HOME"] = str(tmp_path / "srv-exo-guard")
    r = subprocess.run(["bash", str(AGENTS / "install-profiles.sh"), "--no-venv"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for name in ("librarian", "trader", "portfolio", "wallet", "builder", "researcher"):
        f = home / "profiles" / name / ".env"
        assert f.read_text().splitlines().count(f"EXO_AGENT_PROFILE={name}") == 1, name
        assert oct(f.stat().st_mode & 0o777) == "0o600"
    assert (home / "profiles" / "wallet" / ".env").read_text() == "OTHER=1\nEXO_AGENT_PROFILE=wallet\n"
    link = home / "profiles" / "wallet" / "skills" / "exo-wallet"
    assert link.resolve() == (AGENTS / "skills" / "exo-wallet").resolve()
    assert not str(link.resolve()).startswith(GUARD)


def test_install_profiles_rejects_unknown_flags(tmp_path):
    env, _ = _hermes_stub(tmp_path)
    r = subprocess.run(["bash", str(AGENTS / "install-profiles.sh"), "--frce"], env=env, capture_output=True, text=True)
    assert r.returncode == 2
