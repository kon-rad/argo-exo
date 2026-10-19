#!/usr/bin/env bash
# Installs m1-jobd on the M1 Max. Run there as konrad from the vault: Areas/argo/argo-exo/argo-exo/agents/bot-tools/install-m1.sh
# The allowlist is copied only when missing (it is the M1's own file afterwards); pass --force-jobs to replace it.
# The authorized_keys line is the owner's step (printed by install.sh on the droplet).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p ~/.local/bin ~/.config/m1-jobs ~/m1-jobs
install -m 755 "$HERE/m1_jobd.py" ~/.local/bin/m1-jobd
if [[ ! -f ~/.config/m1-jobs/jobs.toml || "${1:-}" == "--force-jobs" ]]; then
  install -m 644 "$HERE/m1-jobs.toml" ~/.config/m1-jobs/jobs.toml
fi
~/.local/bin/m1-jobd list
