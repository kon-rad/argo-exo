# Argo Exo software build log
### Deck, NOWNodes, Guardian, voice and agents, kiosk, pre-sale site, vault template: built 2026-10-06 to 2026-10-07 with subagent-driven development

**Built:** 2026-10-06 to 2026-10-07 (81 commits, `d4ee472..0439e7b`)
**Contracts:** see [architecture.md](architecture.md).
**Method:** one implementer per task, an independent reviewer per task, fix rounds until review came back clean.

---

## TL;DR: the five things that matter

1. **All code in sections 01-07 is written, reviewed and unit-tested. Almost nothing has run live.** No droplet install, no Pi install of the new kiosk, no `cre` simulation, no mainnet deploy, no pre-sale contract on Base. The one end-to-end rehearsal is the pre-sale purchase flow on an anvil fork of Base. **Most likely to change the plan:** the CRE workflow has never been through `cre workflow simulate`, so the Guardian's marker parsing and the SDK wiring are unproven until the maintainer installs the CLI.
2. **Review caught real fund-loss bugs that tests did not.** The worst: a zero-value self-call through `ExoModule` could take over the Safe; `demoReporter == 0` plus a permissionless mock forwarder let anyone approve a transaction; a plan typo hid every `setApprovalForAll` on chain. All fixed with tests; see section 2.
3. **Money paths fail closed by default.** Broadcast is off (`EXO_GUARDIAN_BROADCAST=1` to enable), unknown selectors refuse, approvals to unknown spenders refuse, approve fires on key release, the bridge token never reaches Hermes.
4. **Twelve pending items from the maintainer gate going live**, listed in section 5. The pre-sale page is deliberately unshippable until he fills the `TODO(konrad)` copy slots.
5. **Gates are green today (2026-10-07):** 749 pytest, 46 node, 42 + 27 forge, 111 + 6 bun, `check-public` clean, gitleaks clean over 81 commits.

---

## 1. What was built, per section

| Section | Built | Verified how | Not run live |
|---|---|---|---|
| 01 foundation | Repo, `check-public.sh` leak gate, CI, deck approve tests, `DECK_HOST_S` for firmware | pytest, CI green | GNU-tool run of the gate (docker was down; CI covers it) |
| 02 nownodes | `exo_nownodes` client with usage counter, WebSocket confirmations, local loopback proxy, TS builders (`packages/nownodes-ts`), `nownodes-chain` Hermes skill | 207+ py tests, 6 bun tests; real `cast block-number` through the proxy | Every live call: no `NOWNODES_API_KEY` yet, hosts are the plan's assumed ones |
| 03 guardian | `ExoModule` Safe module, shared approval-hash vector (py/sol/ts), CRE `exo` workflow (decode, rules, two-LLM judge), ledger, `guardian-run`, bridge approval endpoints, deck queue sync, hot-key signer, panic freeze | 42 forge, 111 bun, 749 pytest incl. 142 PgStore tests on a throwaway Postgres | `cre` simulate, WASM compile, Safe, keys, mainnet (Task 9) |
| 04 voice and agents | Deepgram/Gemini STT, router, Aura/espeak TTS, talk hooks, `exo-bridge` (Flask, tailnet-only), six Hermes profiles, `exo-wallet` skill, install scripts | Live check 2026-10-06: Deepgram and Gemini transcribed a test wav, Aura returned 142 KB of audio and `aplay` was invoked. Bridge CLI flags checked read-only on the droplet | Spoken round trip, droplet install (needs Tailscale), Pi `.env` keys, button tests |
| 05 kiosk | AR-style kiosk: home, approvals, voice/talk log, agents kanban, wallets, transactions/CRE, system view, media browser; button logic with chords | 175 then 622 py tests; headless Chrome render at 1920x1080 | On the Pi with real glasses; `deck-kiosk` toggle script not in vault |
| 06 presale site | `ExoPreorder` (USDC-paid numbered receipt NFT, onchain plate art), Flask API (sale state, shipping claims), permit and approve checkout, build with release gate, Caddy + systemd deploy files | 27 forge, 46 node, 40 api tests | Real deploy, DNS, real wallet, `remote-install.sh` on a host |
| 07 secondbrain | PARA vault template and generic agent rules under `secondbrain/`, this dev log | Review plus privacy grep | README prose and LICENSE are the maintainer's call |

