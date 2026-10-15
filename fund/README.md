# Argo Fund

A small crypto fund run by five AI partners and one human. The partners research, debate and vote; passed trades wait in a Safe until the human approves them; everything is written down in plain English.

- **Why it might work:** [PHILOSOPHY.md](PHILOSOPHY.md) (principles first, after Ray Dalio; a long-term core plus a researched frontier)
- **Design:** `plans/11-argo-fund-design.md` in the project notes

## How it works

```
fund-research   ─► research notes
fund-committee  ─► five partners write views, debate, vote ─► meeting minutes
                   a trade passes with 3 of 5 yes ─► fund-trade propose ─► waiting for approval
kiosk / phone   ─► the human reads the motion and every vote ─► approve
fund-trade      ─► fresh KyberSwap quote → Safe transaction → 2 of 3 owner signatures → executed → trade log
fund-review     ─► weekly scoreboard vs hold-USDC / hold-ETH / 50-50, partner believability, lessons
```

The fund's wallet is a **Safe with three owners, two signatures required**: the agent (on the server), the deck (a hardware approve key) and the human's phone wallet. The agent proposes and pays gas, but it can never move money alone.

## What's here

| Path | What |
|---|---|
| `personas/` | The five partners: Wren Hollis (macro), Kaito Brandt (onchain data), Odalys Fen (risk), Juno Achebe (momentum and frontier), Basile Marr (contrarian) |
| `skills/` | `fund-research`, `fund-committee`, `fund-trade`, `fund-review` as SKILL.md files. They run under Claude Code or Hermes unchanged |
| `fund_trade/` | The deterministic CLI behind `fund-trade`: Safe creation, SafeTx hashing and signing, KyberSwap on Base, proposals, the trade log |
| `templates/` | Starting constitution, trade log, meeting minutes |
| `bin/fund-trade`, `bin/fund-run` | The CLI wrapper; the headless Claude Code runner for cron |
| `install.sh` | Server install (skills, fund folder, agent key, cron lines) |
| `tests/` | Unit tests, plus an end-to-end test on an Anvil fork of Base (`FUND_FORK_TESTS=1`) |

## Run your own

```sh
export FUND_DIR=~/my-fund                 # where the constitution, trade log and minutes live
fund/install.sh                           # links skills, creates the fund folder and the agent key
# fill FUND_DECK_OWNER / FUND_PHONE_OWNER in ~/.argo-fund/env (your two human-held wallets)
fund/bin/fund-trade create-safe           # deploys the 2-of-3 Safe on Base; the agent pays the gas
# send USDC on Base to the Safe, then:
fund/bin/fund-run fund-committee          # or ask Claude Code: /fund-committee
```

Approving: any second owner can sign. The Exo deck does it with its approve key (`deck/exo_deck/fund_sign.py`); without a deck, sign the SafeTx hash from `fund-trade prepare` with your own wallet and pass it to `fund-trade execute`.

## Status

MVP. Trading rules are an empty hook (`fund_trade/rules.py`): the 2-of-3 Safe and the human approval are the safety while the fund is small. Spot swaps on Base only.

Not financial advice. This is an experiment in process, transparency and learning, run with an amount its owner can afford to lose.
