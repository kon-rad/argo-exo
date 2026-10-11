# The Transaction Guardian

How Exo uses a Chainlink CRE workflow to decide whether an AI agent's transaction may run: how the rules are defined, how they're checked, how the verdict is enforced onchain, and which attacks it stops. It ends with an honest list of what it doesn't stop yet.

**Status (2026-10-07):** built during the TOKEN2049 Origins Hackathon. The workflow runs in Chainlink's CRE simulator, and reports reach `ExoModule` through the simulation forwarder. It isn't deployed to a Chainlink DON yet. Everything below describes the code in this repository: `chain/cre/exo/` (workflow) and `chain/contracts/src/ExoModule.sol` (contract).

---

## In one paragraph

Exo's agents can propose any transaction you ask for in plain English, but they can't execute one. Every proposal goes to the **Guardian**, a Chainlink CRE workflow. It simulates the exact transaction against live Ethereum state, works out what would really change in your wallet, checks that against rules the agent can't read or edit, asks two independent AI reviewers for a second opinion, and explains the result in one plain sentence. Its verdict is bound to that exact transaction and written onchain to `ExoModule`, a Safe module. The module runs a transaction only if the Guardian approved its exact hash, it hasn't expired, it hasn't run before and the wallet isn't frozen. The deck's hot key then submits it, after you press the physical approve key, or automatically for small transactions to known recipients.

```
 agent / voice / camera ── proposal ──►  GUARDIAN (Chainlink CRE workflow)
                                          1 check the request is for this wallet
                                          2 simulate the exact tx (NOWNodes debug_traceCall)
                                          3 price it (Chainlink ETH/USD feed)
                                          4 check your rules (on the simulated changes)
                                          5 explain it in plain English (from the simulation)
                                          6 two AI reviewers (can only add a refusal)
                                          7 verdict: approve / refuse
                                                │ signed report
                                                ▼
                                       ExoModule (Safe module, onchain)
                                          stores: this exact tx hash is approved until T
                                                │
 you press the approve key ──► deck hot key calls ExoModule.execute(tx) ──► Safe runs it
```

---

## 1. Why a guardian at all

AI is becoming the interface to money. You say "send 20 USDC to Mira" and an agent works out the steps. That's a huge improvement over pasting addresses and signing hex. It also means the agent is now the thing that can be fooled. An agent that reads webpages, photos, messages and its own memory can be steered by anything in them: a QR code, a hidden instruction on a page, a planted "fact" in its memory.

The largest theft in crypto history shows what happens when the thing that shows you a transaction is also the only thing that tells you what it does. In February 2025, attackers attributed by the FBI to North Korea compromised the web interface of a multisig wallet. The exchange's signers saw a routine transfer and signed a transaction that replaced the wallet's logic, and about $1.5 billion left. The smart contracts were never broken. The interface lied.

The Guardian applies the opposite principle: **the layer that talks to you never decides.** The agent proposes. Separate code works out what the transaction really does. The chain enforces the verdict. A human approves.

---

## 2. How the rules are defined

### 2.1 Where they live

Your rules are a JSON policy stored as a **CRE secret** (`POLICY_JSON`), not in the workflow's public config and not anywhere the agent can reach. The agent never sees the numbers, so it can't aim just under a limit. It also can't change them: changing the policy means updating the secret, and that's done from the owner's machine, not by the agent.

The policy is validated with a strict schema (`PolicySchema` in `chain/cre/exo/src/lib/rules.ts`). **Unknown keys are refused.** A typo in a safety switch, such as `allow_aproval_for_all`, makes the policy unreadable, and an unreadable policy refuses every transaction. It never silently falls back to a default.

### 2.2 What a policy contains

| Field | Type | What it does |
|---|---|---|
| `address_book` | address → name | Trusted recipients and spenders. Names are what you hear ("mira.eth"). |
| `max_usd_per_tx` | USD | Most value that may leave the wallet in one transaction |
| `max_usd_per_day` | USD | Most value that may leave in a day, including this transaction |
| `auto_max_usd` | USD | At or below this, an approved low-risk send to a known recipient may run without a key press |
| `require_known_recipient_over_usd` | USD | Above this, every recipient must be in the address book |
| `allow_unlimited_approvals` | bool | Whether a token approval may be unlimited (also governs approvals to unknown spenders) |
| `allow_approval_for_all` | bool | Whether a contract may be given control of a whole NFT collection |
| `refuse_sources` | list | Proposal sources never trusted to move funds (e.g. `camera`) |
| `stablecoins` | addresses | Tokens priced at $1 for the limits |

