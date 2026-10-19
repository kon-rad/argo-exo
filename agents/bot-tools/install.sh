#!/usr/bin/env bash
# Installs the bot tools on the droplet. Run as root from the argo-exo checkout: sudo agents/bot-tools/install.sh
#
#   /usr/local/lib/argo-bot-tools/bot_tools.py  root-owned; /usr/local/bin/{cli-git,cli-deploy,m1-job} link to it
#   /etc/secondbrain/bot-repos.toml, deploy-targets.toml   root-owned registries (overwritten from this folder)
#   ~hermes/.ssh/m1_jobs                         the key the M1 runs m1-jobd for (printed line goes in the M1's
#                                                authorized_keys, by the owner)
#
# Not done here, by design: the GitHub token (the owner writes ~hermes/.config/bot-github/token, mode 600).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
APP_USER="${APP_USER:-hermes}"
HOMEDIR="$(getent passwd "$APP_USER" | cut -d: -f6)"

install -d -o root -g root -m 755 /usr/local/lib/argo-bot-tools
install -o root -g root -m 755 "$HERE/bot_tools.py" /usr/local/lib/argo-bot-tools/bot_tools.py
for n in cli-git cli-deploy m1-job; do ln -sfn /usr/local/lib/argo-bot-tools/bot_tools.py "/usr/local/bin/$n"; done
install -d -o root -g root -m 755 /etc/secondbrain
install -o root -g root -m 644 "$HERE/bot-repos.toml" /etc/secondbrain/bot-repos.toml
install -o root -g root -m 644 "$HERE/deploy-targets.toml" /etc/secondbrain/deploy-targets.toml

install -d -o "$APP_USER" -g "$APP_USER" -m 700 "$HOMEDIR/.config/bot-github"
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$HOMEDIR/cc-work/bots"
if [[ ! -f "$HOMEDIR/.ssh/m1_jobs" ]]; then
  sudo -u "$APP_USER" ssh-keygen -q -t ed25519 -N '' -C "hermes@droplet m1-jobs" -f "$HOMEDIR/.ssh/m1_jobs"
fi
if ! sudo -u "$APP_USER" grep -q '^m1max ' "$HOMEDIR/.ssh/known_hosts" 2>/dev/null; then
  sudo -u "$APP_USER" bash -c "ssh-keyscan -T 5 -t ed25519 m1max >> '$HOMEDIR/.ssh/known_hosts' 2>/dev/null" || true
fi

echo "installed: cli-git, cli-deploy, m1-job; registries in /etc/secondbrain"
[[ -s "$HOMEDIR/.config/bot-github/token" ]] && echo "GitHub token: present" || echo "GitHub token: MISSING (owner: $HOMEDIR/.config/bot-github/token, mode 600, owner $APP_USER)"
echo "M1 authorized_keys line (owner adds it on the M1):"
echo "command=\"/Users/konrad/.local/bin/m1-jobd\",restrict $(cat "$HOMEDIR/.ssh/m1_jobs.pub")"
