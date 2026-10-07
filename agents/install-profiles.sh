#!/usr/bin/env bash
# Create or update the Exo profiles and the `exo` board. Run as the hermes user on the droplet.
#
#   bash install-profiles.sh            # create missing profiles; SKIP any that already exist
#   bash install-profiles.sh --force    # for existing profiles: back up SOUL.md + description, then replace
#
# The droplet already has a `researcher` profile (plus argo-cmo, argo-coder, video-editor). Without --force this
# script leaves an existing profile's files untouched and says so. With --force the old SOUL.md is copied to
# SOUL.md.bak-<timestamp> first. Never starts or restarts a gateway.
set -euo pipefail

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

die() { echo "ERROR: $*" >&2; exit 1; }

command -v hermes >/dev/null || die "hermes not on PATH (run as the hermes user, login shell)"

# Probe every subcommand used below; abort rather than guess.
probe() { hermes "$@" --help >/dev/null 2>&1 || die "this hermes has no 'hermes $*'; check 'hermes --help' and adjust the script"; }
probe profile create
probe profile list
probe profile describe
probe kanban boards list
probe kanban boards create

cd "$(dirname "$0")/profiles"
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
existing=$(hermes profile list 2>/dev/null || true)
stamp=$(date +%Y%m%d-%H%M%S)

for dir in */; do
  name="${dir%/}"
  desc="$(tr -d '\n' < "$name/description.txt")"
  target="$HOME_DIR/profiles/$name"
  if grep -qw -- "$name" <<<"$existing"; then
    if [[ $FORCE -eq 0 ]]; then
      echo "profile $name EXISTS: left untouched (re-run with --force to back up and replace its SOUL.md and description)"
      continue
    fi
    [[ -f "$target/SOUL.md" ]] && cp -p "$target/SOUL.md" "$target/SOUL.md.bak-$stamp" && echo "profile $name: backed up SOUL.md.bak-$stamp"
  else
    hermes profile create "$name" --no-alias --description "$desc"
  fi
  hermes profile describe "$name" --text "$desc"
  install -m 644 "$name/SOUL.md" "$target/SOUL.md"
  echo "profile $name ok"
done

hermes kanban boards list | grep -qw exo || hermes kanban boards create exo
echo "board exo ok"
