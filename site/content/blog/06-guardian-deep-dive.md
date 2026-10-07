---
title: The Chainlink CRE Guardian, In Depth
slug: guardian-deep-dive
date: 2026-10-07
order: 6
summary: How Exo's Transaction Guardian works end to end: the policy format, onboarding step by step, what runs under the hood, what happens on approval and on refusal, how to change your rules and your address book, and a long list of transactions it will and won't let through.
---

The Transaction Guardian is the check every Exo transaction passes before it can run. Our [security model post](/blog/security-model.html) explains why it exists. This one explains how it works, in enough detail to set it up, tune it and predict what it will do.

Short version: an agent proposes a transaction; a Chainlink CRE workflow simulates it, checks it against your rules, explains it, and writes an approval for that exact transaction to a Safe module onchain; you press the approve key; the module runs it.

## The pieces

| Piece | What it is | Where it runs |
|---|---|---|
| **Agent Safe** | A Safe smart account holding the agents' working balance | Ethereum mainnet |
| **`ExoModule`** | A Safe module and Chainlink CRE consumer contract. It stores approvals and executes only approved transactions | Ethereum mainnet |
| **The Guardian** | A Chainlink CRE workflow written in TypeScript, with a `guard` handler and a `freeze` handler | Chainlink CRE (today: CRE's simulator) |
| **Your policy** | A JSON document of rules, stored as the workflow's `POLICY_JSON` secret | CRE's secrets store, never in the agent's reach |
| **Cold key** | The owner of the Safe and the module | Offline (the kit's air-gapped Pi Zero, or your hardware wallet) |
| **Deck hot key** | Pays gas and calls `ExoModule.execute` | The wearable |
| **Approve key** | The physical keyswitch you press | The wearable |

## Your policy

Your rules are a JSON object with exactly these fields. Unknown fields are refused: a typo in a safety switch makes the policy unreadable, and an unreadable policy refuses everything.

| Field | Type | What it does |
|---|---|---|
| `address_book` | address → name | People and contracts you trust. The name is what you hear ("mira.eth") |
| `max_usd_per_tx` | USD | Most value that may leave the Safe in one transaction |
| `max_usd_per_day` | USD | Most value that may leave in a day (UTC), including this one |
| `auto_max_usd` | USD | At or below this, a low-risk send to a known recipient may run without a key press, if you've turned auto-approve on |
| `require_known_recipient_over_usd` | USD | Above this, every recipient must be in the address book. Set it to 0 for strict mode |
| `allow_unknown_spender_approvals` | true / false | Whether token approvals may go to spenders outside the address book, or be unlimited |
| `allow_approval_for_all` | true / false | Whether a contract may be given control of a whole NFT collection |
| `refuse_sources` | list | Where a proposal came from that is never trusted to move funds, for example `camera` |
| `stablecoins` | token addresses | Tokens counted at US$1 for the limits |

A sensible starting policy, with a placeholder address:

```json
{
  "address_book": { "0x1111111111111111111111111111111111111111": "mira.eth" },
  "max_usd_per_tx": 100,
  "max_usd_per_day": 300,
  "auto_max_usd": 25,
  "require_known_recipient_over_usd": 50,
  "allow_unknown_spender_approvals": false,
  "allow_approval_for_all": false,
  "refuse_sources": ["camera"],
  "stablecoins": ["0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"]
}
```

Some rules aren't in the policy because no setting should turn them off. Unknown contract functions are refused. A transaction that reverts in simulation, or has effects the Guardian can't read, is refused. Anything aimed at the Safe or the module itself is refused. A request not from your Safe, or for another chain, is refused. If the AI reviewers can't be reached, the answer is refuse.

## Onboarding, step by step

| # | Step | Who signs | Notes |
|---|---|---|---|
| 1 | **Create the cold key** on an air-gapped device | Nobody yet | The Exo kit's optional Pi Zero is flashed offline and never connects to a network; a hardware wallet you already own works too |
| 2 | **Create the agent Safe** on Ethereum, owned by the cold key | Cold key | This Safe holds only the agents' working balance, not your savings |
| 3 | **Deploy `ExoModule`** with the cold key as owner, the deck's hot-key address as executor, and onchain ETH caps per transaction and per day | Deployer | The caps are a backstop independent of the Guardian |
| 4 | **Enable the module on the Safe** | Cold key | From now on the module, and only for approved hashes, can ask the Safe to execute |
| 5 | **Write your policy** | You | Start strict: a small address book, low limits, approvals off |
| 6 | **Configure the Guardian**: the Safe and module addresses in its config; your policy, the NOWNodes key and the AI reviewers' key as secrets | You, from your own machine | The agents never see these values |
| 7 | **Fund it**: a working balance into the Safe; a little ETH to the deck's hot key for gas | You | The deck key never holds spending money |
| 8 | **Add your wallets to the dashboard**: the Safe, the deck key and your cold wallet as watch-only addresses | You | Public addresses only, never keys |
| 9 | **Test with a small send** to someone in your address book | You, with the approve key | Our first live run: "You pay 1 USDC to mira.eth. Nothing else changes." Approved, low risk, in about four seconds |

## Under the hood: one request, nine steps

When an agent calls the `exo-wallet` skill, the proposal goes to a small bridge service, which passes it to the Guardian. Any error at any step is a refusal.

| # | Step | What happens |
|---|---|---|
| 1 | **Parse and bind** | Checks the request's shape, that it's from your Safe and for Ethereum. Computes the approval hash from the chain, the module, the target, the value, the calldata and a fresh random salt |
| 2 | **Load secrets** | The NOWNodes key, the reviewers' key and your policy |
| 3 | **Simulate** | Runs the exact transaction from the Safe against current Ethereum state with NOWNodes' `debug_traceCall`, and decodes every ETH, token and NFT transfer and every approval |
| 4 | **Price** | Reads the Chainlink ETH/USD feed if ETH leaves the Safe (a stale price refuses); stablecoins count at $1; rounding always goes up, so it can't slip under a limit |
| 5 | **Check rules** | Your policy, applied to the *simulated* changes, never to the agent's words |
| 6 | **Explain** | Builds the sentence you'll hear from the simulation. "Nothing else changes" is said only when it's true. Hidden and direction-changing characters are stripped from token names |
| 7 | **Review** | Two AI models from different vendors each rate the risk from the decoded call and the simulated changes. Either saying "high", or failing to answer cleanly, refuses |
| 8 | **Decide** | Approve only if everything passed. Mark it auto-eligible only if risk is low, it's at or under your auto limit, every recipient is in the address book and it grants nothing |
| 9 | **Report** | Writes the verdict onchain to `ExoModule` as a signed CRE report: this hash is approved until 10 minutes from now |

## What happens when a transaction is approved

1. `ExoModule` records the approval for that exact hash, with its expiry.
2. The deck picks it up. Your earbuds read the explanation, the glasses pin it at the top of the Approvals panel, and the approve key blinks.
3. You press and release the key. (Or, if auto-approve is on and the Guardian marked it auto-eligible, the deck proceeds by itself.)
4. The deck's hot key calls `ExoModule.execute`. The module recomputes the hash and checks that it's approved, unexpired, never used, within the onchain ETH caps and not frozen. It marks the hash used, then asks the Safe to make the call.
5. The transaction goes out through NOWNodes. The ledger records the proposal, the verdict, the explanation and the receipt.

If you don't press within 10 minutes, the approval expires onchain and the deck moves the item to its expired list. Nothing is sent.

## What happens when a transaction is refused

1. Nothing can execute it. There's no approval for its hash, so the module would revert even if someone tried.
2. You hear the reason in plain English: for an airdrop "claim" that is really `setApprovalForAll`, that it would give an unknown contract control of a whole NFT collection.
3. The refusal goes in the ledger with its reason.
4. A refusal is final. The agents are instructed never to re-propose a refused transaction, with changes or otherwise. If you still want it, you change your policy, or you do it yourself with your own wallet.

## Changing your policy

Your policy is a secret of the Guardian workflow, so changing it means updating that secret, which is done from your own machine, never by an agent.

1. Edit the JSON: raise a limit, flip a switch, add or remove an address.
2. Update the `POLICY_JSON` secret. (In today's simulator setup it's an environment file that only the Guardian's own system user can read.)
3. The next proposal is checked against the new rules. Approvals already issued keep their 10-minute life; to stop them immediately, freeze.

The **onchain caps** on native ETH are separate: only the cold key can change them, with `setCaps`. So even a compromised policy can't push more ETH per transaction or per day through the module than your cold key allowed.

## Trusted addresses: the address book

The address book is your list of trusted wallets and contracts, and it's what most of the rules lean on.

| You want to | Do this |
|---|---|
| **Add a trusted wallet** | Add `"0xTheirAddress": "their name"` to `address_book` and update the secret. From then on, sends to them can go above your unknown-recipient threshold, can be auto-eligible, and are spoken by name |
| **Add a trusted contract to approve** | Add the contract (a DEX router, say) to the address book. Exact-amount token approvals to it become possible. Unlimited approvals stay off unless you also allow them |
| **Remove a wallet or contract** | Delete its line and update the secret. New proposals to it are treated as unknown immediately |
| **Rename someone** | Change the name; it's what the earbuds will say |

Agents can look names up to build a transaction ("send to mira.eth"), but the Guardian's address book is the one that counts, and agents can't edit it.

## What you can and can't do

Assuming the starting policy above ($100 per transaction, $300 per day, auto under $25, strangers allowed up to $50, approvals off):

### Approved

| Request | Result |
|---|---|
| "Send 20 USDC to Mira" (in the address book) | Approved, low risk, auto-eligible: "You pay 20 USDC to mira.eth. Nothing else changes." |
| "Send 80 USDC to Mira for rent share" | Approved; waits for your key (above the $25 auto limit) |
| "Send 0.01 ETH to Mira" | Approved, priced with the Chainlink feed |
| "Send 30 USDC to 0x7a2…" (not in the book) | Passes the rules (under the $50 stranger limit) unless a reviewer flags it, and is never auto: you hear the address and press the key |
| "Approve exactly 50 USDC for the address-book router" | Approved; an exact allowance to a trusted spender |

### Refused

| Request | Why |
|---|---|
| "Send 150 USDC to Mira" | Over the $100 per-transaction limit |
| A fourth $90 send in one day | Over the $300 daily limit |
| "Send 60 USDC to 0x7a2…" (not in the book) | Over the $50 limit for unknown recipients |
| "Approve unlimited USDC for this dApp" | Unlimited approvals are off |
| "Approve 10 USDC for 0xUnknown…" | Approvals to spenders outside the address book are off |
| "Claim this airdrop" that's really `setApprovalForAll` | Approval-for-all is off |
| Anything built from a photo the camera took | `camera` is a refused source |
| The agent says "send 20" but built a transfer of 200 | The simulation doesn't match the stated intent |
| A "transfer" that also moves an NFT or grants an allowance | A send must have exactly one effect |
| A call that would add an owner to the Safe or change its modules | Calls to the Safe or the module are always refused |
| A transaction that reverts in simulation | Refused: it wouldn't do what it says |
| A token swap on a DEX, an NFT marketplace purchase, staking, a DAO vote | Refused today: the Guardian doesn't decode those functions yet, and it refuses what it doesn't understand |
| Anything while the AI reviewers are down, or an ETH payment while the price feed is stale | Fail closed |
| Anything while the wallet is frozen | The module won't execute until the cold key unfreezes |

## Where it stands, and what's next

Built during the TOKEN2049 Origins hackathon, 6–7 October 2026. The workflow compiles to WebAssembly and runs in Chainlink's CRE simulator against live Ethereum mainnet data through NOWNodes, with both AI reviewers live. It has approved a real 1 USDC send to an address-book contact and refused an approval-for-all built from a camera photo. The contract and workflow have over 150 automated tests between them.

Not done yet, in order:

1. **Deploy to a Chainlink DON**, then pin the module to the Guardian's workflow ID and retire the simulation key.
2. **Run the `guard` handler inside CRE's Trusted Execution Environment**, so your rules are only ever decrypted inside an enclave.
3. **Parse the intent from your own signed voice command** rather than the agent's description.
4. **Decode more actions**: swaps on address-book DEXs first, then NFT purchases and votes, each with its own rules.
5. **Onchain token caps and a balance post-condition** in `ExoModule`, so ERC-20 limits are enforced by the contract too, not only by the Guardian.

The code is open: the workflow lives in `chain/cre/exo/`, the contract in `chain/contracts/src/ExoModule.sol`, and the full threat model, including everything above that it doesn't stop yet, in `docs/guardian.md` in the [Exo repository](https://github.com/kon-rad/argo-exo).
