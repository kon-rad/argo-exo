# chain/cre — the Transaction Guardian workflow

CRE project `cre` with one TypeScript workflow, `exo` (`@chainlink/cre-sdk` 1.22.0).

| Trigger | Handler | Input | Output |
|---|---|---|---|
| 0 | `guard` | §4.3 request (+ `requested_at`, `context.spent_today_usd`) | §4.3 result JSON; a kind-1 (approve) or kind-2 (refuse) report to ExoModule |
| 1 | `freeze` | `{"reason"}` | `{"ok", "report_tx"}`; a kind-3 report |

`exo/main.ts` only adapts the CRE runtime. The pipeline is `exo/src/lib/guard.ts` (`runGuard`, `runFreeze`), and
every decision is in `exo/src/lib` (bun-tested). Any error before the verdict is a refusal.

**Plain handler vs. TEE.** `USE_TEE` at the top of `main.ts` is `false`: `guard` runs as a plain `handler`, with HTTP
through node mode and identical consensus. Set it to `true` only once Confidential Workflows beta access is
confirmed. `guard` then runs in `handlerInTee`, keys and policy stay in the enclave, and only the report crosses
to the DON.

These files were written by hand from the CRE docs. `cre init` was never run here, so diff them against a fresh
`cre init` (TypeScript) before trusting them:

| File | Doc section |
|---|---|
| `project.yaml` | Project Configuration §3.1 |
| `exo/workflow.yaml` | Part 1 Step 4 (generated layout), Project Configuration §3.2 |
| `secrets.yaml` | Using Secrets in Simulation, Step 1 |

## Before the first simulation (Konrad)

1. Install the `cre` CLI and run `cre login` yourself. Neither was done here.
2. Set up the workflow:

   ```bash
   cd chain/cre/exo
   bun install
   bunx cre-setup
   ```

   `bunx cre-setup` downloads the Javy WASM toolchain. `cre init`'s template runs it as a postinstall; it is left
   out here so CI doesn't download it.
3. Fill `chain/cre/.env` from `.env.example`. Use a dedicated `CRE_ETH_PRIVATE_KEY`. `POLICY_JSON` must be one line
   and match `PolicySchema` exactly (unknown keys are refused). For example:

   ```
   POLICY_JSON={"address_book":{"0x…mira":"mira.eth"},"max_usd_per_tx":100,"max_usd_per_day":300,"auto_max_usd":25,"allow_unlimited_approvals":false,"allow_approval_for_all":false,"refuse_sources":["camera"],"require_known_recipient_over_usd":50,"stablecoins":["0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48","0xdac17f958d2ee523a2206206994597c13d831ec7","0x6b175474e89094c44da98b954eedeac495271d0f"]}
   ```

4. Fill these TODOs:

   - `exo/config.mainnet.json`:
     - `module`: TODO(konrad), the ExoModule address. Any address will do for a dry run.
     - `safe`: TODO(konrad), the agent Safe.
     - `judges`: TODO(konrad), confirm the two models. They must come from different vendors and answer within CRE's 10 s HTTP cap.
     - `ethUsdFeed`: check it on data.chain.link.
   - `exo/payloads/*.json`: replace `from` with the Safe, and the recipient with mira's address. Regenerate the data with `cast calldata "transfer(address,uint256)" <mira> 1000000`, and make a new salt with `cast keccak $(date +%s%N)`.

   While `module` or `safe` is still the zero placeholder, every guard request refuses.
5. Start the NOWNodes loopback proxy on `127.0.0.1:8545`. The EVM write reaches mainnet through it (02-nownodes
   Task 4).

## Simulate (no broadcast: report writes are dry runs and spend no gas)

Run from `chain/cre`:

```bash
mkdir -p evidence
cre workflow simulate exo --target mainnet --non-interactive --trigger-index 0 --http-payload @exo/payloads/send-usdc.json | tee evidence/guard-send-usdc.txt
cre workflow simulate exo --target mainnet --non-interactive --trigger-index 0 --http-payload @exo/payloads/approval-for-all.json | tee evidence/guard-approval-for-all.txt
cre workflow simulate exo --target mainnet --non-interactive --trigger-index 1 --http-payload '{"reason":"simulation test"}' | tee evidence/freeze.txt
```

Expected results:

- The first run approves: `"verdict":"approve"` and "You pay 1 USDC to mira.eth. Nothing else changes."
- The second refuses, citing the camera and the approval-for-all.
- The third returns `{"ok":true,...}`.

Without `--broadcast`, `report_tx` is the zero hash. After a successful run, set the `guard` and `freeze`
entries in `workflow-manifest.json` to `"simulated"`.

`--broadcast` sends real mainnet transactions from the simulator key. Every one needs Konrad's go-ahead at that
moment.

## Before deploying (not done)

- The HTTP triggers use `{}`, which is valid only in simulation. A deployed workflow needs `authorizedKeys` with
  the bridge's signing address (TODO(konrad) in `main.ts`).
- Order the ExoModule migration as described in `chain/README.md`: forwarder first, then identity, then
  `demoReporter`.
