#!/usr/bin/env bash
# Install exo-bridge and the Transaction Guardian for the dedicated `exoguard` user. Konrad runs it on the droplet:
#
#   sudo bash agents/install-bridge.sh
#
# Why a separate user: every Hermes agent runs as `hermes` and has file and terminal tools. Anything `hermes` can read
# (the full bridge token, the ledger writer DSN, the simulator key) or write (chain/runner, the CRE workflow) is
# effectively the agents'. So the bridge, guardian-run and `cre` run as `exoguard` from ITS OWN checkout,
# /srv/exo-guard/argo-exo (exoguard-owned, mode 750: hermes can neither read nor write it), cloned from the public
# remote, never from the hermes checkout. bridge.env and chain/cre/.env are exoguard-owned, mode 600. The Hermes
# env gets only EXO_GUARD_TOKEN, EXO_BRIDGE_URL, EXO_SAFE (+ EXO_AGENT_PROFILE, EXO_ADDRESS_BOOK): see
# agents/hermes.env.example.
#
# Needs: Tailscale up (the bridge binds the tailnet IP ONLY), git, openssl, python3, and the hermes gateway's
# api_server enabled (API_SERVER_KEY in ~hermes/.hermes/.env).
# It creates the user, the directories, the checkout, the venv, the env files and the kanban wrapper, and fixes
# their owners and modes. Tokens are written to the env file and never printed. It does NOT install the systemd
# unit, the sudoers rule, bun or cre, does not start anything and never touches the gateway: it prints those steps.
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

GUARD_USER=exoguard
GUARD_HOME=/srv/exo-guard
CHECKOUT=$GUARD_HOME/argo-exo
VENV=$GUARD_HOME/venv
CONF=$GUARD_HOME/config
ENV=$CONF/bridge.env
CRE_ENV=$CHECKOUT/chain/cre/.env
WRAPPER=/usr/local/bin/exo-hermes
REPO_URL="${EXO_GUARD_REPO_URL:-https://github.com/kon-rad/argo-exo.git}"
REF="${EXO_GUARD_REF:-main}"
HERMES_USER="${EXO_HERMES_USER:-hermes}"

[[ $EUID -eq 0 ]] || die "run as root: sudo bash $0"
cd /                                   # exoguard can't read root's cwd
as_guard() { runuser -u "$GUARD_USER" -- env HOME="$GUARD_HOME" "$@"; }
id "$HERMES_USER" >/dev/null 2>&1 || die "no $HERMES_USER user on this machine"
HERMES_HOME_DIR="$(getent passwd "$HERMES_USER" | cut -d: -f6)"
HERMES_ENV="$HERMES_HOME_DIR/.hermes/.env"
for c in git openssl python3 runuser; do command -v "$c" >/dev/null || die "$c missing"; done

