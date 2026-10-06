import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-public.sh"


def run(tmp_path, content, denylist=""):
    f = tmp_path / "note.md"
    f.write_text(content)
    deny = tmp_path / "deny.txt"
    deny.write_text(denylist)
    return subprocess.run(["bash", str(SCRIPT), str(tmp_path)],
                          env={"EXO_DENYLIST_FILE": str(deny), "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True)


def test_clean_tree_passes(tmp_path):
    assert run(tmp_path, "hello world\n").returncode == 0


def test_ipv4_is_flagged(tmp_path):
    r = run(tmp_path, "server at 10.20.30.40\n")  # public-ok
    assert r.returncode == 1 and "note.md:1" in r.stdout


def test_tailnet_ip_is_flagged(tmp_path):
    assert run(tmp_path, "bridge 100.101.102.103\n").returncode == 1  # public-ok


def test_private_denylist_pattern_is_flagged(tmp_path):
    assert run(tmp_path, "ssh secret-host\n", denylist="secret-host\n").returncode == 1


def test_public_ok_marker_skips_a_line(tmp_path):
    assert run(tmp_path, "BASE = {\"host\": \"100.64.0.5\"}  # public-ok\n").returncode == 0  # public-ok


def test_example_placeholders_pass(tmp_path):
    assert run(tmp_path, "EXO_BRIDGE_HOST=<droplet-tailnet-ip>\n127.0.0.1 and 0.0.0.0\n").returncode == 0
