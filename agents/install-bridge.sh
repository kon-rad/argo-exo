#!/usr/bin/env bash
# Install exo-bridge for the hermes user. Run on the droplet as `hermes`.
# Needs: Tailscale up (the bridge binds to the tailnet IP ONLY), openssl, python3.
# It installs deps, writes the env file (mode 600, token never printed) and prints the commands that need sudo.
# It does not touch the gateway and does not start anything.
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

command -v tailscale >/dev/null || die "tailscale is not installed. Install and join the tailnet first; the bridge never binds a public IP or 0.0.0.0."
TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
[[ "$TS_IP" =~ ^100\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "'tailscale ip -4' returned no tailnet address; run 'tailscale up' first."

command -v openssl >/dev/null || die "openssl missing"
HERMES_ENV="$HOME/.hermes/.env"
grep -q '^API_SERVER_KEY=' "$HERMES_ENV" 2>/dev/null || die "API_SERVER_KEY missing from $HERMES_ENV; enable api_server first (see agents/README steps)"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m venv "$HOME/.venvs/exo-bridge"
"$HOME/.venvs/exo-bridge/bin/pip" install -q -r "$REPO/agents/bridge/requirements.txt"

mkdir -p "$HOME/.config/exo" && chmod 700 "$HOME/.config/exo"
ENV="$HOME/.config/exo/bridge.env"
if [[ -f "$ENV" ]]; then
  echo "$ENV exists: kept (delete it to regenerate the token)"
else
  umask 077
  TOKEN="$(openssl rand -hex 32)"
  KEY="$(grep '^API_SERVER_KEY=' "$HERMES_ENV" | head -1 | cut -d= -f2-)"
  KEY="${KEY%\"}"; KEY="${KEY#\"}"; KEY="${KEY%\'}"; KEY="${KEY#\'}"
  {
    echo "EXO_BRIDGE_TOKEN=$TOKEN"
    echo "EXO_BRIDGE_HOST=$TS_IP"
    echo "EXO_BRIDGE_PORT=8765"
    echo "EXO_BOARD=exo"
    echo "EXO_TASK_MAX_RUNTIME=30m"
    echo "EXO_TELEGRAM_CHAT_ID="
    echo "HERMES_BIN=$HOME/.local/bin/hermes"
    echo "API_SERVER_URL=http://127.0.0.1:8642"
    echo "API_SERVER_KEY=$KEY"
    echo "# Writer DSN for the ledger: lives ONLY here. Empty = Guardian routes answer 503."
    echo "EXO_LEDGER_WRITER_DSN="
    echo "# Needed when the Guardian is enabled: path to the repo's chain/runner."
    echo "# PYTHONPATH=$REPO/chain/runner"
  } > "$ENV"
  chmod 600 "$ENV"
  unset TOKEN KEY
  echo "wrote $ENV (mode 600). Fill EXO_TELEGRAM_CHAT_ID and EXO_LEDGER_WRITER_DSN there; copy EXO_BRIDGE_TOKEN to the deck's .env by hand."
fi

[[ -e "$HOME/argo-exo" ]] || ln -s "$REPO" "$HOME/argo-exo"
echo "repo linked at $HOME/argo-exo (the unit's WorkingDirectory)"

cat <<MSG
Next, as a sudoer (not run here):
  sudo install -m 644 $REPO/agents/systemd/exo-bridge.service /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl enable --now exo-bridge
  systemctl is-active exo-bridge && sudo ss -ltnp | grep -E '8765|8642'
Expect 8765 on $TS_IP only and 8642 on 127.0.0.1 only. Anything on 0.0.0.0: stop and fix.
MSG