An example, with placeholder addresses:

```json
{
  "address_book": { "0x1111111111111111111111111111111111111111": "mira.eth" },
  "max_usd_per_tx": 100,
  "max_usd_per_day": 300,
  "auto_max_usd": 25,
  "require_known_recipient_over_usd": 50,
  "allow_unlimited_approvals": false,
  "allow_approval_for_all": false,
  "refuse_sources": ["camera"],
  "stablecoins": ["0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"]
}
```

### 2.3 Rules that aren't in the policy

Some rules are fixed in code, because no setting should be able to turn them off:

- Unknown contract functions are refused.
- A transaction that reverts in simulation, or has effects the Guardian can't read, is refused.
- A transaction aimed at the wallet itself or at its guard module is refused.
- A transaction that doesn't come from the protected Safe, or is for another chain, is refused.
- If the AI reviewers can't be reached or their answers can't be parsed, the transaction is refused.

---

## 3. How the rules are checked

The `guard` handler (`chain/cre/exo/src/lib/guard.ts`, `runGuard`) runs these steps in order. **Any error at any step is a refusal** (`failClosed`).

| # | Step | What happens | Refuses when |
|---|---|---|---|
| 1 | **Parse and bind** | The request (`proposal_id`, `source`, `intent`, `tx {chain_id, to, value, data, salt}`, `from`) is schema-checked. The approval hash is computed: `keccak256(abi.encode(chainId, module, to, value, keccak256(data), salt))` | Malformed input; wrong chain; `from` isn't the protected Safe; Guardian not configured |
| 2 | **Load secrets** | NOWNodes key, AI reviewer key, `POLICY_JSON` | Secrets or policy missing or unreadable (with a fixed reason, so the policy text never leaks into an error) |
| 3 | **Simulate** | The exact transaction runs **from the Safe** against current mainnet state with NOWNodes `debug_traceCall` (or `eth_simulateV1`). The trace becomes a list of changes: ETH and token transfers, NFT transfers, approvals, approvals-for-all. Reverted sub-calls are excluded | The transaction reverts; any event the decoder can't read (`otherEvents > 0`) |
| 4 | **Price** | If ETH leaves the wallet, the Chainlink ETH/USD feed is read; stale or missing prices are rejected. Stablecoins count at $1. Amounts use exact integer arithmetic and round up to the cent, so rounding can't slip past a limit | ETH leaves and no reliable price is available |
| 5 | **Rules** | `checkRules` runs on the **simulated changes**, never on the agent's words or the English explanation (see §3.1) | Any violation |
| 6 | **Explain** | `explain` builds the sentence you hear from the simulated changes: "You pay 20 USDC to mira.eth. Nothing else changes." "Nothing else changes" is only said when nothing was granted and every event was understood. Token symbols and names are stripped of control, zero-width and bidi characters, so a token can't hide text | — |
| 7 | **Review** | Two AI models from different vendors each get the decoded call, the simulated changes, the explanation and the rule findings, all quoted as data. They answer `{risk: low/medium/high, reason}` | Either says `high`; an answer isn't exactly that JSON; a reviewer is unreachable; fewer than two answers |
| 8 | **Decide** | `decide` approves only if every check above passed. Risk is the higher of the two reviews. A transaction is `auto_eligible` only when risk is low, the priced outflow is at or below `auto_max_usd`, every recipient is in the address book and it grants no approvals | Any reason above |
| 9 | **Report** | The verdict is encoded as `(kind, txHash, expiresAt, reasonHash)` and written to `ExoModule` as a signed CRE report. The expiry comes from the workflow's own clock, never from the request. A refusal is written too, and it revokes any pending approval of the same hash | If an approval can't be written onchain, the result is turned into a refusal, so nothing queues an approval that doesn't exist |

### 3.1 The rule checks in detail

