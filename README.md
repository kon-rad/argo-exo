# argo-exo

<!-- TODO(konrad): rewrite this prose before the repo goes public. -->

TODO(konrad): Exo is a wearable Raspberry Pi. Push-to-talk speaks to your own Hermes agents, and the wallet agent's transactions are guarded by a Chainlink CRE workflow.

## Repo map

| Path | What |
|---|---|
| `deck/` | Pi side: buttons, bin, collector, kiosk, voice, ring |
| `firmware/xiao-clip/` | Clip firmware |
| `agents/bridge/` | exo-bridge service on the droplet |
| `chain/` | ExoModule.sol, agent Safe, CRE Transaction Guardian |
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

## Deck notes

- `cyberdeck-dashboard.service` now runs the `exo_deck` kiosk. The unit name is kept so the existing Chromium kiosk needs no change.
- Pi-side prerequisite: `/usr/local/bin/deck-kiosk` is not in this repo (hand-installed on the Pi). The Kiosk double-tap calls it; `deck/install.sh` warns if it is missing.
- Testing hooks over SSH: load the env first, or the hooks see no keys and URLs: `set -a; . /srv/deck/.env; set +a`, then run e.g. `/srv/deck/hooks/talk-start`.

## Hackathon evidence

Full detail and raw output: [docs/hackathon-evidence.md](docs/hackathon-evidence.md).

- **Chainlink CRE Transaction Guardian** (`chain/cre/exo`): decode → simulate on mainnet via NOWNodes `debug_traceCall` → Chainlink ETH/USD price → policy → two LLM judges → approval report to `ExoModule`.
- **Runs as a Confidential Workflow** (`handlerInTee`): the API keys and the wearer's policy are read inside the enclave.
- **Five live `cre workflow simulate` runs** ([output](docs/evidence/cre/)): approve "You pay 1 USDC to mira.eth. Nothing else changes."; refuse a camera-sourced `setApprovalForAll`; refuse over the $100 cap; refuse a recipient mismatch; freeze. About 4 s per verdict.
- **NOWNodes is the chain layer:** JSON-RPC on Ethereum, Base, Arbitrum and Polygon; four Blockbooks for multichain balances; the Trace API for the Guardian's simulation; receipt polling for "Confirmed" in the wearer's ear ([live check](docs/evidence/nownodes/live-check.txt)).
- **Live stack:** `exo-bridge`, the Hermes agents and the Postgres ledger run on a droplet over Tailscale. Talk round trip 2.8 s; the kiosk reads the agents' task board live ([smoke test](docs/evidence/stack/droplet-bridge-smoke.txt), [screenshots](docs/evidence/kiosk/)).
- **Tests green in CI:** 830 pytest, 112 bun, 69 forge, 47 node.
- **Not yet live:** mainnet `--broadcast` with a deployed `ExoModule` and Safe; the deck hardware with this build.
