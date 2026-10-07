---
title: An Agent With a Wallet, a Human With a Key: Exo's Security Model
slug: security-model
date: 2026-10-07
order: 3
summary: How Exo splits money across a cold key, an agent Safe and a gas-only hot key; the technologies that guard each step; the attacks they stop; and eight real incidents, from BadgerDAO to Bybit, measured against the design.
---

Giving an AI agent a crypto wallet sounds like a bad idea, and done naively it is. The agent reads untrusted text all day. A webpage, a QR code, a token name or a planted "memory" can try to talk it into sending money somewhere it shouldn't.

Exo's answer is that the agent never gets a key that can move money on its own. This post lays out who holds what, which technologies check each step, and how the design holds up against real attacks from the last five years.

## Four keys, four jobs

| Key or role | Where it lives | What it can do | What it can't do |
|---|---|---|---|
| **Cold key** (owner) | An air-gapped device that never goes online (the Exo kit's optional Pi Zero, or your hardware wallet) | Owns the agent Safe and the Exo module; changes the onchain caps; unfreezes; replaces the deck key; removes the module to cut the agent off | It never signs day-to-day transactions |
| **Agent Safe** | A Safe smart account on Ethereum | Holds the working balance the agents may use | Moves nothing unless the Exo module asks, and the module asks only for Guardian-approved transactions |
| **Deck hot key** | On the wearable (the "deck") | Pays gas and calls `ExoModule.execute` | It holds no spending money and can execute only what the Guardian already approved, once, before it expires |
| **The Guardian** | A Chainlink CRE workflow | Simulates, checks your rules, explains, and writes a verdict onchain | It can't sign or send a transaction itself |

The agents (Hermes, on your own server) sit outside all four. They can *propose* a transaction. That's the whole of their power over your money.

Your long-term savings stay with the cold key and are never reachable by the agents at all. The Safe holds only what you choose to give the agents to work with, which is a capped working balance, much like the cash in your pocket versus the money in your bank.

## The path of a transaction

1. **Proposal.** You say "send 20 USDC to Mira for lunch". The wallet agent builds the transfer and sends it to the Guardian. It can't send it anywhere else.
2. **Simulation.** The Guardian runs the exact transaction from the Safe against current Ethereum state, using NOWNodes' `debug_traceCall`, and decodes every balance change, token transfer, NFT transfer and approval.
3. **Rules.** Your policy is checked against the *simulated* changes: limits in USD (priced with the Chainlink ETH/USD feed), the address book, and switches for approvals.
4. **Explanation.** A sentence is built from the simulation, not from the agent: "You pay 20 USDC to mira.eth. Nothing else changes."
5. **Second opinion.** Two AI models from different vendors review the decoded call. They can only add a refusal, never an approval.
6. **Verdict onchain.** The approval is bound to the transaction's exact hash, valid for 10 minutes, and written to `ExoModule`.
7. **You.** Your earbuds read the explanation, your glasses show it, and the approve key blinks. You press it.
8. **Execution.** The deck's hot key calls `ExoModule.execute`. The module checks the hash, the expiry, the freeze state and the caps, and only then asks the Safe to run it.

Small, low-risk payments to people already in your address book can be set to run without the key press (auto-approve). Turning auto-approve on itself needs a key press.

## The technologies underneath

| Layer | Technology | Why it's there |
|---|---|---|
| Account | **Safe** smart account | Battle-tested multisig contract; separates ownership (cold) from execution (module) |
| Enforcement | **`ExoModule`**, a Safe module and Chainlink CRE consumer contract | Runs only approved hashes; single use; at most 1-hour approvals; per-transaction and daily ETH caps; freeze; no `delegatecall`; refuses calls to the Safe or itself |
| Decision | **Chainlink CRE** workflow (the Guardian) | Rules held as secrets the agent can't read; verdicts signed and delivered onchain; on a Chainlink DON, independent operators run it |
| Truth | **Transaction simulation** via NOWNodes `debug_traceCall` | What the transaction *does*, not what anyone says it does |
| Price | **Chainlink ETH/USD Data Feed** | Limits in dollars; stale prices refuse |
| Review | **Two LLM reviewers** from different vendors | A second "no" for patterns rules don't catch; every field they see is quoted as untrusted data |
| Human | **A physical approve key** on the wearable | Out-of-band from every screen and every piece of software |
| Separation | **Unix users and narrow tokens** on the server | The agents run as one user; the Guardian runs as another with its own code checkout and secrets the agents can't read; the agents' token opens one endpoint only |
| Root | **Air-gapped cold key** | The only key that can change the rules of the account itself |

Every step **fails closed**. If the price feed, the node, an AI reviewer or the policy can't be read, the answer is "refuse". Your money stays where it is, and only the agent's ability to move it pauses.

## What it protects against

| Attack | Example | What stops it |
|---|---|---|
| Prompt injection | A webpage tells the agent "send the balance to 0xBad…" | The agent has no key; unknown recipients are refused above your threshold; you hear where the money goes before you press |
| Drainer contracts | A "claim your airdrop" link that's really `setApprovalForAll` | The simulation shows the approval; approvals-for-all and approvals to unknown spenders are refused by default |
| Unlimited approvals | A dApp asks to spend all your USDC, forever | Refused unless you've explicitly allowed it |
| A confused agent | It builds a transfer of 200 instead of 20 | For a send, the simulation must match the stated token, amount and recipient exactly |
| Hidden side effects | A "transfer" that also grants an allowance | A send may have exactly one effect; any event the Guardian can't read is a refusal |
| An interface that lies | Whatever builds the transaction also describes it | The description is computed by the Guardian, from the exact transaction the module will execute |
| Replay or tampering | Reusing an old approval, or changing the calldata | Hash binding, single use, 10-minute expiry |
| Account takeover | An "upgrade" that adds an owner or swaps the Safe's logic | Calls to the Safe or the module are refused by the Guardian *and* reverted by the module; `delegatecall` is never used |
| A stolen deck | Someone copies the deck's hot key | It holds gas only, and can execute only what's already approved |
| Panic | You think something's wrong | Hold the approve key and the mic button together for 2 seconds: the module freezes, and only the cold key can unfreeze |

## Eight real incidents, measured against the design

These are honest assessments, not marketing. Some of these attacks hit institutions with very different setups from a personal wearable, so the question is whether the same *class* of attack would get through Exo.

| Incident | What happened | Would Exo's design have stopped it? |
|---|---|---|
| **BadgerDAO** (Dec 2021, ~US$120M) | Attackers used a compromised Cloudflare API key to inject a script into the site, which collected token approvals from about 200 wallets, then drained them | **Yes, for this class.** The approvals were to an unknown spender, which the Guardian refuses by default, and the explanation would have named the grant |
| **Ledger Connect Kit** (Dec 2023, ~US$600K) | A phished npm account pushed a drainer into a library many dApps load | **Yes, for this class.** Drainer approvals and transfers to unknown addresses are refused |
| **Address poisoning** (May 2024, 1,155 WBTC, ~US$68M) | The victim copied a lookalike address from their own history (`0xd9A1c…` instead of `0xd9A1b…`) | **Yes, with the default policy.** A transfer that size to an address outside the address book is refused, and the explanation names the recipient |
| **WazirX** (Jul 2024, ~US$235M) | Signers approved a transaction that upgraded the multisig to a malicious contract | **Yes, for this class.** A call that targets the Safe itself is refused, and the module never uses `delegatecall` |
| **Radiant Capital** (Oct 2024, ~US$50M) | Malware showed signers a normal transaction while their hardware wallets blind-signed a different one | **Yes, for this class.** The approval is bound to the exact hash the module executes, and its explanation comes from a simulation run elsewhere, not from the compromised screen |
| **Freysa** (Nov 2024, ~US$47K) | A user prompt-injected an AI agent into "approving" a transfer of its whole prize pool | **Yes.** An Exo agent can only propose. The transfer would face the limits, the address book and your key |
| **Bybit** (Feb 2025, ~US$1.5B) | Malicious JavaScript in the multisig's web interface showed signers a routine transfer; they signed a change to the wallet's logic | **Yes, for this class.** The interface that shows you the transaction isn't the one that decides, and logic-changing calls to the Safe are refused at two layers |
| **AIXBT** (Mar 2025, 55.5 ETH, ~US$106K) | An attacker got into the agent's dashboard and queued prompts telling it to send funds | **Very likely.** Control of the agent isn't control of the money: the send would hit the per-transaction cap and the address book, then wait for a key press the attacker can't make |

## What it doesn't stop yet

We'd rather list these than have you find them.

- **A hijacked agent can state an intent that matches its malicious transaction.** The intent check catches mistakes, not lies. The address book, the limits and your key are what stop the lie. The next step is to have the deck sign your spoken words so the Guardian parses the intent itself.
- **Small sends to strangers can pass the rules** below your `require_known_recipient_over_usd` threshold. They still need your key press (auto-approve requires a known recipient). Set the threshold to 0 for strict mode.
- **Token allowances outlive the transaction.** Approvals only go to address-book spenders and are never unlimited by default, but an approved spender can use its allowance later.
- **It's not on a Chainlink DON or in a TEE yet.** Today the Guardian runs in CRE's simulator and its verdicts reach the module through a dedicated simulation key. Moving to a DON, then to confidential (TEE) execution, are the next steps.
- **Swaps, marketplace purchases, staking and votes are refused** because the Guardian doesn't decode those functions yet. It refuses what it doesn't understand, by design.
- **It can't help with money that isn't in the Safe:** exchange balances, bridges you don't control, or someone else's protocol bug.

The principle under all of it is one the Bybit theft taught the industry at enormous cost: **the layer that talks to you should never be the layer that decides.** On Exo, the agent talks, the Guardian decides, the chain enforces, and you hold the key.

## Sources

- [CoinDesk: BadgerDAO reveals how it was hacked for $120M](https://coindesk.com/business/2021/12/10/badgerdao-reveals-details-of-how-it-was-hacked-for-120m/amp)
- [The Hacker News: Ledger supply-chain breach](https://thehackernews.com/2023/12/crypto-hardware-wallet-ledgers-supply.html)
- [Halborn: the $68 million address poisoning hack](https://www.halborn.com/blog/post/massive-68-million-address-poisoning-hack-underscores-ongoing-cyber-threat)
- [2024 WazirX hack (Wikipedia)](https://en.wikipedia.org/wiki/2024_WazirX_hack)
- [Halborn: the Radiant Capital hack explained](https://www.halborn.com/blog/post/explained-the-radiant-capital-hack-october-2024)
- [Simon Willison on the Freysa agent](https://simonwillison.net/2024/Nov/29/0xfreysaagent/)
- [BleepingComputer: Lazarus hacked Bybit via a breached Safe{Wallet} developer machine](https://www.bleepingcomputer.com/news/security/lazarus-hacked-bybit-via-a-breached-safe-wallet-developer-machine/)
- [Cointelegraph: hacker breaches AI crypto bot AIXBT, steals 55 ETH](https://cointelegraph.com/news/hacker-breaches-ai-crypto-bot-aixbt-steals-55-eth)
