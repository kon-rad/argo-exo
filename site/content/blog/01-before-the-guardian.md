---
title: Before the Guardian: Who Checks a Payment Before It Leaves
slug: before-the-guardian
date: 2026-10-07
order: 1
summary: Banks, brokers and wallet companies have all built a checkpoint between "I want to pay" and "the money moved". Here is that lineage, and what Exo's Chainlink CRE Guardian changes about it.
---

Every serious money system puts something between the person who asks for a payment and the payment itself. A card network scores your purchase before the till beeps. A corporate bank holds a large wire until a second person signs off. A broker rejects an order that would blow through a client's credit line before it reaches the exchange.

Exo puts an AI agent in charge of a crypto wallet. That makes the checkpoint more important, not less. The agent reads webpages, photos, messages and its own memory, and anything in them can try to steer it. So every transaction an Exo agent proposes goes through the **Transaction Guardian**, a Chainlink CRE workflow, before it can run.

The Guardian isn't a new idea, and we'd rather say so up front. This post traces where it comes from, what already exists, and the specific things it does differently.

## The checkpoints traditional finance already runs

| Control | Who runs it | What it checks | Exo's equivalent |
|---|---|---|---|
| **Card authorisation scoring** | Card networks and issuers | Scores each purchase for fraud in the milliseconds before approval | The Guardian's two AI reviewers, which can only add a refusal |
| **Maker-checker / dual control** | Corporate and treasury banking | One person prepares a payment, a different person approves it | The agent proposes; the wearer presses the approve key |
| **New-payee holds and payee lists** | Retail and business banks | Extra checks, or a delay, the first time you pay someone | The address book: above a set amount, recipients must already be in it |
| **Confirmation of Payee** | UK banks via Pay.UK (launched 2020, mandatory for regulated payment firms since 31 October 2024) | Checks the name you typed against the account you're paying | The plain-English explanation, built from a simulation: "You pay 20 USDC to mira.eth" |
| **Pre-trade risk controls** | US broker-dealers under SEC Rule 15c3-5 (adopted 2010) | Orders must be within credit and capital limits and not erroneous before they reach the market | Per-transaction and daily limits, checked before signing |
| **Spending limits on cards** | Issuers, expense platforms | Caps per transaction, per day, per merchant category | `max_usd_per_tx`, `max_usd_per_day`, and onchain ETH caps in the Safe module |
| **Reimbursement after fraud** | UK payment firms (APP fraud rules, from 7 October 2024, up to £85,000 per claim) | Pays victims back after the fact | None. A mined transaction can't be reversed, which is why every check happens *before* signing |

The pattern is the same in each row. The party who asks for the payment never decides it alone, the check looks at what the payment actually does, and the decision is enforced by whoever holds the money.

The last row is where crypto differs. A bank can claw back or reimburse. A blockchain can't. Once a transaction is mined, the only protection left is the one that ran before you signed.

## What crypto has built so far

Crypto spent a decade relearning these controls, mostly after expensive mistakes.

