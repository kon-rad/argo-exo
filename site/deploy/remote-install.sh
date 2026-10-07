#!/usr/bin/env bash
# Runs ON THE DROPLET as root, started by site/deploy/install.sh: `sudo bash remote-install.sh <stage dir>`.
# Idempotent: rerunning installs the new build and restarts the API; the Caddy import line is added only once.
# It never overwrites /etc/caddy/Caddyfile: it appends one import line after a timestamped backup, and restores the
# backup if `caddy validate` fails.
set -euo pipefail

SRC="${1:?usage: remote-install.sh <stage dir>}"
ENVF=/etc/exo-presale/env
SRV=/srv/exo-site
STATE=/var/lib/exo-presale
MAIN=/etc/caddy/Caddyfile
BLOCK=/etc/caddy/Caddyfile.exo
IMPORT_LINE="import $BLOCK"
UNIT=exo-presale-api
PKGS=(flask==3.1.3 waitress==3.0.2 requests==2.34.2 eth-account==0.14.0 eth-utils==6.0.0)
TS=$(date +%Y%m%d%H%M%S)

prune_backups() {  # keep the newest 3 of "$1"*
  ls -1t "$1"* 2>/dev/null | tail -n +4 | while IFS= read -r old; do rm -f -- "$old"; done || true
}
say() { printf '[exo] %s\n' "$*"; }
die() { printf '[exo] STOP: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run as root (install.sh uses sudo)"
for d in dist api/exo_presale content packages/nownodes-py/exo_nownodes deploy; do
  [[ -d "$SRC/$d" ]] || die "stage is missing $d"
done
command -v caddy >/dev/null || die "caddy is not installed"
[[ -f "$MAIN" ]] || die "$MAIN not found"

say "1. env file"
if [[ ! -f "$ENVF" ]]; then
  cat >&2 <<TEMPLATE
[exo] STOP: $ENVF is missing. Create it by hand (sudo install -d -m 750 /etc/exo-presale; sudoedit $ENVF), with:
  EXO_PREORDER=            # the deployed ExoPreorder (empty before the deploy: the page says "opening soon")
  EXO_PREORDER_FROM_BLOCK= # its deployment block, for exo-presale-admin
  NOWNODES_API_KEY=        # the NOWNodes key
then rerun install.sh. This script fixes its owner and mode (root:exosite 640).
TEMPLATE
  exit 1
fi
if grep -q '^[[:space:]]*\(export[[:space:]]\+\)\?EXO_BASE_RPC_URL' "$ENVF"; then
  die "$ENVF sets EXO_BASE_RPC_URL (the local-fork override); remove it"
fi
if ! grep -q '^[[:space:]]*NOWNODES_API_KEY=..*' "$ENVF"; then
  say "   warning: NOWNODES_API_KEY is empty; /api/sale will answer 503 once a contract is set"
fi

say "2. system user exosite"
if id exosite >/dev/null 2>&1; then say "   exists"; else
  useradd --system --home-dir "$STATE" --no-create-home --shell /usr/sbin/nologin exosite; say "   created"; fi
chown root:exosite "$ENVF" "$(dirname "$ENVF")"; chmod 640 "$ENVF"; chmod 750 "$(dirname "$ENVF")"

say "3. files -> $SRV (root-owned)"
install -d -m 755 -o root -g root "$SRV" "$SRV/api" "$SRV/packages" "$SRV/packages/nownodes-py"
rsync -a --delete --chown=root:root --chmod=D755,F644 "$SRC/dist/" "$SRV/dist/"
rsync -a --delete --chown=root:root --chmod=D755,F644 "$SRC/api/exo_presale/" "$SRV/api/exo_presale/"
rsync -a --delete --chown=root:root --chmod=D755,F644 "$SRC/content/" "$SRV/content/"
rsync -a --delete --chown=root:root --chmod=D755,F644 "$SRC/packages/nownodes-py/exo_nownodes/" "$SRV/packages/nownodes-py/exo_nownodes/"

say "4. venv"
[[ -x "$SRV/venv/bin/python" ]] || python3 -m venv "$SRV/venv"
"$SRV/venv/bin/pip" install --quiet --disable-pip-version-check "${PKGS[@]}"

say "5. state dir $STATE (exosite, 700)"
install -d -m 700 -o exosite -g exosite "$STATE" "$STATE/.cache"
chown exosite:exosite "$STATE" "$STATE/.cache"; chmod 700 "$STATE" "$STATE/.cache"
for f in "$STATE"/claims.db "$STATE"/claims.db-*; do
  if [[ -e "$f" ]]; then chown exosite:exosite "$f"; chmod 600 "$f"; fi
done

say "6. systemd unit + admin wrapper"
install -m 644 -o root -g root "$SRC/deploy/$UNIT.service" "/etc/systemd/system/$UNIT.service"
cat > /usr/local/bin/exo-presale-admin <<'WRAP'
#!/bin/sh
# Usage: sudo -u exosite exo-presale-admin summary|export     (export prints names/emails: redirect to a 600 file)
# The env file is parsed by Python as literal KEY=VALUE lines (never shell-sourced), like systemd does.
umask 077
export EXO_CLAIMS_DB=/var/lib/exo-presale/claims.db EXO_NOWNODES_USAGE=/var/lib/exo-presale/.cache/usage.json
export PYTHONPATH=/srv/exo-site/api:/srv/exo-site/packages/nownodes-py
cd /srv/exo-site/api && exec /srv/exo-site/venv/bin/python -m exo_presale.admin --env-file /etc/exo-presale/env "$@"
WRAP
chmod 755 /usr/local/bin/exo-presale-admin
systemctl daemon-reload

say "7. caddy"
if [[ -f "$BLOCK" ]]; then cp -p "$BLOCK" "$BLOCK.bak-$TS"; fi
cp -p "$MAIN" "$MAIN.bak-exo-$TS"
install -m 644 -o root -g root "$SRC/deploy/Caddyfile.exo" "$BLOCK"
if grep -qxF "$IMPORT_LINE" "$MAIN"; then say "   import line already present"; else
  printf '\n# Argo Exo pre-sale (added by argo-exo site/deploy/install.sh)\n%s\n' "$IMPORT_LINE" >> "$MAIN"
  say "   appended '$IMPORT_LINE' (backup $MAIN.bak-exo-$TS)"
fi
VLOG=$(mktemp)
trap 'rm -f "$VLOG"' EXIT
if ! caddy validate --config "$MAIN" --adapter caddyfile >"$VLOG" 2>&1; then
  cp -p "$MAIN.bak-exo-$TS" "$MAIN"
  if [[ -f "$BLOCK.bak-$TS" ]]; then cp -p "$BLOCK.bak-$TS" "$BLOCK"; else rm -f "$BLOCK"; fi
  tail -5 "$VLOG" >&2
  die "caddy validate failed; restored the previous Caddyfile (and Caddyfile.exo). Nothing reloaded."
fi
if ! systemctl reload caddy; then
  # `caddy validate` as root can leave Caddy's log files root-owned, and the caddy user then can't reopen them.
  say "   reload failed; handing /var/log/caddy back to caddy:caddy and retrying"
  if [[ -d /var/log/caddy ]]; then chown -R caddy:caddy /var/log/caddy; fi
  systemctl reload caddy
fi
if cmp -s "$MAIN" "$MAIN.bak-exo-$TS"; then rm -f "$MAIN.bak-exo-$TS"; fi     # keep a backup only when it changed
prune_backups "$MAIN.bak-exo-"
prune_backups "$BLOCK.bak-"

say "8. $UNIT"
systemctl enable "$UNIT" >/dev/null 2>&1
systemctl restart "$UNIT"
for _ in $(seq 20); do curl -sf http://127.0.0.1:5310/api/sale >/dev/null 2>&1 && break; sleep 0.5; done
systemctl is-active --quiet "$UNIT" || { journalctl -u "$UNIT" -n 20 --no-pager >&2; die "$UNIT is not running"; }
say "   /api/sale -> $(curl -s http://127.0.0.1:5310/api/sale)"
free -m | head -2
say "done"
