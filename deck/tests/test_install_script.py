"""Static checks on deck/install.sh. The script is never executed here."""
import os
import re
import subprocess
from pathlib import Path

DECK = Path(__file__).resolve().parents[1]
SCRIPT = DECK / "install.sh"


def test_executable():
    assert os.access(SCRIPT, os.X_OK)


def test_bash_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_password_not_in_ssh_args():
    text = SCRIPT.read_text()
    assert "PW='" not in text
    assert '"PW=' not in text


def test_no_hostnames_or_ips():
    text = SCRIPT.read_text()
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text)
    assert "deck@" + "cyber" + "deck" not in text


def test_hooks_match_files():
    text = SCRIPT.read_text()
    m = re.search(r"for h in ([\w\- ]+); do", text)
    assert m
    listed = sorted(m.group(1).split())
    on_disk = sorted(p.name for p in (DECK / "hooks").iterdir() if p.is_file())
    assert listed == on_disk
    assert "/srv/deck/state/tx-queue" in text and "/srv/deck/state/tx-approved" in text


def test_venv_and_paths():
    text = SCRIPT.read_text()
    assert "python3 -m venv /srv/deck/venv" in text
    assert "/srv/deck/app/deck" in text
    for h in (DECK / "hooks").iterdir():
        if h.is_file():
            body = h.read_text()
            assert "$DECK_ROOT/venv/bin/python" in body
            assert "$DECK_ROOT/app/deck" in body
