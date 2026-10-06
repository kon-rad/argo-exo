# argo-exo

<!-- TODO(konrad): rewrite this prose before the repo goes public. -->

TODO(konrad): Exo is a wearable Raspberry Pi. Push-to-talk speaks to your own Hermes agents, and the wallet agent's transactions are guarded by a Chainlink CRE workflow.

## Repo map

| Path | What |
|---|---|
| `deck/` | Pi side: buttons, bin, collector, dashboard, voice, ring |
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

## Built before vs during the hackathon

| Component | Status |
|---|---|
| `deck/buttons` | Existed before 2026-10-06 |
| `deck/bin` | Existed before 2026-10-06 |
| `deck/collector` | Existed before 2026-10-06 |
| `deck/dashboard` (base) | Existed before 2026-10-06 |
| `deck/ring` | Existed before 2026-10-06 |
| `firmware/xiao-clip` | Existed before 2026-10-06 |
| Everything else | Built during the hackathon |