| Check | Rule |
|---|---|
| Source | `source` in `refuse_sources` (e.g. a proposal built from a camera photo) → refuse |
| Self-calls | Target is the Safe or `ExoModule` → refuse |
| NFT control | `setApprovalForAll(…, true)` and `allow_approval_for_all` is false → refuse |
| Unlimited approvals | Allowance ≥ 2⁹⁶−1 (covers max uint256, Permit2's max and uint96 "infinite") and not allowed → refuse |
| Approvals to strangers | Any grant to a spender outside the address book, unless unlimited approvals are explicitly allowed → refuse |
| Per-transaction limit | Priced outflow > `max_usd_per_tx` → refuse |
| Daily limit | Today's spend + this outflow > `max_usd_per_day` → refuse |
| Unknown recipients | Outflow > `require_known_recipient_over_usd` to an address outside the book → refuse. An unpriced token to an unknown address always refuses |
| Intent match | For a `send`, the simulation must show **exactly one** outflow from the wallet, of exactly the stated token and amount (integer-exact, no rounding), to exactly the stated address, and nothing else granted → otherwise refuse |
| Request type | Intent kind must be one the Guardian knows → otherwise refuse |

### 3.2 What the Guardian understands today

The calldata must be a plain ETH send or one of: `transfer`, `approve`, `transferFrom`, `increaseAllowance`, `setApprovalForAll`, `safeTransferFrom` (ERC-20 and ERC-721). **Everything else is refused as "a function this check does not understand",** including DEX swaps, marketplace NFT purchases, staking and governance calls. This is deliberate: an unknown function's effects may not be fully visible in a trace. Supporting more actions means adding their decoders and rules, not loosening the check.

### 3.3 The AI reviewers: a second "no", never a "yes"

The reviewers can only make things stricter. A transaction that breaks a rule is refused whatever they say. A transaction that passes every rule is refused if either one says `high`, disagrees in an unreadable way, or doesn't answer. Their prompt quotes every field as JSON inside labelled data blocks, escapes anything that could close a block, and marks the agent's own description as **untrusted**. A prompt injection hidden in a token name or an intent summary can, at worst, fail to add a refusal. It can't approve anything.

---

## 4. How the verdict is enforced onchain

`ExoModule` is the only path from the agent to the money. It's a Chainlink CRE consumer contract (it receives the Guardian's reports) and a Safe module (it can ask the Safe to execute).

| Property | How |
|---|---|
| **Only approved transactions run** | `execute(to, value, data, salt)` recomputes the hash and requires `approvedUntil[hash]` to be set and unexpired |
| **Exactly that transaction** | Change `to`, `value`, `data`, `salt`, the chain or the module, and the hash changes: not approved |
| **Once** | `used[hash]` is set before the call; a hash can never run again |
| **Short-lived** | An approval must expire in the future and within 1 hour of landing (`MAX_APPROVAL_TTL`); the workflow uses 10 minutes |
| **Revocable** | A refusal report for the same hash deletes a pending approval |
| **Only the deck can execute** | `msg.sender == deck`; the deck's key holds gas only |
| **No `delegatecall`** | The Safe is always called with operation 0 (a plain call) |
| **No wallet takeover** | `execute` reverts if the target is the Safe or the module, so an approval can never add owners, enable modules, set guards, change the threshold or change the module's caps |
| **Native ETH capped** | Per-transaction and per-UTC-day ETH caps, independent of the Guardian |
| **Freeze** | The Guardian, the deck or the owner can freeze; **only the owner can unfreeze** |
| **Owner = cold key** | The Safe and the module are owned by an air-gapped key that never joins the daily flow. It can change caps, replace the deck key, or remove the module and cut the agent off completely |
| **Reports must be authenticated** | Behind the real Chainlink forwarder: a pinned workflow ID or author. In simulation: a dedicated reporter key. With neither configured, every report reverts |

### Why onchain, and why Chainlink

A guardian that runs on our own server is only as trustworthy as that server. Exo's Guardian is a CRE workflow. Once it's deployed to a Chainlink DON, its verdicts are signed by Chainlink's independent node operators, and the contract accepts only reports that come through Chainlink's forwarder carrying the expected workflow ID. Changing the Guardian's code changes its workflow ID, so even a compromised deployment account can't swap in permissive logic without the cold key re-pinning the ID. The rules live in CRE's secrets store instead of on any server we run. And because the module enforces the verdict, a warning can't simply be clicked past: the transaction cannot run without it.

---

## 5. Threats it protects against

