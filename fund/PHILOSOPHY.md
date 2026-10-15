# Argo Fund — Investment Philosophy
### Why we believe a principles-first AI committee can run a small crypto fund well, and how we'll know if it can't

**Written:** 2026-10-14
**Applies to:** every meeting, every motion, every amendment. The constitution says *what* the fund does; this says *why*.

---

## TL;DR — the five things that matter

1. **Principles before predictions.** Ray Dalio's central idea in *Principles* is that good decisions come from writing down how you decide, applying it consistently, and refining it after every mistake. Our constitution is that written-down "how". An AI committee is unusually good at applying written principles every single time without being tired, scared or greedy. That consistency is the edge we're betting on, not price prediction.
2. **A barbell: a long-term core and a researched frontier.** Most of the fund owns the networks we believe will matter in ten years (ETH, BTC, stablecoins). A smaller sleeve backs the technology frontier (new protocols, new primitives), each bet with a written thesis, a catalyst and an exit. The core compounds; the frontier is where research can earn its keep.
3. **An idea meritocracy, made of disagreement on purpose.** Five partners with deliberately different worldviews vote on everything, and one of them argues the other side by duty. Dalio's "thoughtful disagreement" and "believability weighting" are built into the meeting: every view is recorded, and every partner's record is scored.
4. **Radical transparency as the audit trail.** Every view, vote, trade and amendment is written in plain English and kept forever. Konrad can read exactly how the fund reached any decision, and so can anyone the fund is open-sourced to.
5. **Honest about what this can't do.** Crypto assets are highly correlated, LLMs have no proven edge in short-term price prediction, and a $100 fund pays real costs. We measure against simply holding USDC and simply holding ETH, every week. If the committee can't beat a 50/50 hold after six months, the conclusion is the process, not the market.

---

## 1. Principles first

Dalio describes a loop: set goals, encounter problems, diagnose them to the root cause, design a change, do it, and repeat. His shorthand is *pain + reflection = progress*. The fund runs the same loop on a weekly clock:

| Dalio's loop | How the fund runs it |
|---|---|
| Have clear goals | The constitution's mandate: grow the fund over years; beat hold-USDC and hold-ETH; never lose more than the fund can recover from |
| Identify problems, don't tolerate them | `fund-review` lists every trade that lost money or broke the thesis it was bought on |
| Diagnose to the root cause | The review asks *which principle failed, or was missing*, not "the market went down" |
| Design a change | A lesson becomes a proposed amendment to the constitution |
| Do it | The committee votes the amendment (4 of 5); the constitution changes; the change is logged |

The constitution is meant to get better every month. A fund that makes the same mistake twice has a principle missing.

## 2. Understand the machine

Dalio's other big idea is that the economy is a machine of cause and effect, driven most of all by credit and liquidity cycles. Crypto, with no earnings to anchor it, is the asset most exposed to that machine. So the committee always starts by asking where we are in the cycle (Wren's seat), and only then asks what's cheap or exciting.

| Environment | What tends to work | Fund posture |
|---|---|---|
| Liquidity expanding, risk appetite rising | Core assets lead, frontier bets work | Core fully invested; frontier sleeve active |
| Liquidity expanding, but crowded leverage | Sharp flushes inside an uptrend | Hold the core, trim frontier, keep dry powder |
| Liquidity contracting | Almost everything falls together | Raise USDC; frontier only with exceptional theses |
| Panic, capitulation | The best entry points of the cycle | Basile's moment: buy the core from the USDC reserve |

## 3. The barbell: core and frontier

The constitution sets target bands (the committee can amend them):

| Sleeve | Target | What lives here | Horizon | How it's chosen |
|---|---|---|---|---|
| **Reserve** | 20–40% | USDC | Always | Dry powder and the fund's shock absorber |
| **Core** | 40–60% | ETH (or staked ETH: wstETH, cbETH), cbBTC | Years | Networks with proven, paying demand and the deepest liquidity |
| **Frontier** | 10–30% | New protocols and technologies on Base | Weeks to months | Each bet needs a research note, a written thesis, a catalyst and an exit |

