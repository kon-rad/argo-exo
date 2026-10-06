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
