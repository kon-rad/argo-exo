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
- **Approvals are short-lived and revocable.** An approval must expire in the future and within 1 hour of landing.
  A refuse report for the same hash revokes it; an executed hash can never run again.
- **Freeze** comes from a Guardian report, the deck or the owner. Only the owner can unfreeze.
