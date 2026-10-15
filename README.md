# argo-exo

<!-- TODO(konrad): rewrite this prose before the repo goes public. -->

TODO(konrad): Exo is a wearable Raspberry Pi. Push-to-talk speaks to your own Hermes agents. Its wallet is Argo Fund: five AI partners research and vote on trades, and the deck's approve key is one of the two signatures a trade needs.

## Repo map

| Path | What |
|---|---|
| `deck/` | Pi side: buttons, bin, collector, kiosk, voice, ring |
| `firmware/xiao-clip/` | Clip firmware |
| `fund/` | **Argo Fund**: five-partner AI investment committee (skills, personas), `fund-trade` CLI, 2-of-3 Safe on Base |
| `agents/bridge/` | exo-bridge service on the droplet (talk, kanban, fund routes) |
| `chain/` | Hackathon-era Guardian: ExoModule.sol and the Chainlink CRE workflow (not deployed; superseded by `fund/`) |
| `packages/` | NOWNodes client libraries |
| `infra/` | Proxy and deployment config |
| `site/` | Project site |
| `scripts/` | Repo tooling, including `check-public.sh` |
| `tests/` | Repo-level tests |
| `docs/` | Architecture notes |

See [docs/architecture.md](docs/architecture.md) for the data flow.

## Quick start

```bash
pip install -r requirements-dev.txt && pytest
```

## Argo Fund

The deck's wallet is a small crypto fund run by an AI committee. Details: [fund/README.md](fund/README.md); why we think it can work: [fund/PHILOSOPHY.md](fund/PHILOSOPHY.md).

- **Five partners** with clashing worldviews (macro, onchain data, risk, momentum, contrarian) write independent views, debate once and vote. A trade passes with 3 of 5; the constitution changes with 4 of 5.
- **Everything in plain English:** a constitution the committee amends, meeting minutes with every vote, and a trade log.
- **A Safe on Base with three owners, two signatures needed:** the agent (server), the deck's approve key, and the owner's phone. The agent can never move money alone.
- **Approve on the kiosk:** panel 6 (Fund) shows the waiting trade and how each partner voted; the approve key gets a fresh KyberSwap quote, checks the Safe transaction hash on the deck, and signs it.
- **Skills, not a service:** `fund-research`, `fund-committee`, `fund-trade`, `fund-review` are SKILL.md files that run under Claude Code (cron on the droplet) or Hermes.

## Deck notes

- `cyberdeck-dashboard.service` now runs the `exo_deck` kiosk. The unit name is kept so the existing Chromium kiosk needs no change.
- Pi-side prerequisite: `/usr/local/bin/deck-kiosk` is not in this repo (hand-installed on the Pi). The Kiosk double-tap calls it; `deck/install.sh` warns if it is missing.
- Testing hooks over SSH: load the env first, or the hooks see no keys and URLs: `set -a; . /srv/deck/.env; set +a`, then run e.g. `/srv/deck/hooks/talk-start`.

## Hackathon evidence (TOKEN2049, October 2026)

The CRE Guardian below was the hackathon build. It was never deployed to a Chainlink DON and has been superseded by Argo Fund; the code stays in `chain/` for reference.

Full detail and raw output: [docs/hackathon-evidence.md](docs/hackathon-evidence.md).

- **Chainlink CRE Transaction Guardian** (`chain/cre/exo`): decode → simulate on mainnet via NOWNodes `debug_traceCall` → Chainlink ETH/USD price → policy → two LLM judges → approval report to `ExoModule`.
- **Runs as a Confidential Workflow** (`handlerInTee`): the API keys and the wearer's policy are read inside the enclave.
- **Five live `cre workflow simulate` runs** ([output](docs/evidence/cre/)): approve "You pay 1 USDC to mira.eth. Nothing else changes."; refuse a camera-sourced `setApprovalForAll`; refuse over the $100 cap; refuse a recipient mismatch; freeze. About 4 s per verdict.
- **NOWNodes is the chain layer:** JSON-RPC on Ethereum, Base, Arbitrum and Polygon; four Blockbooks for multichain balances; the Trace API for the Guardian's simulation; receipt polling for "Confirmed" in the wearer's ear ([live check](docs/evidence/nownodes/live-check.txt)).
- **Live stack:** `exo-bridge`, the Hermes agents and the Postgres ledger run on a droplet over Tailscale. Talk round trip 2.8 s; the kiosk reads the agents' task board live ([smoke test](docs/evidence/stack/droplet-bridge-smoke.txt), [screenshots](docs/evidence/kiosk/)).
- **Tests green in CI:** 830 pytest, 112 bun, 69 forge, 47 node.
- **Not yet live:** mainnet `--broadcast` with a deployed `ExoModule` and Safe; the deck hardware with this build.
