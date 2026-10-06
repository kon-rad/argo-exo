#!/bin/bash
# Flash the XIAO ESP32S3 Sense life-log clip and show its first serial output.
# Usage: ~/cyberdeck-clip/flash.sh            (compile + upload + 90 s monitor)
#        ~/cyberdeck-clip/flash.sh monitor    (monitor only)
set -euo pipefail
cd "$(dirname "$0")"
FQBN="esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
SKETCH=deck-clip

PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)
if [ -z "$PORT" ]; then
  echo "No XIAO found on USB. Check the USB-C cable carries data."
  echo "If it still doesn't show: hold BOOT, tap RESET, release BOOT, then rerun."
  exit 1
fi
echo "XIAO on $PORT"

if [ "${1:-}" != "monitor" ]; then
  arduino-cli compile --fqbn "$FQBN" "$SKETCH"
  arduino-cli upload --fqbn "$FQBN" -p "$PORT" "$SKETCH"
  sleep 3
  PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
fi

echo "--- serial (90 s). Expect: cam=1 sd=1 mic=1 psram=1 ---"
timeout 90 arduino-cli monitor -p "$PORT" -c baudrate=115200 || true
