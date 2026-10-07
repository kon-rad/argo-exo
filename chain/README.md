# chain

ExoModule.sol, the agent Safe setup and the Chainlink CRE Transaction Guardian workflow.
Built from its own plan (exo-cre-workflow-and-skills-plan in the project notes). The deck's
`hooks/approve` signer, which executes Guardian-approved transactions, lives here too.

## ExoModule threat model

`chain/contracts/src/ExoModule.sol` executes a transaction through the agent Safe only if the Guardian approved its
exact hash, only when the deck hot key asks, at most once, and not while the module is frozen.

- **Native value is capped onchain, ERC-20 amounts are not.** The per-tx and per-day caps apply to ETH value only.
  Token amounts are bounded by the Guardian's rules and the human key.
- **Allowances outlive the transaction.** An approved `approve`, `permit` or `setApprovalForAll` lets the spender
  move funds later, without another Guardian approval, until the allowance is revoked.
- **The day cap is a UTC calendar window** (`block.timestamp / 1 days`), not a rolling 24 hours: up to twice the
  day cap can leave across a UTC midnight.
- **No self-calls.** `execute` refuses to target the Safe or the module itself, so an approval can't add owners,
  enable modules, set guards, change the threshold, or change the module's caps or deck.
- **Reports need an authenticated source.** While reports come through the permissionless MockKeystoneForwarder
  (simulated today), each must be sent from the `demoReporter` key (`tx.origin`). With no demo reporter and no
  workflow identity (expected workflow ID or author) configured, every report reverts.
- **A workflow identity only authenticates reports behind the real KeystoneForwarder.** The real forwarder verifies
  DON signatures before it passes the metadata on; the mock forwarder doesn't, so anyone calling it can forge
  any workflow ID or author and approve any hash (`test_hazard_identity_behind_mock_forwarder_is_forgeable`).
  - Never clear `demoReporter` while the mock forwarder is set.
  - Never call `setForwarderAddress(address(0))`: it removes the forwarder check entirely.
  - The simulator key is dedicated. Demo mode trusts `tx.origin`, so any transaction that key signs can deliver a
    report. It must sign nothing else.
- **Approvals are short-lived and revocable.** An approval must expire in the future and within 1 hour of landing.
  A refuse report for the same hash revokes it; an executed hash can never run again.
- **Freeze** comes from a Guardian report, the deck or the owner. Only the owner can unfreeze.

## Moving ExoModule from simulation to a DON

Owner actions, in exactly this order, each confirmed onchain before the next:

1. `setForwarderAddress(<real KeystoneForwarder for the chain>)`. Take the address from Chainlink's CRE docs
   (forwarder directory for that chain), not the mock forwarder used in simulation, and check it on a block explorer.
2. `setExpectedWorkflowId(<deployed workflow ID>)` and/or `setExpectedAuthor(<workflow owner address>)`.
3. Only then, `setDemoReporter(address(0))`.

Doing step 3 before step 1 leaves the module accepting forged reports from anyone. Every step needs Konrad's go-ahead.

## The ledger and guardian-run

`chain/ledger/*.sql` is the Postgres ledger: run `schema.sql`, `views.sql`, then `roles.sql` (it takes the two
passwords as psql variables; see its header). guardian-run (`chain/runner/exo_guardian`) writes as `exo_writer`; the
kiosk reads `exo_tx_v` and `exo_cre_calls_v` as `exo_reader`, which can't see the base tables.

`exo-bridge` serves the Guardian routes (`/guard`, `/approvals/pending`, `/approvals/<id>/executed`, `/freeze`) when
`EXO_LEDGER_WRITER_DSN` is set and `chain/runner` is on its `PYTHONPATH`; otherwise they answer 503. The bridge,
guardian-run and `cre` run as the `exoguard` user from its own checkout (`/srv/exo-guard/argo-exo`), which the
`hermes` user (every agent) can't read or write; the writer DSN lives only in exoguard's `bridge.env` (mode 600), and
the simulator key in exoguard's `chain/cre/.env` (mode 600) is Guardian-equivalent. See `agents/install-bridge.sh`.
The Hermes skill calls `exo_guardian/cli.py guard '<request json>'` from the hermes checkout with `EXO_BRIDGE_URL`
and the narrow `EXO_GUARD_TOKEN`; the CLI POSTs to the bridge's `/guard` and never touches the ledger. With that
token the bridge accepts only the sources `agent:<profile>`, `camera` and `dashboard`.

Guards run one at a time, end to end (a Postgres advisory lock from reading `spent_today_usd` to recording the
verdict), so two concurrent requests can't both spend the same daily headroom. The lock is never waited on: a
second guard gets 503 `{"error":"guardian busy"}` at once. `/freeze` doesn't take that lock.

An item reaches the deck's approval queue only when all of these hold:

| Check | Otherwise |
|---|---|
| The workflow answered `approve` for this proposal id, in a well-formed result | `refused` |
| The run was `--broadcast` (`EXO_GUARDIAN_BROADCAST=1`) | `simulated`, never queued |
| `report_tx` is a real, non-zero tx hash (the approval is onchain) | `refused` |
| The workflow's `tx_hash` equals the approval hash recomputed in Python from the exact to/value/data/salt sent, with `chainId` and `module` from `exo/config.mainnet.json` | `refused` ("approval hash mismatch") |
| The approval hasn't expired (the workflow's `expires_at`) | `refused`; `pending` never returns expired items |

The runner sets the salt (`os.urandom(32)`), the proposal id, `requested_at` and `context.spent_today_usd` (today's
UTC `usd_out` of executed and unexpired waiting approvals, from the ledger); the requester can't. A simulator
failure, timeout or unreadable output is a refusal recorded with its latency, and the bridge answers 502
`guardian unavailable`.

**`EXO_GUARDIAN_BROADCAST=1` sends real mainnet report transactions from the simulator key on every approval and
freeze** (a refusal writes no report). Leave it unset until Konrad gives the go-ahead. Without it, `/freeze` answers `{"ok": false, "simulated": true}`.

PgStore tests run only against a throwaway database (`EXO_TEST_PG_ADMIN_DSN`, `EXO_TEST_PG_WRITER_DSN`,
`EXO_TEST_PG_READER_DSN`; see `chain/runner/tests/guardian_pg.py`). They truncate the ledger.
