#!/usr/bin/env bash
# Deploy deck/ to the Pi from the Mac.
# Usage: DECK_HOST=deck@<deck-hostname> deck/install.sh
# The sudo password is read locally and sent over ssh stdin, never in argv.
set -euo pipefail
cd "$(dirname "$0")"
HOST="${DECK_HOST:?set DECK_HOST, e.g. deck@<deck-hostname>}"
read -rsp "Pi sudo password: " PW; echo

# Minimal first hop (same stdin-password pattern): make sure the target dir exists and is deck-owned.
printf '%s\n' "$PW" | ssh "$HOST" 'IFS= read -r PW; printf "%s\n" "$PW" | sudo -S -p "" mkdir -p /srv/deck/app/deck; printf "%s\n" "$PW" | sudo -S -p "" chown deck:deck /srv/deck /srv/deck/app /srv/deck/app/deck'
rsync -az --delete --exclude tests --exclude __pycache__ --exclude .venv ./ "$HOST:/srv/deck/app/deck/"

{ printf '%s\n' "$PW"; cat <<'R'
set -e
S(){ printf '%s\n' "$PW" | sudo -S -p "" "$@"; }
A=/srv/deck/app/deck
[ -x /srv/deck/venv/bin/python ] || python3 -m venv /srv/deck/venv
/srv/deck/venv/bin/pip install -q -r $A/requirements.txt </dev/null
S apt-get install -y -qq espeak-ng alsa-utils rclone ffmpeg >/dev/null
S install -m 755 $A/bin/deck-approve /usr/local/bin/deck-approve
S install -m 755 $A/bin/deck-capture /usr/local/bin/deck-capture
install -m 644 $A/buttons/deck-buttons.py /srv/deck/deck-buttons.py
install -m 644 $A/collector/lifelog-collector.py /srv/deck/lifelog-collector.py
mkdir -p /srv/deck/hooks /srv/deck/state/tx-queue /srv/deck/state/tx-approved /srv/deck/outbox/photos /srv/deck/outbox/audio /srv/deck/media/video
for h in talk-start talk-stop talk-cancel repeat; do install -m 755 $A/hooks/$h /srv/deck/hooks/$h; done
for u in $A/systemd/*.service $A/systemd/*.timer; do S install -m 644 "$u" /etc/systemd/system/; done
S systemctl disable --now cyberdeck-voice 2>/dev/null || true
S rm -f /etc/systemd/system/cyberdeck-voice.service
S usermod -aG gpio,audio deck || true
S systemctl daemon-reload
S systemctl enable deck-buttons lifelog-collector cyberdeck-dashboard
for t in $A/systemd/*.timer; do S systemctl enable --now "$(basename "$t")"; done
S systemctl restart deck-buttons lifelog-collector cyberdeck-dashboard
for k in EXO_BRIDGE_URL EXO_BRIDGE_TOKEN GEMINI_API_KEY EXO_MIC_DEVICE; do
  grep -q "^$k=" /srv/deck/.env || echo "MISSING in /srv/deck/.env: $k"
done
systemctl is-active deck-buttons lifelog-collector cyberdeck-dashboard
R
} | ssh "$HOST" 'IFS= read -r PW; export PW; bash -s'
