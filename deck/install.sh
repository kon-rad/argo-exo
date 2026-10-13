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
/srv/deck/venv/bin/pip install -q -r $A/requirements-sensors.txt </dev/null || echo "WARN: luma.oled / nfcpy did not install: deck-oled and deck-nfc will not start"
S apt-get install -y -qq espeak-ng alsa-utils rclone ffmpeg i2c-tools libnfc-bin >/dev/null
S install -m 755 $A/bin/deck-approve /usr/local/bin/deck-approve
S install -m 755 $A/bin/deck-capture /usr/local/bin/deck-capture
S install -m 755 $A/bin/deck-button /usr/local/bin/deck-button
S install -m 755 $A/bin/exo-unlock /usr/local/bin/exo-unlock
S install -m 755 $A/bin/exo-demo /usr/local/bin/exo-demo
S install -m 755 $A/bin/deck-kiosk /usr/local/bin/deck-kiosk
S install -m 755 $A/bin/deck-secondbrain /usr/local/bin/deck-secondbrain
S install -m 755 $A/bin/deck-oled /usr/local/bin/deck-oled
S install -m 755 $A/bin/deck-nfc /usr/local/bin/deck-nfc
install -D -m 644 $A/desktop/deck-secondbrain.desktop "$HOME/.local/share/applications/deck-secondbrain.desktop"
install -m 644 $A/buttons/deck-buttons.py /srv/deck/deck-buttons.py
install -m 644 $A/collector/lifelog-collector.py /srv/deck/lifelog-collector.py
install -m 644 $A/recorder/deck-recorder.py /srv/deck/deck-recorder.py
install -m 644 $A/ring/ring_sync.py /srv/deck/ring_sync.py
mkdir -p /srv/deck/hooks /srv/deck/state/tx-queue /srv/deck/state/tx-approved /srv/deck/outbox/photos /srv/deck/outbox/audio /srv/deck/outbox/nfc /srv/deck/outbox/lifeos /srv/deck/oled/screens /srv/deck/media/video
install -d -m 700 /srv/deck/keys
[ -f /srv/deck/buttons.json ] || /usr/local/bin/deck-button reset >/dev/null
# The I2S mic, shared by talk, memos and video. Only once the card exists; until then video records silently.
if grep -qi adau7002 /proc/asound/cards 2>/dev/null; then
  [ -f /etc/asound.conf ] && ! grep -q exomic /etc/asound.conf && S cp /etc/asound.conf /etc/asound.conf.pre-exo
  S install -m 644 $A/alsa/asound-exomic.conf /etc/asound.conf
  grep -q "^EXO_MIC_DEVICE=exomic" /srv/deck/.env 2>/dev/null || echo "NOTE: set EXO_MIC_DEVICE=exomic in /srv/deck/.env so push-to-talk shares the I2S mic"
else
  echo "NOTE: no adau7002 sound card yet: video records without sound until the I2S mic is fitted"
fi
for h in talk-start talk-stop talk-cancel repeat approve freeze fund-approve; do install -m 755 $A/hooks/$h /srv/deck/hooks/$h; done
for u in $A/systemd/*.service $A/systemd/*.timer; do S install -m 644 "$u" /etc/systemd/system/; done
S systemctl disable --now cyberdeck-voice 2>/dev/null || true
S rm -f /etc/systemd/system/cyberdeck-voice.service
S usermod -aG gpio,audio,i2c,dialout deck || true
S systemctl daemon-reload
S systemctl enable deck-buttons lifelog-collector cyberdeck-dashboard deck-recorder
for t in $A/systemd/*.timer; do S systemctl enable --now "$(basename "$t")"; done
if [ ! -f /srv/deck/.env ]; then
  echo "ERROR: /srv/deck/.env is missing on the Pi. Create it from .env.example (deck section), then rerun." >&2
  exit 1
fi
for k in EXO_BRIDGE_URL EXO_BRIDGE_TOKEN GEMINI_API_KEY EXO_MIC_DEVICE DECK_UPLOAD_TOKEN ULTRAHUMAN_API_TOKEN; do
  grep -q "^$k=" /srv/deck/.env || echo "MISSING in /srv/deck/.env: $k"
done
echo "NOTE: deck-queue-sync.service is installed but not enabled. Once EXO_MODULE_ADDRESS and NOWNODES_API_KEY are in /srv/deck/.env, the hot keystore is at /srv/deck/keys/hot.json and exo-unlock has run, run: sudo systemctl enable --now deck-queue-sync"
echo "NOTE: deck-confirm.service is installed but not enabled. Once NOWNODES_API_KEY, EXO_WATCH_ADDRESSES and EXO_MODULE_ADDRESS are in /srv/deck/.env, run: sudo systemctl enable --now deck-confirm"
S systemctl restart deck-buttons lifelog-collector cyberdeck-dashboard deck-recorder
# Chest OLED (I2C) and NFC reader (UART): enabled only once their bus exists. Wiring: exo-sensor-integration-plan.
if [ -e /dev/i2c-1 ]; then S systemctl enable deck-oled; S systemctl restart deck-oled
else echo "NOTE: I2C is off (no /dev/i2c-1): sudo raspi-config nonint do_i2c 0, reboot, rerun. deck-oled is installed, not enabled"; fi
if [ -e /dev/serial0 ]; then S systemctl enable deck-nfc; S systemctl restart deck-nfc
else echo "NOTE: UART is off (no /dev/serial0): sudo raspi-config nonint do_serial_hw 0, reboot, rerun. deck-nfc is installed, not enabled"; fi
grep -q "console=serial0" /boot/firmware/cmdline.txt 2>/dev/null && echo "NOTE: the serial console is still on /dev/serial0, so the PN532 will not answer: sudo raspi-config nonint do_serial_cons 1, reboot"
systemctl is-active deck-buttons lifelog-collector cyberdeck-dashboard deck-recorder
R
} | ssh "$HOST" 'IFS= read -r PW; export PW; bash -s'
