#!/usr/bin/env bash
# Deploy deck/ to the Pi from the Mac.
# Usage: DECK_HOST=deck@<deck-hostname> deck/install.sh
# The sudo password is read locally and sent over ssh stdin, never in argv.
set -euo pipefail
cd "$(dirname "$0")"
HOST="${DECK_HOST:?set DECK_HOST, e.g. deck@<deck-hostname>}"
read -rsp "Pi sudo password: " PW; echo

# Minimal first hop (same stdin-password pattern): make sure the target dir exists and is deck-owned.
printf '%s\n' "$PW" | ssh "$HOST" 'IFS= read -r PW; printf "%s\n" "$PW" | sudo -S -p "" mkdir -p /srv/deck/app/deck /srv/deck/app/packages/nownodes-py; printf "%s\n" "$PW" | sudo -S -p "" chown -R deck:deck /srv/deck/app/packages; printf "%s\n" "$PW" | sudo -S -p "" chown deck:deck /srv/deck /srv/deck/app /srv/deck/app/deck'
rsync -az --delete --exclude tests --exclude __pycache__ --exclude .venv ./ "$HOST:/srv/deck/app/deck/"
rsync -az --delete --exclude tests --exclude __pycache__ ../packages/nownodes-py/ "$HOST:/srv/deck/app/packages/nownodes-py/"

{ printf '%s\n' "$PW"; cat <<'R'
set -e
S(){ printf '%s\n' "$PW" | sudo -S -p "" "$@"; }
A=/srv/deck/app/deck
[ -x /srv/deck/venv/bin/python ] || python3 -m venv /srv/deck/venv
/srv/deck/venv/bin/pip install -q -r $A/requirements.txt </dev/null
S apt-get install -y -qq espeak-ng alsa-utils rclone ffmpeg >/dev/null
S install -m 755 $A/bin/deck-approve /usr/local/bin/deck-approve
S install -m 755 $A/bin/deck-capture /usr/local/bin/deck-capture
S install -m 755 $A/bin/exo-unlock /usr/local/bin/exo-unlock
install -m 644 $A/buttons/deck-buttons.py /srv/deck/deck-buttons.py
install -m 644 $A/collector/lifelog-collector.py /srv/deck/lifelog-collector.py
install -m 644 $A/ring/ring_sync.py /srv/deck/ring_sync.py
mkdir -p /srv/deck/hooks /srv/deck/state/tx-queue /srv/deck/state/tx-approved /srv/deck/outbox/photos /srv/deck/outbox/audio /srv/deck/media/video
install -d -m 700 /srv/deck/keys
for h in talk-start talk-stop talk-cancel repeat approve freeze; do install -m 755 $A/hooks/$h /srv/deck/hooks/$h; done
for u in $A/systemd/*.service $A/systemd/*.timer; do S install -m 644 "$u" /etc/systemd/system/; done
S systemctl disable --now cyberdeck-voice 2>/dev/null || true
S rm -f /etc/systemd/system/cyberdeck-voice.service
S usermod -aG gpio,audio deck || true
S systemctl daemon-reload
S systemctl enable deck-buttons lifelog-collector cyberdeck-dashboard
for t in $A/systemd/*.timer; do S systemctl enable --now "$(basename "$t")"; done
if [ ! -f /srv/deck/.env ]; then
  echo "ERROR: /srv/deck/.env is missing on the Pi. Create it from .env.example (deck section), then rerun." >&2
  exit 1
fi
for k in EXO_BRIDGE_URL EXO_BRIDGE_TOKEN GEMINI_API_KEY EXO_MIC_DEVICE DECK_UPLOAD_TOKEN ULTRAHUMAN_API_TOKEN; do
  grep -q "^$k=" /srv/deck/.env || echo "MISSING in /srv/deck/.env: $k"
done
[ -x /usr/local/bin/deck-kiosk ] || echo "WARNING: /usr/local/bin/deck-kiosk is missing; the Kiosk double-tap will do nothing until it is installed on the Pi"
echo "NOTE: deck-queue-sync.service is installed but not enabled. Once EXO_MODULE_ADDRESS and NOWNODES_API_KEY are in /srv/deck/.env, the hot keystore is at /srv/deck/keys/hot.json and exo-unlock has run, run: sudo systemctl enable --now deck-queue-sync"
echo "NOTE: deck-confirm.service is installed but not enabled. Once NOWNODES_API_KEY, EXO_WATCH_ADDRESSES and EXO_MODULE_ADDRESS are in /srv/deck/.env, run: sudo systemctl enable --now deck-confirm"
S systemctl restart deck-buttons lifelog-collector cyberdeck-dashboard
systemctl is-active deck-buttons lifelog-collector cyberdeck-dashboard
R
} | ssh "$HOST" 'IFS= read -r PW; export PW; bash -s'