### Base-fork rehearsal (section 06, the only end-to-end run)

Anvil fork of Base at block 52277557, real USDC, no real money. Passed: permit route and approve route, `PriceAboveMax`, paused state, `/api/sale` live, two claims saved, a stranger got 403, admin summary showed 2 minted and 3.00 USDC, a stale claim after a transfer was flagged. Caddy block validated with real caddy 2.11.4; `.mjs` served as `text/javascript`.

---

## 2. What broke and why: the findings that mattered

| Finding | Where | Cause | Fix |
|---|---|---|---|
| **Self-call takeover (Critical)** | `ExoModule.execute` | A zero-value call to the Safe or the module itself could add an owner or disable the module | `execute` reverts `SelfCall` when `to` is the safe or module. Cost: Guardian can never manage Safe settings; owner does it directly |
| **Fail-open `demoReporter`** | `ExoModule._processReport` | `demoReporter == 0` plus a permissionless mock forwarder let anyone submit an approval | Fails closed unless `demoReporter != 0` or a workflow id/author is configured; deploy script rejects a zero simulator |
| No TTL bound, refuse didn't revoke | `ExoModule` | Plan gap | `MAX_APPROVAL_TTL` = 1 h; a refuse report revokes an unexecuted approval of the same hash |
| DON migration order | docs | Clearing `demoReporter` before setting the real forwarder left it forgeable | Checklist: forwarder first, then identity, then clear `demoReporter`; hazard test added |
| **`ApprovalForAll` topic typo** | plan 03 Task 3 | Plan literal `...9653c200f2...` vs real `...9653f200f2...`, would have hidden every `setApprovalForAll` | Topics computed with viem; short fixture calldata also fixed |
| `explain` fails open | decode | Optional trace defaulted to "fine" | `TraceResult` required; simulateV1 path takes top-level value; mismatch means not understood |
| Malformed judge output auto-approves | rules | `in RANK` hit the prototype chain | Own-property check; judge sees framed fields, not the raw explanation (symbol injection) |
| Unlimited-approval evasion | rules | 2^96-2 slipped under a magnitude threshold | Any non-zero approval to a spender not in the address book is a violation |
| **Mempool said "Confirmed"** | NOWNodes WS | Notification for a mempool tx was announced as confirmed, then txid dedup swallowed the real confirmation | Silent on mempool; only mined txs are announced |
| **Proxy exposed to the browser** | NOWNodes proxy | No Host/Origin check: any local web page could spend quota or broadcast | Host/Origin checks, `application/json` required, query string stripped, unit hardening |
| **Daily cap race** | `guardian-run` | Concurrent guards read `spent_today` together | `pg_advisory_xact_lock` over the whole guard (threading lock in memory store) |
| **Hermes held the writer DSN** | skill | Skill could forge `waiting_key` rows | Hermes never gets a DSN; CLI calls bridge `/guard` over HTTP |
| **Guard token** | bridge | Any agent could read the full bridge token (opens `/freeze`, `/executed`, `/talk`) | Second narrow `EXO_GUARD_TOKEN` valid only for `POST /guard` |
| Agents claiming "voice" | skill | An LLM cannot prove the wearer spoke | Skill sends `agent:<profile>`; `--source` accepts only `camera` or `dashboard` |
| Marker not fsynced | signer | Power loss before broadcast could re-sign | fsync temp file and directory before broadcast |
| **Wallet freeze** | signer | Freeze could lose a same-nonce race to an in-flight execute | Freeze always bids at least 2x tip and the normal maxFee |
| Approve while starting panic hold | buttons | Approve+Mic chord would send a tx | Approve fires on key release and is cancelled if Mic was touched |
| Kiosk freeze on slow NOWNodes | wallets panel | Sequential tick plus blocking refresh | Cache served at once; background refresh with non-blocking lock; 3 s connect, 5 s read |
| **Body limit** | pre-sale API | waitress buffered up to 1 GB before Flask's 4 KB check, on a shared droplet | 8 KB cap in Caddy and waitress |
| **CI `\|\| true`** | `ci.yml` | JS step hid every failure, and passed a directory arg Node 22 rejects | Removed; globs fixed |
| **Gitleaks false positives** | CI | Public token addresses and the Anvil #0 test key flagged; `gitleaks-action` failed on first-push range | Allowlist bare 0x addresses and the exact test key; pin gitleaks CLI 8.30.1 |
| Gate was fail-open | `check-public.sh` | Plan-mandated allowlist filter | Strip allowed tokens before anchored IPv4 match; widened the private denylist (hostnames stay out of git) |
| Talk queue consequence | deck-buttons | Talk-stop holds the hook queue for STT + bridge + TTS | Surfaced, not fixed; see open questions |

