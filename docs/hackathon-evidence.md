# Hackathon evidence

What has run against real infrastructure, with the raw output. Recorded 2026-10-07 during TOKEN2049 Origins.

## Chainlink CRE: the Transaction Guardian workflow

`chain/cre/exo` is a TypeScript CRE workflow with two HTTP triggers. **guard** judges a transaction an agent wants to send from the wearer's Safe. **freeze** stops the module. A guard run does six things:

1. Decodes the call.
2. Simulates it on Ethereum mainnet with `debug_traceCall` through NOWNodes, and reads the balance changes from the trace.
3. Prices the outflow with the Chainlink ETH/USD feed (`latestRoundData`, read through NOWNodes).
4. Applies the wearer's policy: caps, the address book, sources, approvals.
5. Asks two LLM judges from different vendors through OpenRouter.
6. Writes an approval report to `ExoModule` (`chain/contracts/src/ExoModule.sol`), the Safe module that executes only the hashes it approved.

The guard handler runs as a **Confidential Workflow** (`handlerInTee`, `USE_TEE = true` in `main.ts`). The NOWNodes and OpenRouter keys and the wearer's policy are secrets read inside the enclave. Only the report payload leaves it.

| Run | Request | Verdict | Output |
|---|---|---|---|
| 1 | An agent sends 1 USDC to an address-book contact | **approve**: "You pay 1 USDC to mira.eth. Nothing else changes." | [1-send-usdc-approve.txt](evidence/cre/1-send-usdc-approve.txt) |
| 2 | A QR code scanned by the camera asks for `setApprovalForAll` | **refuse**: camera source, plus an unlimited NFT approval | [2-camera-approval-for-all.txt](evidence/cre/2-camera-approval-for-all.txt) |
| 3 | 5,000 USDC to the same contact | **refuse**: over the $100 per-transaction cap | [3-over-cap.txt](evidence/cre/3-over-cap.txt) |
| 4 | The stated recipient differs from the calldata's recipient | **refuse**: doesn't match the request | [4-recipient-mismatch.txt](evidence/cre/4-recipient-mismatch.txt) |
| 5 | Panic freeze | `{"ok": true}` | [5-freeze.txt](evidence/cre/5-freeze.txt) |

**How these were run:** CRE CLI v1.37.0, `cre workflow simulate` with `--target mainnet`.
- **Inputs:** the payloads in [`evidence/cre/payloads/`](evidence/cre/payloads/) and the config in [`evidence/cre/config.evidence.json`](evidence/cre/config.evidence.json).
- **Dry runs:** no `--broadcast`, so `report_tx` is the zero hash and no gas was spent.
- **Real data:** the traces, the price feed and both judges were live calls.
- **Placeholders:** the config points `safe` at a well-known USDC-holding mainnet address so the simulated transfer has funds to trace, and `module` at a placeholder. No Safe or module of ours is deployed yet.
- **Key:** the simulator key was a throwaway, never funded.

**Latency:** about 4 s per verdict once the workflow is prebuilt to WASM (`chain/runner/exo_guardian/simulate.py`, `ensure_wasm`); 28 s when compiling every call.

**How the deployed system uses it:** `guardian-run` runs the same command on the droplet, as its own `exoguard` user. It parses the result marker and records every verdict in Postgres. An approval reaches the deck's approval queue only from a `--broadcast` run, whose `report_tx` is a real transaction hash. The wearer then presses the approve key, and the deck's hot key calls `ExoModule.execute`.

## NOWNodes: which endpoint powers what

| Endpoint | Used by | For |
|---|---|---|
| `eth.nownodes.io` (JSON-RPC, Trace API) | CRE workflow (`chain/cre/exo/src/lib/http.ts`, `guard.ts`) | `debug_traceCall` simulation of every proposed transaction; the ETH/USD feed read |
| `eth.nownodes.io` | Deck: `approve_hook.py`, `freeze.py`, `queue_sync.py`, `confirm.py` | Re-check `ExoModule` before signing, broadcast `execute` / `freeze`, settle and announce from receipts |
| `eth.nownodes.io` via `infra/nownodes-proxy` | `cre` (`project.yaml`), Foundry | Loopback proxy that adds the `api-key` header for tools that only take a bare RPC URL |
| `base.nownodes.io` | Pre-sale API (`site/api/exo_presale/rpc.py`) | Reads `ExoPreorder` on Base: sale state, ownership for shipping claims |
| `eth-blockbook`, `base-blockbook`, `arb-blockbook`, `maticbook` `.nownodes.io` | Kiosk wallets panel (`deck/exo_deck/chain_view.py`); Hermes `nownodes-chain` skill (`agents/skills/nownodes-chain/chain.py`) | Multichain balances and history for the wearer's wallets, on the glasses and by voice |
| Blockbook WebSocket | `deck-confirm` with `EXO_CONFIRM_MODE=wss` | Live notifications for watched addresses. Needs NOWNodes Pro: Start answers 403, so the default is receipt polling |

Clients: `packages/nownodes-py` and `packages/nownodes-ts`. The key goes only in the `api-key` header and is redacted from errors and logs. Live check of every host, chain IDs, Blockbook lookups and traces: [evidence/nownodes/live-check.txt](evidence/nownodes/live-check.txt).

## The running stack

| Piece | State | Evidence |
|---|---|---|
| `exo-bridge` on the droplet (Tailscale only), Hermes agents, the `exo` kanban board, the Postgres ledger | Running | [evidence/stack/droplet-bridge-smoke.txt](evidence/stack/droplet-bridge-smoke.txt): talk round trip 2.8 s, a task delegated to the `researcher` agent |
| Kiosk (the glasses UI) reading the droplet over Tailscale | Running | [agents panel](evidence/kiosk/agents-live-droplet.png), [CRE panel](evidence/kiosk/cre-live-droplet.png) |
| Tests | Green in CI | 830 pytest, 112 bun (CRE workflow), 42 + 27 forge, 47 node |

## Not yet run live

- **Guardian on mainnet:** `--broadcast`, a deployed `ExoModule` and Safe, and a funded simulator key. `EXO_GUARDIAN_BROADCAST` is off until then.
- **The guard call on the droplet itself:** it needs `cre` credentials there.
- **The deck hardware:** the Pi is not online with this build yet.
