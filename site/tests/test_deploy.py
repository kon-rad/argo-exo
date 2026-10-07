"""site/deploy: the Caddy block, the systemd unit and the installer (text checks + a local run with ssh stubbed)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
CADDY = (DEPLOY / "Caddyfile.exo").read_text()
UNIT = (DEPLOY / "exo-presale-api.service").read_text()
INSTALL = (DEPLOY / "install.sh").read_text()
REMOTE = (DEPLOY / "remote-install.sh").read_text()
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def test_caddy_serves_mjs_as_javascript():
    assert re.search(r"@mjs\s+path\s+\*\.mjs", CADDY)
    assert 'header @mjs Content-Type "text/javascript; charset=utf-8"' in CADDY
    # the matcher sits in the static handle, before file_server
    static = CADDY[CADDY.index("\thandle {"):]
    assert static.index("@mjs") < static.index("file_server")


def test_caddy_block_shape():
    assert CADDY.count("exo.myargoquest.com {") == 1
    assert "reverse_proxy 127.0.0.1:5310" in CADDY and "root * /srv/exo-site/dist" in CADDY
    assert "script-src 'self'" in CADDY and "unsafe-inline" not in CADDY
    assert "X-Content-Type-Options nosniff" in CADDY
    assert "frame-ancestors 'none'" in CADDY and "base-uri 'none'" in CADDY and "form-action 'self'" in CADDY
    api = CADDY[CADDY.index("handle /api/* {"):CADDY.index("\thandle {")]
    assert re.search(r"request_body\s*\{\s*max_size 8KB\s*\}", api)
    assert api.index("request_body") < api.index("reverse_proxy")
    assert not re.search(r"^\s*log\b", CADDY, re.M)            # no access log of visitors
    assert set(IPV4.findall(CADDY)) == {"127.0.0.1"}


def test_unit_is_locked_down():
    for line in ("User=exosite", "ProtectSystem=strict", "ProtectHome=true", "NoNewPrivileges=true", "PrivateTmp=true",
                 "UMask=0077", "MemoryMax=120M", "EnvironmentFile=/etc/exo-presale/env",
                 "ReadWritePaths=/var/lib/exo-presale /var/lib/exo-presale/.cache",
                 "ExecStart=/srv/exo-site/venv/bin/python -m exo_presale"):
        assert line in UNIT.splitlines(), line
    assert "EXO_BASE_RPC_URL" not in UNIT and "EXO_PRESALE_HOST" not in UNIT   # loopback default, no fork override


def test_remote_install_never_overwrites_main_caddyfile():
    assert 'cp -p "$MAIN" "$MAIN.bak-exo-$TS"' in REMOTE
    assert 'grep -qxF "$IMPORT_LINE" "$MAIN"' in REMOTE
    assert '>> "$MAIN"' in REMOTE
    assert not re.search(r'[^>]>\s*"\$MAIN"', REMOTE)           # never truncated...
    assert REMOTE.count('cp -p "$MAIN.bak-exo-$TS" "$MAIN"') == 1  # ...only restored from its own backup
    v, r = REMOTE.index('caddy validate --config "$MAIN"'), REMOTE.index("systemctl reload caddy")
    assert v < r                                                # validate before any reload
    assert "chown -R caddy:caddy /var/log/caddy" in REMOTE     # the root-owned log gotcha


def test_remote_install_prunes_backups_and_validate_log():
    assert "tail -n +4" in REMOTE and REMOTE.count("prune_backups") >= 3
    assert "trap 'rm -f \"$VLOG\"' EXIT" in REMOTE
    v = REMOTE.index("VLOG=$(mktemp)")
    assert REMOTE.index("trap 'rm -f \"$VLOG\"' EXIT") < REMOTE.index('caddy validate --config "$MAIN"') and v < REMOTE.index("trap 'rm -f")


def test_admin_wrapper_does_not_source_the_env_file():
    wrap = REMOTE[REMOTE.index("<<'WRAP'"):REMOTE.index("\nWRAP\n")]
    assert ". /etc/exo-presale/env" not in wrap and "source " not in wrap and "set -a" not in wrap
    assert "--env-file /etc/exo-presale/env" in wrap


def test_remote_install_state_dir_and_env():
    assert 'install -d -m 700 -o exosite -g exosite "$STATE" "$STATE/.cache"' in REMOTE
    assert 'chmod 600 "$f"' in REMOTE
    assert 'if [[ ! -f "$ENVF" ]]' in REMOTE and "exit 1" in REMOTE
    assert 'chmod 640 "$ENVF"' in REMOTE and 'chown root:exosite "$ENVF"' in REMOTE
    assert "EXO_BASE_RPC_URL" in REMOTE                         # refuses an env file carrying the fork override
    assert REMOTE.index('if [[ ! -f "$ENVF" ]]') < REMOTE.index("useradd")   # refuses before changing anything


def test_no_hosts_in_deploy_files():
    for name, text in (("install.sh", INSTALL), ("remote-install.sh", REMOTE), ("unit", UNIT)):
        assert set(IPV4.findall(text)) <= {"127.0.0.1"}, name
    assert 'SITE_HOST:?' in INSTALL


@pytest.mark.parametrize("script", ["install.sh", "remote-install.sh", "rehearse-fork.sh"])
def test_bash_syntax(script):
    assert subprocess.run(["bash", "-n", str(DEPLOY / script)]).returncode == 0


def test_install_needs_site_host():
    env = {k: v for k, v in os.environ.items() if k != "SITE_HOST"}
    r = subprocess.run(["bash", str(DEPLOY / "install.sh"), "--dry-run"], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "SITE_HOST" in r.stderr


@pytest.mark.skipif(not shutil.which("rsync"), reason="rsync not installed")
def test_install_stages_the_right_tree_with_ssh_stubbed(tmp_path):
    """Run install.sh --yes with fake ssh/rsync-to-host: check what would reach the droplet and the ssh command."""
    bin_ = tmp_path / "bin"; bin_.mkdir()
    remote = tmp_path / "remote"; remote.mkdir()
    log = tmp_path / "calls.log"
    real_rsync = shutil.which("rsync")
    (bin_ / "ssh").write_text(f'#!/bin/sh\necho "ssh $*" >> "{log}"\n')
    (bin_ / "rsync").write_text(
        "#!/bin/bash\nlast=\"${@: -1}\"\n"
        f'if [[ "$last" == *:* ]]; then echo "rsync-remote $*" >> "{log}"; set -- "${{@:1:$#-1}}" "{remote}/"; fi\n'
        f'exec "{real_rsync}" "$@"\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SITE_HOST": "example-host", "PY": sys.executable,
           "TMPDIR": str(tmp_path)}
    r = subprocess.run(["bash", str(DEPLOY / "install.sh"), "--yes"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert "rsync-remote -az --delete" in calls and "example-host:exo-site-stage/" in calls
    assert 'ssh -t example-host sudo bash "$HOME/exo-site-stage/deploy/remote-install.sh" "$HOME/exo-site-stage"' in calls
    staged = {str(p.relative_to(remote)) for p in remote.rglob("*")}
    for must in ("dist/index.html", "dist/static/wallet.mjs", "dist/static/preorder.json", "api/exo_presale/admin.py",
                 "packages/nownodes-py/exo_nownodes/rpc.py", "content/copy.json", "deploy/remote-install.sh",
                 "deploy/Caddyfile.exo", "deploy/exo-presale-api.service"):
        assert must in staged, must
    assert not any("__pycache__" in p or "/tests" in p or p.endswith("rehearse-fork.sh") for p in staged)
    assert not list(tmp_path.glob("exo-site-stage.*"))           # local staging dir removed


def test_prune_backups_keeps_newest_three_and_survives_no_match(tmp_path):
    fn = REMOTE[REMOTE.index("prune_backups() {"):REMOTE.index("\n}\n", REMOTE.index("prune_backups() {")) + 3]
    for i in range(5):
        p = tmp_path / f"Caddyfile.bak-exo-{i}"
        p.write_text("x"); os.utime(p, (1000 + i, 1000 + i))
    script = f'set -euo pipefail\n{fn}\nprune_backups "{tmp_path}/Caddyfile.bak-exo-"\nprune_backups "{tmp_path}/none-"\necho survived\n'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.stdout.strip() == "survived", r.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"Caddyfile.bak-exo-{i}" for i in (2, 3, 4)]


def _contract_check(tmp_path, env_text, contract):
    """Runs remote-install.sh's EXO_PREORDER vs dist/static/preorder.json check on its own."""
    start = REMOTE.index("# The page (dist/static/preorder.json")
    end = REMOTE.index('say "   contract:')
    block = REMOTE[start:end]
    (tmp_path / "dist" / "static").mkdir(parents=True, exist_ok=True)
    (tmp_path / "dist" / "static" / "preorder.json").write_text('{"chainId": 8453, "contract": "%s"}' % contract)
    (tmp_path / "env").write_text(env_text)
    script = ("set -euo pipefail\nsay() { printf '%s\\n' \"$*\"; }\ndie() { printf 'STOP: %s\\n' \"$*\" >&2; exit 1; }\n"
              f'SRC="{tmp_path}"\nENVF="{tmp_path}/env"\n{block}\necho agree\n')
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