Plan defects carried forward: module owner is the cold-key EOA, so Task 9 must unfreeze from the cold key directly, not via Safe Transaction Builder. The `exo-master-plan.md` line 193 still says "buttons 1 + 4".

---

## 3. Architecture touched

Four runtimes, contracts in [architecture.md](architecture.md): **deck** (Pi: buttons, talk hooks, kiosk, signer, queue sync), **droplet** (Hermes, `exo-bridge`, ledger Postgres, `guardian-run`, pre-sale API), **chain** (`ExoModule`, CRE workflow, `ExoPreorder`), **site** (static build plus checkout JS). 

---

## 4. How it was tested

| Gate (2026-10-07) | Command | Result |
|---|---|---|
| Python | `.venv/bin/python -m pytest -q` | **749 passed, 66 skipped** |
| Site JS | `node --test site/js-tests/*.test.mjs` | **46 pass, 0 fail** |
| Chain contracts | `forge test` in `chain/contracts` | **42 passed, 0 failed** (2 suites) |
| Site contracts | `forge test` in `site/contracts` | **27 passed, 0 failed, 1 skipped** (3 suites) |
| CRE workflow | `bun test` in `chain/cre/exo` | **111 pass** (9 files, 520 expects) |
| NOWNodes TS | `bun test` in `packages/nownodes-ts` | **6 pass** |
| Public gate | `scripts/check-public.sh .` | exit 0, no hits |
| Secrets | `gitleaks git . --no-banner --redact` | no leaks, 81 commits scanned |

The 66 skipped pytest cases are not itemised here (probably env-gated: Postgres, fork); not verified. The checkout also holds another session's uncommitted deck, firmware and `docs/guardian.md` edits; no gate failed in them, and they were not touched.

Live checks that did run: STT/TTS (2026-10-06, Deepgram and Gemini STT, Aura TTS), Base preflight reads over the public RPC (chain id 8453, USDC name "USD Coin", version "2", 6 decimals), read-only flag checks of the Hermes CLI on the server, `cast block-number` through the proxy, headless Chrome renders of the kiosk.

**Not measured:** plan 07 Task 12 asks for talk latency and task-to-running latency from 04 Task 8 Step 12. Those steps need the Pi online and the bridge on a tailnet-reachable droplet, so no figures exist. Pre-dates this build: the Pi dashboard and `deck-approve` queue.

---

## 5. Pending maintainer actions (nothing below is done)

