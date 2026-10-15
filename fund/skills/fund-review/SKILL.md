---
name: fund-review
description: Weekly review of the Argo Fund — NAV against hold-USDC, hold-ETH and a 50/50 hold, net of costs; every trade's outcome against the thesis it was bought on; a believability score for each of the five partners; and lessons proposed as constitution amendments for the next meeting. Use for the Sunday review cron, or when asked "how is the fund doing", "review the fund", or "which partner has been right".
---

# fund-review

Dalio's loop, run weekly: find what went wrong, diagnose it to a missing or broken principle, propose the fix.

## Paths
`$FUND_DIR/trade-log.md`, `$FUND_DIR/meetings/`, `$FUND_DIR/state/proposals/*.json` (via `fund-trade proposals --all`), `$FUND_DIR/reviews/` (output), `$FUND_HOME/PHILOSOPHY.md`.

## Steps
1. `fund-trade portfolio` for NAV now. The opening NAV and date are at the bottom of `trade-log.md` (the first entry) or in the constitution's adoption date; the fund opened with 100 USDC.
2. **Benchmarks since inception.** Fetch ETH's price on the inception date and today (DefiLlama historical prices, `https://coins.llama.fi/prices/historical/<unix>/coingecko:ethereum`). Compute: hold-USDC (= opening NAV), hold-ETH, and 50/50. Show the fund's NAV against all three, in USD and %.
3. **Costs.** Sum gas and slippage where the trade log shows them; say "unknown" where it doesn't.
4. **Trade outcomes.** For every trade executed since the last review: price then, price now, return, and **whether the thesis it was bought on is intact, broken or proven**. Quote the motion.
5. **Believability.** For each partner, score this week's calls: a vote is *right* if a yes on a trade that's up (or a no on one that's down, or a no on a motion that failed and would have lost), *wrong* the other way. Keep a running table: calls, right, wrong, % right, carried over from the previous review. Say plainly that the sample is small.
6. **Konrad's refusals.** For every refused proposal: what would it have returned? Was the human right?
7. **Lessons.** At most three. Each: what happened → which principle failed or was missing → a proposed amendment in exact constitution wording, marked "for the next meeting". Don't amend the constitution yourself.
8. Write `$FUND_DIR/reviews/YYYY-Www.md` (ISO week), house style: TL;DR of five items first, then the sections above, then "What's confirmed vs. inferred", then sources.

## Rules
- Never invent a price; if a historical price is unavailable, say so and skip that benchmark.
- A losing trade isn't automatically a mistake, and a winning one isn't automatically right. Judge the decision against the information at the time.
