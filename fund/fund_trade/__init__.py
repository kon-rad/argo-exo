"""fund-trade: the deterministic, onchain half of Argo Fund (no LLM in here).

The committee skills decide; this package turns a passed motion into a Safe transaction, executes it once a second
owner has signed, and writes the plain-English trade log."""
