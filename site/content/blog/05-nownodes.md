---
title: How Exo Talks to Four Chains Through NOWNodes, and Why the Approve Key Is a Keyswitch
slug: nownodes-and-the-approve-key
date: 2026-10-07
order: 5
summary: Which chains Exo uses, every place it calls NOWNodes (from the Guardian's simulations to the wallet panel), how the client and proxy are built, and why a glowing NOWNodes keyswitch on the wearable is a security control, not decoration.
---

Everything Exo does onchain goes through one provider: **NOWNodes**. The Guardian's simulations, the wallet balances in your glasses, the agents' chain skill, the broadcast of approved transactions and the pre-sale checkout all use the same client and the same key. This post explains what we call, why we chose NOWNodes, how the integration is built, and why the most important button on the device is a NOWNodes-branded mechanical keyswitch.

## The chains

| Chain | Chain ID | JSON-RPC host | Blockbook (indexer) host |
|---|---|---|---|
| Ethereum | 1 | `eth.nownodes.io` | `eth-blockbook.nownodes.io` |
| Base | 8453 | `base.nownodes.io` | `base-blockbook.nownodes.io` |
| Arbitrum One | 42161 | `arbitrum.nownodes.io` | `arb-blockbook.nownodes.io` |
| Polygon | 137 | `matic.nownodes.io` | `maticbook.nownodes.io` |

All eight endpoints were checked live on 7 October 2026: each RPC returned the right chain ID and each Blockbook reported itself in sync. The agent Safe and the Guardian run on **Ethereum mainnet**. The pre-sale runs on **Base**. Balances and history cover all four.

## Where Exo calls NOWNodes

| Use | Method | Who calls it |
|---|---|---|
| **Simulate every proposed transaction** | `debug_traceCall` with the call tracer (verified live on Ethereum and Base) | The Guardian (Chainlink CRE workflow) |
| **Broadcast approved transactions** | `eth_sendRawTransaction` | The deck's signer, after you press the approve key |
| **Wallet balances across four chains** | Blockbook REST | The Wallets panel in the glasses |
| **"What's my balance on Base?"** | Blockbook REST | The agents' `nownodes-chain` skill (read-only) |
| **Live confirmations** | `eth_getTransactionReceipt` polling (Blockbook WebSocket subscriptions as an option) | The deck, to say "Confirmed" in your earbuds |
| **Pre-sale state** | `eth_call` on Base | The pre-sale site's API (price, supply, sale status) |
| **Contract deployment and tests** | JSON-RPC through a local proxy | Foundry (`forge`, `cast`) and the CRE simulator |

## Why NOWNodes

**One key, one header, every chain.** Exo needs four EVM chains today and will need more. With NOWNodes the key travels in an `api-key` header and the host names follow one pattern, so adding a chain is a line of configuration rather than a new account and a new client.

**Node RPC and an indexer from the same provider.** A raw node can tell you a balance but not "what did this address do last week". Blockbook can. Having both behind the same key means the dashboard and the agents get balances, token holdings and transaction history without us running an indexer.

**Trace methods.** The Guardian's whole job depends on simulating a transaction and decoding everything it would change. That needs `debug_traceCall`, which many public RPCs don't offer. NOWNodes serves it on Ethereum and Base.

**No nodes on a wearable.** A Raspberry Pi in a sling can't run an Ethereum node, let alone four. A hosted provider is the only realistic option for a device this size.

**Room to grow.** NOWNodes covers more than 100 networks, including Bitcoin and other non-EVM chains, so the multichain portfolio can follow you beyond the EVM.

## How it's built

**`exo_nownodes`** (Python) is the one client every part of the deck and server uses. It sends the key only as a header, never in a URL. It retries at most twice on rate limits and server errors, then raises a clear error rather than looping. It turns a JSON-RPC error inside an HTTP 200 into an error rather than an empty result. And it counts requests against the plan's monthly allowance, warning at 80%.

**`nownodes-ts`** (TypeScript) holds the request builders and response parsers the Guardian imports. Inside a Chainlink CRE workflow, network calls must go through CRE's own HTTP client, so this package builds requests and parses answers but never fetches anything itself.

**A loopback proxy** on `127.0.0.1:8545` serves `/eth`, `/base`, `/arbitrum` and `/polygon`. CRE's RPC configuration and Foundry's `--rpc-url` have no field for a custom header, so the proxy adds it. It binds to loopback only, checks the Host and Origin headers so a web page in a local browser can't use it, requires JSON, and strips query strings. Our first live run found one bug here: Cloudflare in front of NOWNodes rejects Python's default `User-Agent`, so the proxy now sends its own.

**Confirmations** are what you hear after you press the key. NOWNodes' free Start plan doesn't include WebSockets (our first live test got a 403), so by default the deck polls the receipt of each transaction it sent in the last hour, every 3 seconds, and announces it once: "Confirmed" when it succeeded, "That payment failed" when it reverted. It never announces a transaction that's still in the mempool. On a plan with WebSockets, a setting switches the deck to Blockbook address subscriptions instead.

The key never appears in logs or error messages; the WebSocket URL, which has to contain it, is never logged at all.

## The approve key

On the front of the deck sits a single mechanical keyboard switch in a clear housing, with a black keycap and a green LED: a NOWNodes keyswitch keychain, opened up and wired into the Raspberry Pi. It's the **approve key**, and it's the only way a transaction leaves the agent Safe without your standing permission.

| State | What the key does |
|---|---|
| A transaction waits | The LED blinks. Your earbuds read the Guardian's explanation and the glasses pin it at the top of the Approvals panel |
| You press and release | The top transaction is signed by the deck's hot key and broadcast through NOWNodes. The LED flashes three times |
| Nothing waits | The key does nothing |
| You hold it with the Mic button for 2 seconds | The wallet freezes onchain; only the cold key can unfreeze |
| You ask for auto-approve | It turns on only if you press the key within 5 seconds. Even then, it covers only low-risk, small payments to people in your address book |

### Why a physical key is a security feature

**It's out-of-band.** Every famous signing attack, from Radiant Capital to Bybit, worked by lying on a screen. The key isn't a screen and isn't a button in an app. Nothing an agent writes, and nothing a malicious webpage shows, can press it.

**It approves exactly one thing.** The key always approves the item pinned at the top of the panel, which is the one whose explanation you just heard. An early version could approve an item that was scrolled off-screen; we fixed that in review.

**It acts on release, not on press.** If you start the panic chord (key plus Mic) the transaction is cancelled instead of sent.

**It can't approve what the Guardian refused.** The key only releases transactions the Guardian already approved onchain, bound to their exact hash, within the last 10 minutes. Even someone with full control of the deck can only send what's already approved, once.

**It turns approval into a habit.** You hear what a transaction does, then you physically press a key. It's slower than a click, and that pause is the point.

The keyswitch also does one more thing: it makes the moment visible. Anyone near you sees a green light blink and a key go down, the same way you'd see someone sign a cheque.

## Sources

- [NOWNodes](https://nownodes.io) and its [pricing plans](https://nownodes.io/pricing)
- [Blockbook, the indexer behind NOWNodes' Blockbook endpoints](https://github.com/trezor/blockbook)
- [Ethereum JSON-RPC: debug_traceCall in Geth](https://geth.ethereum.org/docs/interacting-with-geth/rpc/ns-debug)
