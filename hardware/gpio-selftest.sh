#!/usr/bin/env bash
# Light each status LED for half a second, then print button states. Run on the Pi.
set -euo pipefail
for p in 17 25 22 23 24; do pinctrl set $p op dh; sleep 0.5; pinctrl set $p op dl; done
for p in 5 26 6 13 16; do pinctrl set $p ip pu; printf "GPIO %-2s %s\n" $p "$(pinctrl get $p | grep -o 'hi\|lo')"; done
echo "Each released button should read hi; hold one and re-run to see lo."