| # | Needs | Blocks |
|---|---|---|
| 1 | `NOWNODES_API_KEY` in the local `.env` | Every live NOWNodes check; host list in `hosts.py`/`hosts.ts` unverified |
| 2 | Install Tailscale on the droplet, then `agents/install-profiles.sh` and `install-bridge.sh` per `agents/docs/board-conventions.md` | Bridge, Hermes profiles, any spoken round trip |
| 3 | Install `cre` CLI and `cre login`; diff against `cre init`; WASM build; simulate; Confidential (TEE) access check | CRE workflow is unproven; marker regex unverified |
| 4 | Real keys and the Safe (fresh keys; anvil accounts carry EIP-7702 delegations on Base) | Task 9 |
| 5 | Pi online; Pi `.env` keys incl. `DECK_UPLOAD_TOKEN`, `ULTRAHUMAN_API_TOKEN`; run `deck/install.sh`; hook and button tests; add `DECK_HOST_S` to the real `secrets.h` before the next firmware flash | Kiosk, buttons, talk on hardware |
| 6 | Pre-sale copy: every `TODO(konrad)` slot (33 message slots, `terms.countries` incl. shipping countries; until filled every claim is 400). Also review the prescribed headings in `copy.json` | Release build refuses while any remain |
| 7 | Tier names, prices, `maxSupply`, treasury, owner (fresh keys), transferable yes/no; check the plate render once names are set (24-byte cap) | Contract deploy |
| 8 | DNS record for the pre-sale host, then `remote-install.sh` (confirm `python3-venv` on droplet) | Site live |
| 9 | Task 9 mainnet checklist (below) | Real money |
| 10 | Talk-queue decision (section 6) | UX of talk during a reply |
| 11 | README prose and LICENSE: the maintainer's call (structure ours, words theirs) | Going public with final wording |
| 12 | Other session's uncommitted deck/firmware/hardware/docs work in the same checkout: commit or merge it before the next deck deploy | Deck deploy |

**Task 9 mainnet checklist (accumulated):** forwarder to the real KeystoneForwarder before identity, identity before clearing `demoReporter`, never `setForwarderAddress(0)`; dedicated simulator key; owner is the cold-key EOA and unfreeze comes from it; `authorizedKeys` on both HTTP triggers before any deploy; plain-handler mode refuses on a multi-node DON, so TEE mode is needed; verify the ETH/USD feed address and decimals; verify `parse_result`'s marker regex against the first real simulate output; flip the manifest to "simulated" after that run; confirm judge models; set `EXO_GUARDIAN_BROADCAST=1` only at go-live.

---

## 6. Open questions and deferred minors

- **Talk queue (needs a decision).** Talk-stop holds the hook queue for the whole STT + bridge + TTS turn, so a Talk press during a reply is delayed or truncated. Left as is on purpose; options are a second worker for talk-start or interrupt-on-press.
- `deck-kiosk` toggle script is not in the vault (working copy on the Pi since 2026-10-04); the installer warns if it is missing, a fresh Pi lacks the toggle.
- `allow_unlimited_approvals` now gates any approval to an unknown spender; rename before the maintainer writes his policy.
- Bridge should force `agent:*` on guard-token requests (today only the skill enforces it); `install-profiles.sh` does not set `EXO_AGENT_PROFILE`, so every request logs as `agent:default`. Fix before deploy.
- CLI timeout (270 s) is shorter than the bridge worst case (300 s lock + 240 s sim): a retry can queue a duplicate approval.
- Daily cap is a UTC calendar window (up to 2x across midnight); unpriced outflows to a known recipient skip caps; approvals survive freeze/unfreeze (bounded by the 1 h TTL).
- `setTierName` renames the Tier trait on already-sold receipts; `markRefunded` burns but does not refund USDC; smart-contract wallets can buy but cannot sign a shipping claim (ecrecover, no EIP-1271).
- Unknown function selectors refuse, so swaps, NFT buys and votes are blocked until `decode.ts` learns more selectors.

---

## What's confirmed vs. inferred

| Confirmed (run or read against the real thing) | Inferred or not run |
|---|---|
| Test and gate counts in section 4, run 2026-10-07 | The 66 skips being env-gated |
| Base fork rehearsal of the purchase flow | Anything on mainnet or a real DON |
| STT and TTS round trip on real APIs, 2026-10-06 | Spoken round trip and both latencies |
| Hermes CLI flags used by the bridge and profiles (read-only on droplet) | NOWNodes hosts and `debug_traceCall` support |
| Contract behaviour under reviewer probes (reentrant buyer, failed permit, tier 0/255, tokenURI gas) | CRE simulator behaviour and the Guardian marker format |
| Caddy block validated by real caddy | `remote-install.sh` on a real host; GNU-tool run of the leak gate (CI covers it) |
