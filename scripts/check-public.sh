#!/usr/bin/env bash
# Fails if anything private is about to go public: IPv4 addresses other than
# loopback/any/doc ranges, emails, Telegram-style chat IDs, and every pattern in
# the (uncommitted) private denylist. A line ending in "# public-ok" is a reviewed
# test fixture and is skipped. Usage: scripts/check-public.sh [path...]
set -uo pipefail
paths=("${@:-.}")
deny="${EXO_DENYLIST_FILE:-$HOME/.config/exo/denylist.txt}"
hits=0
scan() {  # $1 = extended regex, $2 = label
  local out
  out=$(grep -rnIE --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv --exclude=check-public.sh \
        -e "$1" "${paths[@]}" 2>/dev/null \
        | grep -vE '127\.0\.0\.1|0\.0\.0\.0|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|public-ok' || true)
  if [[ -n "$out" ]]; then echo "$out" | sed "s/\$/  [$2]/"; hits=1; fi
}
scan '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' ipv4
scan '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' email
scan 'chat[_-]?id[^A-Za-z0-9]{1,4}-?[0-9]{6,}' telegram-chat-id
if [[ -f "$deny" ]]; then
  while IFS= read -r pat; do
    [[ -z "$pat" || "$pat" == \#* ]] && continue
    out=$(grep -rnIF --exclude-dir=.git --exclude-dir=.venv --exclude=check-public.sh -e "$pat" "${paths[@]}" 2>/dev/null | grep -v "public-ok" || true)
    if [[ -n "$out" ]]; then echo "$out" | sed 's/$/  [denylist]/'; hits=1; fi
  done < "$deny"
fi
exit $hits
