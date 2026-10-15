---
name: fund-research
description: Research for the Argo Fund committee — a daily market snapshot plus deep notes on the questions in the fund's constitution and on frontier crypto/DeFi protocols and technologies (onchain AI agents, restaking, tokenized RWAs, DePIN, intents, ZK). Writes dated research notes the committee reads before voting. Use for the daily research cron, or when asked to "research X for the fund", "find frontier opportunities", or "write a fund research note on <protocol>".
---

# fund-research

Writes the evidence the committee votes on. Works under Claude Code or Hermes.

## Paths
`$FUND_DIR/constitution.md` (§7 "Open questions for research", §6 "Current thesis"), `$FUND_DIR/research/` (output), `$FUND_DIR/meetings/` (what the partners asked for).

## Each run
1. Read the constitution's current thesis and open questions, and the "indicator that would prove me wrong" lines in the last meeting's views.
2. Write **one market snapshot**: `research/YYYY-MM-DD-snapshot.md`, at most one screen:
   - ETH and BTC price and 7/30-day change; total stablecoin supply and its 30-day change; spot ETF net flows (last week); funding rates (crowded or not); notable events in the next 14 days.
   - Each figure with its source link and the date it was read. If a source is down, write "unavailable", never a guess.
3. Write **one deep note** (`research/YYYY-MM-DD-<slug>.md`) on the most important open question, or on one frontier protocol or category. Rotate: don't research the same topic twice in a week unless the committee asked.

4. **Mondays: the copy-trading watchlist** (method: `$FUND_DIR/research/2026-10-14-copy-trading-research.md`). Maintain `$FUND_DIR/copy-watchlist.md` (10–20 wallets: address, label, why listed, last score, date added) and write `research/YYYY-MM-DD-watchlist.md`: what the watchlist bought and sold on Base this week, with **clusters** (3+ wallets accumulating the same asset) highlighted and every claim linked to a block explorer or DeBank. First Monday of the month: re-score every wallet against the note's criteria, drop failures, add at most 3. The watchlist is evidence for the committee, never an order: no motion may cite it as its only reason.

## Deep-note format (house style)
```
# <Title>
### <one-line subtitle: what decision this informs>

**Researched:** YYYY-MM-DD
**Question:** <the question, concretely>
**For:** <which partner(s) asked, or "frontier scan">

## TL;DR — five things that matter
1. **Bolded claim.** Reasoning.

## What it is and who pays for it
## The token's claim on that value
## Risks and how it fails        (contracts, audits, admin keys, oracle, liquidity depth on Base)
## Catalyst and timeline
## If the fund bought it: thesis, size, exit
## What's confirmed vs. inferred
## Sources
```

## Frontier notes must answer
What does it do? Who pays for it today, and how much (fees/revenue with a source)? What does the token capture? What's the catalyst? What kills the thesis? Can the fund exit its whole position on Base at < 1% price impact (check liquidity on GeckoTerminal or DefiLlama)? Is the token on Base, and its 0x address?

## Rules
- Prefer primary sources (protocol docs, DefiLlama, block explorers, issuer pages) and say when a figure is secondhand.
- Never fabricate a number. Unknown is an answer.
- Write for a beginner: define a term the first time you use it.
