#!/bin/bash
# Flash the companion bench sketch (Ghost) to the XIAO ESP32S3 Sense and show its serial output.
# Usage: firmware/companion-bench/flash.sh            (compile + upload + 120 s monitor)
#        firmware/companion-bench/flash.sh compile    (compile only; no board needed)
#        firmware/companion-bench/flash.sh monitor    (monitor only)
set -euo pipefail
cd "$(dirname "$0")"
FQBN="esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
SKETCH=.

if [ "${1:-}" = "compile" ]; then
  arduino-cli compile --fqbn "$FQBN" "$SKETCH"
  exit 0
fi

PORTS=$(ls /dev/cu.usbmodem* 2>/dev/null || true)
if [ -z "${PORT:-}" ] && [ "$(echo "$PORTS" | grep -c .)" -gt 1 ]; then
  echo "More than one USB serial device (a phone counts):"; echo "$PORTS"
  echo "Unplug the others, or rerun with PORT=/dev/cu.usbmodemXXXX"; exit 1
fi
PORT=${PORT:-$(echo "$PORTS" | head -1)}
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
  PORT=${PORT_AFTER:-$PORT}
fi

echo "--- serial (120 s). Expect before the amp and cell arrive: RESULT oled=1 imu=1 amp=0 mic=1 sd=0|1 pix=3 bat=none ---"
timeout 120 arduino-cli monitor -p "$PORT" -c baudrate=115200 || true