ZERO = "0x" + "0" * 40
C1 = "0x" + "ab" * 20


@pytest.mark.parametrize("env_text,contract", [
    ("EXO_PREORDER=\nNOWNODES_API_KEY=k\n", ZERO),                       # not deployed on either side
    ("NOWNODES_API_KEY=k\n", ZERO),                                      # unset = not deployed
    (f"EXO_PREORDER={C1.upper().replace('0X', '0x')}\n", C1),             # case-insensitive
    (f'export EXO_PREORDER="{C1}"  # live\n', C1),
    (f"EXO_PREORDER={ZERO}\nEXO_PREORDER={C1}\n", C1),                   # the last line wins, as systemd reads it
])
def test_remote_install_contract_check_agrees(tmp_path, env_text, contract):
    r = _contract_check(tmp_path, env_text, contract)
    assert r.returncode == 0 and r.stdout.strip().endswith("agree"), r.stderr


@pytest.mark.parametrize("env_text,contract", [
    (f"EXO_PREORDER={C1}\n", ZERO),                                       # env live, page says opening soon
    ("EXO_PREORDER=\n", C1),                                              # page live, API not pointed at it
    (f"EXO_PREORDER={C1}\n", "0x" + "cd" * 20),                           # two different contracts
])
def test_remote_install_refuses_a_contract_mismatch(tmp_path, env_text, contract):
    r = _contract_check(tmp_path, env_text, contract)
    assert r.returncode == 1 and "does not match" in r.stderr and "agree" not in r.stdout


def test_remote_install_runs_the_contract_check_before_installing():
    assert REMOTE.index("does not match the page's contract") < REMOTE.index('say "3. files')
