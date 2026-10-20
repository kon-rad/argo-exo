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

# gitleaks: cli-git scans every push to a public repo with it and refuses to push without it. Pinned and hash-checked.
GITLEAKS_VERSION=8.30.1
GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb   # gitleaks_8.30.1_linux_x64.tar.gz
if ! /usr/local/bin/gitleaks version 2>/dev/null | grep -q "$GITLEAKS_VERSION"; then
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/gl.tgz" "https://github.com/gitleaks/gitleaks/releases/download/v$GITLEAKS_VERSION/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
  echo "$GITLEAKS_SHA256  $tmp/gl.tgz" | sha256sum -c --quiet
  tar -xzf "$tmp/gl.tgz" -C "$tmp" gitleaks
  install -o root -g root -m 755 "$tmp/gitleaks" /usr/local/bin/gitleaks
  rm -rf "$tmp"
fi

install -d -o "$APP_USER" -g "$APP_USER" -m 700 "$HOMEDIR/.config/bot-github"
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$HOMEDIR/cc-work/bots"
if [[ ! -f "$HOMEDIR/.ssh/m1_jobs" ]]; then
  sudo -u "$APP_USER" ssh-keygen -q -t ed25519 -N '' -C "hermes@droplet m1-jobs" -f "$HOMEDIR/.ssh/m1_jobs"
fi
if ! sudo -u "$APP_USER" grep -q '^m1max ' "$HOMEDIR/.ssh/known_hosts" 2>/dev/null; then
  sudo -u "$APP_USER" bash -c "ssh-keyscan -T 5 -t ed25519 m1max >> '$HOMEDIR/.ssh/known_hosts' 2>/dev/null" || true
fi

echo "installed: cli-git, cli-deploy, m1-job, gitleaks $(/usr/local/bin/gitleaks version); registries in /etc/secondbrain"
[[ -s "$HOMEDIR/.config/bot-github/token" ]] && echo "GitHub token: present" || echo "GitHub token: MISSING (owner: the bot account's classic token, scopes repo + read:org, no delete_repo; $HOMEDIR/.config/bot-github/token, mode 600, owner $APP_USER)"
echo "M1 authorized_keys line (owner adds it on the M1):"
echo "command=\"/Users/konrad/.local/bin/m1-jobd\",restrict $(cat "$HOMEDIR/.ssh/m1_jobs.pub")"