| Threat | Example | What stops it |
|---|---|---|
| **Fake claims and drainer contracts** | A "claim your airdrop" QR or link whose transaction is really `setApprovalForAll` to an unknown contract | Simulation shows the approval; the approval rules refuse it; the explanation says what it really does |
| **Unlimited and stranger approvals** | A dApp asks for an unlimited USDC allowance | Unlimited and unknown-spender approvals refused by default |
| **Prompt injection into the agent** | A webpage tells the agent to "send the balance to 0xBad…" | Unknown recipient + limits + intent match + human key; the agent holds no key that can move funds |
| **A confused agent** | The agent builds a transfer of 200 instead of 20, or to the wrong address | Exact intent match on token, amount and recipient |
| **Hidden extra effects** | A "transfer" that also moves an NFT or grants an allowance | `send` must have exactly one effect; unreadable events refuse; "nothing else changes" is only said when true |
| **Malicious token labels** | A token symbol containing hidden characters or instructions | Labels cleaned before display; reviewers treat them as quoted data |
| **Replay and tampering** | Reusing an approval, or changing the calldata after approval | Hash binding, single use, expiry |
| **Wallet takeover** | An "upgrade" that adds an owner or enables a module | Self-calls refused by the Guardian and reverted by the module; no `delegatecall` |
| **Stolen deck key** | Someone copies the deck's hot key | It holds gas only and can execute only what the Guardian already approved, once, before expiry |
| **Interface lies** | Whatever builds the transaction also describes it | The description is computed by the Guardian from the simulation, not taken from the agent |
| **Panic** | You think something is wrong | Freeze from the deck's buttons; only the cold key can unfreeze |
| **A failing dependency** | A price feed, RPC or AI reviewer is down | Fail closed: refuse. Your funds stay where they are; only the agent's ability to move them pauses |

---

## 6. Known limits (what it doesn't stop yet)

We'd rather list these than have you find them.

| Limit | Why it matters | Mitigation today | Plan |
|---|---|---|---|
| **Simulated, not yet DON-signed** | In simulation, reports come through a permissionless mock forwarder; the module trusts a dedicated reporter key | Reporter-key gate, small balances, onchain caps | Deploy to a DON; switch to the real forwarder, then pin the workflow identity, then remove the reporter key (order matters; see `chain/README.md`) |
| **Not yet in a TEE** | The `guard` handler runs as a regular CRE handler; rules and keys are CRE secrets, not enclave-only | Secrets never leave CRE; the agent can't reach them | Switch to `handlerInTee` once Confidential Workflows access is confirmed |
| **The intent comes from the agent** | A hijacked agent can state an intent that matches its malicious transaction, so the intent match catches mistakes, not lies | Address book, limits, the spoken explanation and the human key | Have the deck sign your spoken words and let the Guardian parse the intent from them |
| **Some request fields are trusted** | `source` and today's spend come from the requesting service. A compromised requester could understate today's spend or relabel a camera proposal | The per-transaction limit, the address book and the key still hold | Read today's spend from onchain state; authenticate the source with the deck's signature; set `authorizedKeys` on the triggers before deploy |
| **Small sends to strangers** | Below `require_known_recipient_over_usd`, a priced send to an unknown address can pass the rules | The human key; set the threshold to 0 for strict mode | Default to strict |
| **ERC-20 amounts aren't capped onchain** | Only native ETH has onchain caps; token limits are enforced by the Guardian | The Guardian's USD limits, the key | Per-token caps and a balance post-condition in `ExoModule` |
| **Allowances outlive the transaction** | An approved allowance lets the spender move funds later | Approvals only to address-book spenders, never unlimited by default | A "revoke my approvals" action |
| **Simulation ≠ execution** | State can change between simulation and execution (up to the approval's lifetime) | 10-minute approvals; exact outflow checks | A balance post-condition onchain |
| **Daily cap is a UTC calendar day** | Up to twice the day cap can leave across UTC midnight | Per-transaction cap | Rolling window |
| **Narrow action set** | Swaps, marketplace buys, staking and governance are refused | Fail closed is the safe default | Add decoders and rules per action |
| **Reviewers share a gateway** | Both models are called through one API provider | Rules decide; reviewers can only refuse | Call each vendor directly |

---

## 7. Verify it yourself

| What | Where |
|---|---|
| The pipeline | `chain/cre/exo/src/lib/guard.ts` |
| Rules and policy schema | `chain/cre/exo/src/lib/rules.ts` |
| Verdict logic | `chain/cre/exo/src/lib/verdict.ts` |
| Trace decoding / explanation | `chain/cre/exo/src/lib/trace.ts`, `explain.ts` |
| AI review prompt and parser | `chain/cre/exo/src/lib/judge.ts` |
| The contract | `chain/contracts/src/ExoModule.sol` |
| Contract threat model and DON migration | `chain/README.md` |
| Running the simulation | `chain/cre/README.md` |
| Tests | `chain/cre/exo/test/` (`bun test`), `chain/contracts/test/` (`forge test`) |
| Shared approval-hash vector | `chain/test-vectors/approval-hash.json` (checked by the Solidity, TypeScript and Python tests) |
