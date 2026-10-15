---
name: fund-trade
description: The Argo Fund's onchain tool — check the fund's Safe and portfolio, record a committee-passed trade as a proposal, and (for the bridge) prepare, execute or refuse it. Deterministic CLI, no LLM. Use when a skill or person needs the fund's holdings/NAV, pending proposals, the trade log, or to propose a trade the committee passed. Never use it to execute a trade on your own: execution needs Konrad's signature from the kiosk or phone.
---

# fund-trade

`$FUND_HOME/bin/fund-trade <command>` prints one JSON object (errors: `{"error": ...}`, exit 1).

| Command | Who uses it | Does |
|---|---|---|
| `status` | anyone | Safe address, owners, threshold, agent gas, pending proposals, rules in force |
| `portfolio` | committee, research, review | Holdings, USD values, NAV |
| `proposals [--all]` | committee, kiosk | Open proposals (or every proposal) |
| `show <id>` | anyone | One proposal with its votes |
| `log [--n N]` | kiosk, review | Newest trade-log entries, NAV line, current thesis |
| `propose --meeting D --motion T --sell S --buy B --amount A --votes JSON` | committee only | Records a passed trade (needs ≥ 3 yes, no block) |
| `prepare <id>` | the bridge, when Konrad presses approve | Fresh KyberSwap quote → Safe transaction → hash to sign |
| `execute <id> <signature>` | the bridge, with the deck's signature | Adds the agent's signature, submits, writes the trade log |
| `withdraw --token T --amount A\|all [--reason R]` | Konrad, or the committee for a passed profit-taking motion (add `--meeting D --votes JSON`) | Proposes moving funds to the savings vault (the Pi Zero's address, `FUND_SAVINGS`). Same approval as a trade; the deck refuses any transfer that isn't to its own savings address |
| `refuse <id> [--reason R]` | the bridge / Konrad | Marks the proposal refused and logs it |
| `init-agent`, `create-safe` | setup only | Agent key; the 2-of-3 Safe (agent, deck, phone) |

## How a trade happens
1. The committee passes a motion → `propose` → status `proposed`.
2. Konrad presses approve on the kiosk's Fund panel → the bridge runs `prepare` → the deck checks the hash itself and signs it → the bridge runs `execute` → the agent adds its signature and submits.
3. The trade log gets a plain-English entry; the NAV line updates.

The Safe needs **2 of 3** owner signatures. The agent is one owner, so it can never move funds alone. Don't try to work around that.

## Environment
`FUND_DIR` (required), `FUND_RPC_URL` (default `https://mainnet.base.org`), `FUND_AGENT_KEY` (default `~/.argo-fund/agent.key`), `FUND_DECK_OWNER`, `FUND_PHONE_OWNER` (only needed for `create-safe`).

## Rules
None enforced yet (`fund_trade/rules.py` is an empty hook; Konrad waived them while the fund is ~100 USD). Every trade-log entry records "Rules: none (MVP: waived)" so that's visible.
