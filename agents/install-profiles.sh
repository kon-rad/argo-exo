#!/usr/bin/env bash
# Create or update the Exo profiles and the `exo` board. Run as the hermes user on the droplet.
#
#   bash install-profiles.sh            # create missing profiles; SKIP any that already exist
#   bash install-profiles.sh --force    # for existing profiles: back up SOUL.md + description, then replace
#
# The droplet already has a `researcher` profile (plus argo-cmo, argo-coder, video-editor). Without --force this
# script leaves an existing profile's files untouched and says so. With --force the old SOUL.md is copied to
# SOUL.md.bak-<timestamp> first (only SOUL.md is backed up; the description lives in hermes and is overwritten).
# Never starts or restarts a gateway. Each profile has its OWN gateway (default running, others stopped); this
# script never starts a profile's gateway.
#
# Verified on the droplet (Hermes v0.19.0, real --help):
#   hermes profile create [--no-alias] [--description D] NAME
#   hermes profile describe NAME --text T
#   hermes profile list            (prints a TABLE: Profile, Model, Gateway, Alias, Distribution; active prefixed ◆)
#   hermes config set KEY VALUE
#   hermes gateway restart
#   hermes kanban boards create SLUG   (and: boards list)
# Profiles live in ~/.hermes/profiles/<name>; existence is tested on that directory, not by grepping the table.
set -euo pipefail

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

die() { echo "ERROR: $*" >&2; exit 1; }

command -v hermes >/dev/null || die "hermes not on PATH (run as the hermes user, login shell)"

# Probe every subcommand used below; abort rather than guess.
probe() { hermes "$@" --help >/dev/null 2>&1 || die "this hermes has no 'hermes $*'; check 'hermes --help' and adjust the script"; }
probe profile create
probe profile describe
probe kanban boards list
probe kanban boards create

AGENTS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$AGENTS_DIR/profiles"
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
stamp=$(date +%Y%m%d-%H%M%S)

for dir in */; do
  name="${dir%/}"
  desc="$(tr -d '\n' < "$name/description.txt")"
  target="$HOME_DIR/profiles/$name"
  if [[ -d "$target" ]]; then
    if [[ $FORCE -eq 0 ]]; then
      echo "profile $name EXISTS: left untouched (re-run with --force to back up and replace its SOUL.md and description)"
      continue
    fi
    [[ -f "$target/SOUL.md" ]] && cp -p "$target/SOUL.md" "$target/SOUL.md.bak-$stamp" && echo "profile $name: backed up SOUL.md.bak-$stamp"
  else
    hermes profile create --no-alias --description "$desc" "$name"
  fi
  hermes profile describe "$name" --text "$desc"
  install -m 644 "$name/SOUL.md" "$target/SOUL.md"
  echo "profile $name ok"
done

# Link skills into the profiles that use them. exo-wallet (agents propose transactions only through the Guardian) goes
# into the money agents and the default profile; nownodes-chain (read-only balances and history) goes into the wallet
# and default profiles. Symlinks to this checkout, so a git pull updates them. Existing profiles are linked too: linking
# never touches SOUL.md. A real directory already at the link path is left alone. Never starts a gateway.
link_skill() {  # $1 = skill name, $2 = skills directory to link into
  local dest="$2/$1"
  mkdir -p "$2"
  if [[ -e "$dest" && ! -L "$dest" ]]; then
    echo "skill $1: $dest exists and is not a symlink; left untouched"
    return
  fi
  ln -sfn "$AGENTS_DIR/skills/$1" "$dest"
  echo "skill $1 linked: $dest"
}
for name in trader portfolio wallet; do
  [[ -d "$HOME_DIR/profiles/$name" ]] && link_skill exo-wallet "$HOME_DIR/profiles/$name/skills"
done
link_skill exo-wallet "$HOME_DIR/skills"   # the default profile
[[ -d "$HOME_DIR/profiles/wallet" ]] && link_skill nownodes-chain "$HOME_DIR/profiles/wallet/skills"
link_skill nownodes-chain "$HOME_DIR/skills"

boards="$(hermes kanban boards list 2>/dev/null || true)"
if ! grep -Eq '(^|[[:space:]])exo([[:space:]]|$)' <<<"$boards"; then
  hermes kanban boards create exo
fi
echo "board exo ok"