Why a barbell rather than something in between: the core captures the long-term growth of the networks with as little decision-making as possible (fewer decisions, fewer mistakes, lower costs). The frontier is the only place where the committee's research can actually add something the market hasn't priced, so that's where the research effort goes. The middle (mid-cap tokens with neither a long track record nor a researched edge) is where funds lose money for no reason.

### The frontier, and why research leads it

The frontier sleeve follows **the direction of technology**, not price charts. The committee looks for categories where builders, users and capital are all starting to move:

- **Onchain AI agents** and agent payments
- **Restaking and shared security**
- **Tokenized real-world assets** (treasuries, credit) and the protocols that move them
- **DePIN** (decentralised physical infrastructure)
- **Intents, account abstraction and chain abstraction**
- **Zero-knowledge proving** as infrastructure

A frontier bet needs a `fund-research` note answering: what does it do, who pays for it, what's the token's claim on that value, what's the catalyst, what kills the thesis, and what's the exit. No note, no motion.

## 4. An idea meritocracy

Dalio's Bridgewater is built on *radical truth and radical transparency*, *thoughtful disagreement*, and *believability-weighted decision making*: weigh each person's opinion by their track record on that kind of question. The fund borrows all three:

| Principle | How the committee implements it |
|---|---|
| Thoughtful disagreement | Five partners with clashing worldviews (macro, onchain data, risk, momentum, contrarian). Views are written *before* reading the others, so nobody anchors on the first speaker |
| Argue the other side | Basile runs a pre-mortem on every motion and votes no on unexamined consensus |
| Believability | `fund-review` scores every partner's calls against outcomes each week. In the MVP votes count equally; the scores are shown in every brief, and after eight weeks of data the committee may amend the constitution to weight votes by believability |
| Radical transparency | Minutes, votes, trades and amendments are kept in plain markdown and shown on the kiosk |
| The Warden's block | One partner can stop a motion that breaks the fund's own written rules, never on taste |

## 5. Why an AI committee, and why it might work

What we expect the committee to be **better** at than a person trading alone:

- **Consistency.** It applies the constitution every time. It doesn't revenge-trade, doesn't panic at 3 am, doesn't fall in love with a position.
- **Breadth.** It reads more research than a person can, every day, and writes down what it read.
- **Memory.** Every decision is recorded with its reasoning, so mistakes can actually be diagnosed.
- **Structured dissent.** Disagreement is built in, rather than depending on whether the person happens to doubt themselves that day.

What it is **not** good at, and how we guard against it:

| Weakness | Guard |
|---|---|
| No proven edge in short-term price prediction | Few trades; a core that mostly holds; the frontier only on researched theses |
| Five personas can still share one model's blind spots | Basile's duty to dissent; Konrad's human approval on every trade; the weekly review against benchmarks |
| Crypto assets move together in a crash, so "diversification" is limited | A real USDC reserve; Dalio's "Holy Grail" (many uncorrelated return streams) is mostly unavailable inside crypto, and we say so |
| Costs (gas, slippage, AI usage) are large relative to $100 | Few trades; costs reported in every review |
| Hallucinated facts | Research notes link sources; Kaito cites numbers; a claim with no source doesn't make it into a motion |

## 6. How we'll know if it's working

| Check | When | What it means |
|---|---|---|
| NAV vs hold-USDC, hold-ETH, and a 50/50 hold | Every weekly review | The honest scoreboard |
| Partner believability scores | Weekly | Which worldview is earning its seat |
| Constitution amendments | Monthly | Is the fund learning, or repeating mistakes? |
| Konrad's refusals | Ongoing | How often does the human disagree, and was the human right? |
| **Six-month verdict** | 2027-04 | If the fund trails the 50/50 hold net of costs, change the process (or keep only the research and education) |

The fund is also an education machine: every meeting is a lesson in how markets, protocols and risk actually work. Even if the returns are only average, Konrad should finish the year understanding DeFi far better than at the start. That counts.

---

*Ideas in §1 and §4 are paraphrased from Ray Dalio's* Principles *(2017) and Bridgewater's public descriptions of its culture. The application to an AI committee is ours.*