command -v tailscale >/dev/null || die "tailscale is not installed. Install and join the tailnet first; the bridge never binds a public IP or 0.0.0.0."
TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
[[ "$TS_IP" =~ ^100\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "'tailscale ip -4' returned no tailnet address; run 'tailscale up' first."
grep -q '^API_SERVER_KEY=' "$HERMES_ENV" 2>/dev/null || die "API_SERVER_KEY missing from $HERMES_ENV; enable api_server first (agents/docs/board-conventions.md)"

# 1. the user: a system account with no login shell, home /srv/exo-guard. Never in the hermes group.
if id "$GUARD_USER" >/dev/null 2>&1; then
  echo "user $GUARD_USER exists"
else
  useradd --system --home-dir "$GUARD_HOME" --no-create-home --shell /usr/sbin/nologin "$GUARD_USER"
  echo "user $GUARD_USER created"
fi
if id -nG "$HERMES_USER" | tr ' ' '\n' | grep -qx "$GUARD_USER"; then
  die "$HERMES_USER is in the $GUARD_USER group; remove it (gpasswd -d $HERMES_USER $GUARD_USER) and rerun"
fi
install -d -m 750 -o "$GUARD_USER" -g "$GUARD_USER" "$GUARD_HOME"
install -d -m 700 -o "$GUARD_USER" -g "$GUARD_USER" "$CONF"

# 2. exoguard's own checkout, from the public remote (never from the hermes checkout, which agents can edit).
if [[ -d "$CHECKOUT/.git" ]]; then
  echo "checkout $CHECKOUT exists: kept. To update after reviewing the diff:"
  echo "  sudo -u $GUARD_USER git -C $CHECKOUT fetch && sudo -u $GUARD_USER git -C $CHECKOUT log --oneline HEAD..origin/$REF"
  echo "  sudo -u $GUARD_USER git -C $CHECKOUT merge --ff-only origin/$REF && sudo systemctl restart exo-bridge"
else
  as_guard git clone --quiet --branch "$REF" "$REPO_URL" "$CHECKOUT"
  echo "checkout $CHECKOUT at $(as_guard git -C "$CHECKOUT" rev-parse --short HEAD) ($REF from $REPO_URL)"
fi
chown -R "$GUARD_USER:$GUARD_USER" "$CHECKOUT"
chmod 750 "$CHECKOUT"
# The hermes user must not be able to write (or read) the code that decides approvals.
if runuser -u "$HERMES_USER" -- test -w "$CHECKOUT/chain/runner/exo_guardian" 2>/dev/null \
   || runuser -u "$HERMES_USER" -- test -r "$CHECKOUT/chain/runner/exo_guardian/service.py" 2>/dev/null; then
  die "$HERMES_USER can read or write $CHECKOUT; fix the permissions before going on"
fi

# 3. the venv (bridge + Guardian deps, pinned), owned by exoguard.
[[ -x "$VENV/bin/python" ]] || as_guard python3 -m venv "$VENV"
as_guard "$VENV/bin/pip" install -q --disable-pip-version-check -r "$CHECKOUT/agents/bridge/requirements.txt"
chmod 750 "$VENV"

# 4. the kanban wrapper: the bridge (as exoguard) runs `hermes kanban ...` AS hermes, through one sudoers rule.
cat > "$WRAPPER" <<WRAP
#!/bin/sh
# exo-bridge (user $GUARD_USER) -> the hermes kanban CLI, as $HERMES_USER. Allowed by /etc/sudoers.d/exo-bridge.
exec sudo -n -H -u $HERMES_USER $HERMES_HOME_DIR/.local/bin/hermes "\$@"
WRAP
chown root:root "$WRAPPER"; chmod 755 "$WRAPPER"

# 5. bridge.env (exoguard, 600). The tokens are generated here and never printed.
if [[ -f "$ENV" ]]; then
  echo "$ENV exists: kept (delete it to regenerate the tokens)"
else
  umask 077
  TOKEN="$(openssl rand -hex 32)"
  GUARD_TOKEN="$(openssl rand -hex 32)"
  KEY="$(grep '^API_SERVER_KEY=' "$HERMES_ENV" | head -1 | cut -d= -f2-)"
  KEY="${KEY%\"}"; KEY="${KEY#\"}"; KEY="${KEY%\'}"; KEY="${KEY#\'}"
  {
    echo "EXO_BRIDGE_TOKEN=$TOKEN"
    echo "# Narrow token: opens POST /guard only. This (with EXO_BRIDGE_URL and EXO_SAFE) is the ONLY bridge secret"
    echo "# that goes into the Hermes env for the exo-wallet skill; never EXO_BRIDGE_TOKEN."
    echo "EXO_GUARD_TOKEN=$GUARD_TOKEN"
    echo "EXO_BRIDGE_HOST=$TS_IP"
    echo "EXO_BRIDGE_PORT=8765"
    echo "EXO_BOARD=exo"
    echo "EXO_TASK_MAX_RUNTIME=30m"
    echo "EXO_TELEGRAM_CHAT_ID="
    echo "HERMES_BIN=$WRAPPER"
    echo "API_SERVER_URL=http://127.0.0.1:8642"
    echo "API_SERVER_KEY=$KEY"
    echo "# Writer DSN for the ledger (exo_writer): lives ONLY here. Empty = Guardian routes answer 503."
    echo "EXO_LEDGER_WRITER_DSN="
    echo "# Reader DSN (exo_reader, the two views only) for the kiosk's /ledger/* routes. Empty = they answer 503."
    echo "EXO_LEDGER_DSN="
    echo "# 1 = guard/freeze write real mainnet reports from the simulator key. Leave 0 until Konrad says go."
    echo "EXO_GUARDIAN_BROADCAST=0"
    echo "EXO_CRE_MANIFEST=$CHECKOUT/chain/cre/workflow-manifest.json"
    echo "# PYTHONPATH is set by the unit to $CHECKOUT/chain/runner."
  } > "$ENV"
  unset TOKEN GUARD_TOKEN KEY
  echo "wrote $ENV"
fi
chown "$GUARD_USER:$GUARD_USER" "$ENV"; chmod 600 "$ENV"

# 6. chain/cre/.env (the simulator key: Guardian-equivalent; exoguard, 600).
if [[ ! -f "$CRE_ENV" ]]; then
  install -m 600 -o "$GUARD_USER" -g "$GUARD_USER" "$CHECKOUT/chain/cre/.env.example" "$CRE_ENV"
  echo "wrote $CRE_ENV from .env.example (fill it: sudoedit $CRE_ENV)"
fi
chown "$GUARD_USER:$GUARD_USER" "$CRE_ENV"; chmod 600 "$CRE_ENV"

cat <<MSG

Done: user, checkout, venv, env files, wrapper. Next, as a sudoer (NOT run here):

  # a. the one sudoers rule (exoguard may run only the hermes kanban CLI, as hermes):
  echo '$GUARD_USER ALL=($HERMES_USER) NOPASSWD: $HERMES_HOME_DIR/.local/bin/hermes kanban *' | sudo tee /etc/sudoers.d/exo-bridge >/dev/null
  sudo chmod 440 /etc/sudoers.d/exo-bridge && sudo visudo -c
  # b. bun and the cre CLI as exoguard (into $GUARD_HOME/.bun/bin and $GUARD_HOME/.cre/bin, the unit's PATH), then log in:
  sudo -u $GUARD_USER -H bash -lc 'command -v bun cre'      # install both as $GUARD_USER until this prints two paths
  sudo -u $GUARD_USER -H bash -lc 'cre login'
  # c. fill the secrets (never as $HERMES_USER):
  sudoedit $ENV       # EXO_TELEGRAM_CHAT_ID, EXO_LEDGER_WRITER_DSN, EXO_LEDGER_DSN
  sudoedit $CRE_ENV   # CRE_ETH_PRIVATE_KEY (dedicated simulator key), NOWNODES_API_KEY, OPENROUTER_API_KEY, POLICY_JSON
  # d. the agents' narrow token into the Hermes env (copied without printing it), then EXO_BRIDGE_URL / EXO_SAFE by hand:
  sudo sh -c "grep '^EXO_GUARD_TOKEN=' $ENV >> $HERMES_ENV"
  # e. EXO_BRIDGE_TOKEN goes to the deck's /srv/deck/.env by hand. Never into the Hermes env.
  # f. the units (the NOWNodes proxy carries the simulator's report writes, so it is exoguard's too):
  sudo test -f $CONF/nownodes.env || sudo install -m 600 -o $GUARD_USER -g $GUARD_USER /dev/null $CONF/nownodes.env
  sudoedit $CONF/nownodes.env   # NOWNODES_API_KEY=
  sudo install -m 644 $CHECKOUT/infra/nownodes-proxy/nownodes-proxy.service /etc/systemd/system/
  sudo install -m 644 $CHECKOUT/agents/systemd/exo-bridge.service /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl enable --now nownodes-proxy exo-bridge
  systemctl is-active exo-bridge && sudo ss -ltnp | grep -E '8765|8642'
Expect 8765 on $TS_IP only and 8642 on 127.0.0.1 only. Anything on 0.0.0.0: stop and fix.
If an older install ran exo-bridge or nownodes-proxy as $HERMES_USER: once the new units are up, delete
$HERMES_HOME_DIR/.config/exo/bridge.env, $HERMES_HOME_DIR/.config/exo/nownodes.env and any chain/cre/.env in the
hermes checkout, and rotate every secret they held (bridge tokens, writer DSN password, simulator key).
MSG
