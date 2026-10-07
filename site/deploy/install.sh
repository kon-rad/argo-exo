#!/usr/bin/env bash
# Builds the pre-sale site and installs it, the sale API and the Caddy block on the droplet. Run by Konrad, from
# this repo, after reading what it prints. Rerunnable: every step checks before it changes anything.
#
# Usage: SITE_HOST=<ssh host alias> site/deploy/install.sh [--release] [--yes] [--dry-run]
#   --release  build with the release gate (refuses while any TODO(konrad) slot is empty)
#   --yes      skip the confirmation prompt          --dry-run  print the plan and stop
# The host comes from SITE_HOST only; no hostname or address lives in this repo.
#
# Two connections: one rsync of the staged tree into ~/exo-site-stage on the host, then one `ssh -t` that runs
# deploy/remote-install.sh under sudo (it prints each step as it goes).
set -euo pipefail

RELEASE="" YES="" DRY=""
for a in "$@"; do
  case "$a" in
    --release) RELEASE=--release ;;
    --yes) YES=1 ;;
    --dry-run) DRY=1 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done
: "${SITE_HOST:?set SITE_HOST to the ssh host alias of the droplet}"
[[ "$SITE_HOST" =~ ^[A-Za-z0-9._@-]+$ ]] || { echo "SITE_HOST looks wrong: $SITE_HOST" >&2; exit 2; }

SITE="$(cd "$(dirname "$0")/.." && pwd)" REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PY:-$REPO/.venv/bin/python}"
CONTRACT=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["contract"])' "$SITE/static/preorder.json")

cat <<PLAN
Argo Exo pre-sale install -> $SITE_HOST
  local   build site/ ${RELEASE:-(draft)} into a temp dir; preorder.json contract = $CONTRACT
  local   rsync dist/, api/exo_presale, content/, packages/nownodes-py/exo_nownodes, deploy/ -> $SITE_HOST:~/exo-site-stage/
  remote  (sudo, one ssh session; see site/deploy/remote-install.sh)
          1. refuse unless /etc/exo-presale/env exists (and has no EXO_BASE_RPC_URL fork override)
          2. create system user exosite if missing; env file root:exosite 640
          3. install /srv/exo-site/{dist,api,content,packages} (root-owned, read-only to exosite)
          4. venv /srv/exo-site/venv with pinned flask waitress requests eth-account eth-utils
          5. /var/lib/exo-presale and .cache: exosite, mode 700; claims.db 600 if present
          6. install exo-presale-api.service and /usr/local/bin/exo-presale-admin
          7. /etc/caddy/Caddyfile.exo; back up the main Caddyfile, append 'import /etc/caddy/Caddyfile.exo' once,
             caddy validate (restores both backups on failure), systemctl reload caddy
          8. systemctl enable + restart exo-presale-api; GET http://127.0.0.1:5310/api/sale on the host; free -m
PLAN
[[ -n "$DRY" ]] && exit 0
if [[ -z "$YES" ]]; then
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" == y || "$ans" == Y ]] || { echo "stopped"; exit 1; }
fi

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/exo-site-stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
"$PY" "$SITE/build.py" ${RELEASE:+$RELEASE} --out "$STAGE/dist" >/dev/null
mkdir -p "$STAGE/api" "$STAGE/packages/nownodes-py"
rsync -a --exclude __pycache__ "$SITE/api/exo_presale" "$STAGE/api/"
rsync -a --exclude __pycache__ "$REPO/packages/nownodes-py/exo_nownodes" "$STAGE/packages/nownodes-py/"
rsync -a "$SITE/content" "$STAGE/"
rsync -a --exclude rehearse-fork.sh "$SITE/deploy" "$STAGE/"
echo "built and staged in $STAGE"

rsync -az --delete "$STAGE/" "$SITE_HOST:exo-site-stage/"
ssh -t "$SITE_HOST" 'sudo bash "$HOME/exo-site-stage/deploy/remote-install.sh" "$HOME/exo-site-stage"'
