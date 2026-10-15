#!/bin/sh
# Argo Fund on the droplet, as the hermes user (no root needed for these steps):
#   FUND_DIR=/home/hermes/secondBrain/Areas/argo/argo-exo/fund ./install.sh
# 1. links the four skills into Claude Code (~/.claude/skills) and Hermes (~/.hermes/skills/finance)
# 2. creates FUND_DIR from templates if it doesn't exist (never overwrites)
# 3. creates the agent key (~/.argo-fund/agent.key, mode 600) and prints its address
# 4. prints the crontab lines and the two steps that need Konrad (funding, and the bridge's sudo rule)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${FUND_DIR:?set FUND_DIR to the live fund folder in the vault}"
mkdir -p "$HOME/.claude/skills" "$HOME/.hermes/skills/finance"
for s in fund-research fund-committee fund-trade fund-review; do
  ln -sfn "$HERE/skills/$s" "$HOME/.claude/skills/$s"
  ln -sfn "$HERE/skills/$s" "$HOME/.hermes/skills/finance/$s"
done
mkdir -p "$FUND_DIR/meetings" "$FUND_DIR/research" "$FUND_DIR/reviews" "$FUND_DIR/state/proposals"
TODAY=$(date +%Y-%m-%d)
[ -f "$FUND_DIR/constitution.md" ] || sed "s/{{DATE}}/$TODAY/g" "$HERE/templates/constitution.md" > "$FUND_DIR/constitution.md"
[ -f "$FUND_DIR/trade-log.md" ] || cp "$HERE/templates/trade-log.md" "$FUND_DIR/trade-log.md"
mkdir -p "$HOME/.argo-fund" && chmod 700 "$HOME/.argo-fund"
if [ ! -x "$HOME/.argo-fund/venv/bin/python" ] && [ ! -x "$HERE/../.venv/bin/python" ]; then
  python3 -m venv "$HOME/.argo-fund/venv" && "$HOME/.argo-fund/venv/bin/pip" install -q -r "$HERE/requirements.txt"
fi
[ -f "$HOME/.argo-fund/env" ] || printf 'FUND_DIR=%s\nFUND_RPC_URL=https://mainnet.base.org\nFUND_DECK_OWNER=\nFUND_PHONE_OWNER=\nFUND_SAVINGS=\n' "$FUND_DIR" > "$HOME/.argo-fund/env"
AGENT=$("$HERE/bin/fund-trade" init-agent)
cat <<MSG

Skills linked. Fund folder: $FUND_DIR
Agent: $AGENT

Next (Konrad):
  1. Send ~0.001 ETH on Base to the agent address above (gas for creating the Safe and executing trades).
  2. Fill FUND_DECK_OWNER and FUND_PHONE_OWNER in ~/.argo-fund/env, then create the Safe:
       $HERE/bin/fund-trade create-safe
  3. Send 100 USDC on Base to the Safe address it prints.
  4. Deck: set EXO_FUND_SAFE=<safe> and EXO_FUND_SAVINGS=<the savings address in state/config.json> in the deck's
     environment (today the phone wallet, later the Pi Zero), then
     reinstall the deck hooks (deck/install.sh). Also set FUND_SAVINGS in ~/.argo-fund/env to the same address.
  5. Bridge (root): let the bridge user run fund-trade as hermes, then set FUND_TRADE_CMD in the bridge env:
       echo "exoguard ALL=(hermes) NOPASSWD: $HERE/bin/fund-trade" > /etc/sudoers.d/argo-fund
       FUND_TRADE_CMD="sudo -n -u hermes $HERE/bin/fund-trade"     (it reads ~hermes/.argo-fund/env)

Crontab (crontab -e as hermes). System cron runs in the server's clock; these lines are for a UTC server and
equal 08:00 / 09:00 / Sunday 10:00 in Asia/Tokyo. Shift them if the server or your timezone differs:
  0 23 * * *  FUND_DIR=$FUND_DIR $HERE/bin/fund-run fund-research
  0 0  * * *  FUND_DIR=$FUND_DIR $HERE/bin/fund-run fund-committee
  0 1  * * 0  FUND_DIR=$FUND_DIR $HERE/bin/fund-run fund-review
MSG
