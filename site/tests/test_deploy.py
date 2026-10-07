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