| Generation | Examples | What it does |
|---|---|---|
| **Pre-sign simulation in the wallet** | Rabby's pre-sign simulator; MetaMask's security alerts (MetaMask absorbed Wallet Guard in 2024) | Shows balance changes and approvals before you sign |
| **Security APIs** | Blockaid, Hypernative Transaction Guard, Tenderly | Simulation plus threat intelligence, sold to wallets, exchanges and funds. Hypernative's policy engine auto-approves, rejects or routes to review |
| **Smart-account guards** | Safe Guards; Safe Research's Fiducia (2025), with an optional cosigner | Onchain allowlists, per-token limits, a second signature that only appears when checks pass |
| **Agent wallets with policy engines** | Coinbase Agentic Wallets (2026), Privy, Turnkey, Lit Protocol's Vincent | Give an agent a wallet with spending caps and allowlists; keys held in enclaves |
| **Agent wallets with a human key** | MetaMask Agent Wallet (public August 2026, with a "Guard Mode"); Ledger Agent Stack (open source, July 2026) | Simulation and scanning before execution; a human approves anything flagged, on a phone or a Ledger |
| **Scoped trading keys** | Hyperliquid agent (API) wallets | An agent key can place and cancel orders but can't withdraw |
| **CRE guards at hackathons** | SentinelCRE (1st place, CRE & AI, Chainlink's Convergence hackathon, 2026), CRE Risk Router, Aegis Protocol V5, WalletGuard | AI review and rules inside a Chainlink workflow before an agent's action runs |

If you're a judge, an investor or a buyer, these are the comparisons you'll reach for, so we're naming them ourselves. MetaMask has distribution and a loss-coverage program. Ledger has a trusted secure element. Blockaid and Hypernative have years of threat data. Exo doesn't beat any of them at their own speciality.

## What the Guardian does differently

What Exo does is combine pieces that usually sit with different companies, and move the part you'd normally have to trust onto infrastructure no single company runs.

**1. The rules don't live on our server.** In most agent wallets, the policy runs inside the vendor's own enclave or API. You're trusting that company the way you trust a bank. Exo's rules are a secret inside a Chainlink CRE workflow. Once it's deployed to a Chainlink DON, independent node operators run the check and sign the verdict, and the contract accepts only verdicts from that exact workflow.

**2. The verdict is enforced, not advised.** A warning in a wallet popup can be clicked past. The Guardian writes its verdict onchain to `ExoModule`, a Safe module, and the module refuses to run anything whose exact hash wasn't approved. The approval is single-use, expires within minutes, and is revoked by a later refusal.

**3. The explanation comes from the simulation, never from the agent.** This is Confirmation of Payee for smart contracts. The Guardian runs the exact transaction against live Ethereum state and builds the sentence you hear from what changed. If the agent said "send 20 USDC to Mira" and the simulation shows anything else, the transaction is refused.

**4. You hold the root key, offline.** The Safe and the module are owned by a cold key that never joins the daily flow (in the Exo kit, an air-gapped Pi Zero). It alone can raise the onchain limits, unfreeze the wallet or cut the agent off entirely.

**5. The approval is a physical key on your body.** Maker-checker usually means a second person at a second desk. On Exo the checker is you, and the "desk" is a mechanical key on the device you're wearing. Software, including the agent, can't press it.

**6. It's open source.** The workflow, the contract and the threat model are public, including a list of what the Guardian doesn't stop yet.

## Where it stands today

We built the Guardian during the TOKEN2049 Origins hackathon (6–7 October 2026). It runs in Chainlink's CRE simulator against live Ethereum mainnet data: in our first live run it approved a 1 USDC payment to an address-book contact ("You pay 1 USDC to mira.eth. Nothing else changes.") and refused a `setApprovalForAll` request built from a camera photo. It isn't deployed to a Chainlink DON yet, and it doesn't yet run inside CRE's Trusted Execution Environment. Both are the next steps, and until then its verdicts reach the contract through a dedicated simulation key.

Traditional finance protects you by being able to undo things. Crypto can't undo, so Exo protects you by checking first, in a place neither the agent nor we can quietly change.

## Sources

- [Pay.UK expands Confirmation of Payee](https://newseventsinsights.wearepay.uk/latest-updates/payuk-expands-confirmation-of-payee-to-reduce-fraud-in-the-uk/)
- [FINRA: Market Access Rule (SEC Rule 15c3-5)](https://finra.org/rules-guidance/guidance/reports/2024-finra-annual-regulatory-oversight-report/market-access-rule)
- [Hypernative Transaction Guard](https://www.hypernative.io/product/transaction-guard)
- [Blockaid: building safer onchain AI agents](https://blockaid.io/blog/how-to-build-smarter-safer-onchain-ai-agents-with-blockaid)
- [Safe Research: Fiducia, onchain trust rules and cosigning](https://safefoundation.org/blog/safe-research-fiducia-onchain-trust-rules-and-cosigning)
- [Decrypt: MetaMask launches AI agent wallet](https://decrypt.co/370239/metamask-launches-ai-agent-wallet-security-controls)
- [The Block: Ledger unveils Agent Stack](https://www.theblock.co/post/408549/ledger-unveils-hardware-backed-agent-stack)
- [Consensys acquires Wallet Guard](https://consensys.io/blog/consensys-acquires-wallet-guard-to-enhance-metamask-security)
- [Lit Protocol: Meet Vincent](https://spark.litprotocol.com/meet-vincent-an-agent-wallet-and-app-store-framework-for-user-owned-automation/)
- [Chainlink: Convergence hackathon winners](https://chain.link/blog/convergence-hackathon-winners)
