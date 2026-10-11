#!/usr/bin/env bash
# Bench self-test, run on the Pi after every power-on (pinctrl settings don't survive a reboot).
# Sets the button pull-ups, blinks the LEDs, prints button states.
#   ./gpio-selftest.sh          one-shot check
#   ./gpio-selftest.sh watch    live view; press buttons, Ctrl+C to stop
# Spare pads (GPIO 23, 24, 25, 12, 21) are left alone: they're reserved for sensors.
set -euo pipefail

BUTTONS=(26 6 13 16 5 27)
NAMES=(talk camera mic p1 approve p2)
LEDS=(22 17)  # red recording; keychain approve LED (once wired)

for p in "${BUTTONS[@]}"; do pinctrl set "$p" ip pu; done

states() {
  for i in "${!BUTTONS[@]}"; do
    p=${BUTTONS[$i]}
    printf "GPIO %-2s %-8s %s\n" "$p" "${NAMES[$i]}" "$(pinctrl get "$p" | grep -o '| [hl][io]' | tr -d '| ')"
  done
}

if [[ "${1:-}" == "watch" ]]; then
  trap 'tput cnorm; echo' EXIT
  tput civis
  while true; do clear; echo "Released = hi, pressed = lo. Ctrl+C to stop."; states; sleep 0.3; done
fi

for p in "${LEDS[@]}"; do pinctrl set "$p" op dh; sleep 0.5; pinctrl set "$p" op dl; done
states
echo "Released buttons read hi; run with 'watch' to see presses go lo."
