# Argo Fund — Constitution
### The fund's strategy and rules. Amended only by a 4-of-5 committee vote; every change is logged at the bottom.

**Adopted:** {{DATE}}
**Philosophy:** see `PHILOSOPHY.md` in the fund skills (principles first; a long-term core plus a researched frontier).

---

## 1. Mandate
- Grow the fund over years, starting from **100 USDC on Base**.
- Beat three benchmarks, net of gas and slippage: **hold USDC**, **hold ETH**, and a **50/50 USDC/ETH hold**.
- Never take a position whose failure the fund couldn't recover from.
- Teach: every decision is written so a beginner can follow why it was made.

## 2. Allowed actions
- Spot swaps on Base through the KyberSwap aggregator, executed from the fund's Safe.
- Not yet: lending, liquidity provision, leverage, perps, bridging, copy trading. Each needs an amendment and a research note first.

## 3. Allocation targets (guidance, not hard limits)

| Sleeve | Target | Assets |
|---|---|---|
| Reserve | 20–40% | USDC |
| Core | 40–60% | ETH / WETH / wstETH / cbETH, cbBTC |
| Frontier | 10–30% | Researched bets on Base; each with a thesis, catalyst and exit |

## 4. Limits
None enforced in the MVP (waived by Konrad on 2026-10-14 while the fund is ~100 USD). The committee should behave as if these applied, and may vote to make them binding:
- No single frontier position above 10% of NAV.
- At most 2 trades per meeting.
- Slippage tolerance 1%.

## 4a. Profits and withdrawals
- Withdrawals go **only** to the savings vault (the Pi Zero's offline address). The deck refuses any other destination.
- **Profit rule:** when NAV closes a weekly review more than 25% above the high-water mark (the highest NAV at a previous withdrawal, starting at the opening balance), the committee should move half of the excess to savings, in USDC. A guideline the committee votes on, not automatic.
- Konrad can request a withdrawal at any time (`fund-trade withdraw`); it needs his approve press like any trade.

## 5. Process
1. **Research** (`fund-research`) answers the questions in §7 and scans the frontier.
2. **Meeting** (`fund-committee`): brief → five independent views → one rebuttal round → votes.
3. **A trade passes** with 3 yes votes and no block. **An amendment passes** with 4 yes votes.
4. **The Warden's block:** Odalys Fen may block a trade that breaks a written clause of this constitution, citing the clause.
5. **Human approval:** Konrad approves or refuses every trade on the kiosk (or the phone). Unapproved proposals expire after 48 hours.
6. **Review** (`fund-review`), weekly: performance vs benchmarks, a believability score per partner, lessons proposed as amendments.

## 6. Current thesis
The fund is new. First, build the core: establish an ETH position and a smaller cbBTC position over the first meetings, keeping at least 40% in USDC until the committee has a clear read on the liquidity cycle. No frontier bets until `fund-research` has produced at least one complete frontier note.

## 7. Open questions for research
- Where are we in the liquidity cycle? (stablecoin supply trend, ETF flows, real yields)
- Which frontier categories on Base have paying users today?
- Is staked ETH (wstETH/cbETH) worth the extra smart-contract risk for the core?

## 8. Amendment log

| Date | Amendment | Vote |
|---|---|---|
| {{DATE}} | Constitution adopted | Founding |
