---
name: fund-committee
description: Convene the Argo Fund investment committee — five AI partners (Wren Hollis, Kaito Brandt, Odalys Fen, Juno Achebe, Basile Marr) read the constitution, research and portfolio, write independent views, debate once, and vote on trades and constitution amendments. Passed trades become proposals waiting for Konrad's approval on the kiosk. Use for the daily meeting cron, or when asked to "convene the committee", "hold a fund meeting", "what does the fund think about X", or "vote on <trade>".
---

# fund-committee

Runs one meeting of the Argo Fund committee and records it. Works under Claude Code or Hermes.

## Paths
- `$FUND_HOME`: the fund's code and identity files (`personas/`, `PHILOSOPHY.md`, `templates/`, `bin/fund-trade`).
- `$FUND_DIR`: the live fund (`constitution.md`, `trade-log.md`, `meetings/`, `research/`, `reviews/`, `state/`).
- `fund-trade` means `$FUND_HOME/bin/fund-trade`. It prints JSON. Never read key files; you don't need them.

## Steps

### 1. Gather (you are the chair)
1. Read `$FUND_DIR/constitution.md` and `$FUND_HOME/PHILOSOPHY.md`.
2. Run `fund-trade portfolio` (holdings, NAV) and `fund-trade proposals` (anything still waiting for Konrad).
3. Read the last 2 files in `$FUND_DIR/meetings/`, the latest `$FUND_DIR/reviews/` file if any, the top of `$FUND_DIR/trade-log.md`, and every `$FUND_DIR/research/` note from the last 7 days.
4. If today's minutes already exist (`meetings/YYYY-MM-DD.md`), stop unless asked to hold a second meeting (then name it `YYYY-MM-DD-2.md`).

### 2. Brief
Write the brief (one screen): NAV and holdings with % of NAV by sleeve (reserve / core / frontier), what changed since the last meeting, new research in one line each, proposals still pending, the last review's lessons, and each partner's believability score from the latest review (if any).

### 3. Views (independent)
For each partner, in this order: Wren Hollis, Kaito Brandt, Odalys Fen, Juno Achebe, Basile Marr.
- Load `$FUND_HOME/personas/<first>-<last>.md` and write that partner's view **as that partner**, from the brief and the research only.
- **Independence matters.** If your harness can run subagents, run each partner as its own subagent with its persona file and the brief, and collect the five views. If not, write each view in turn and do not revise an earlier view after writing a later one.
- Each view: ≤ 200 words, in the partner's voice, ending with the indicator that would prove them wrong. Each partner may table **at most one motion**:
  - a trade: `Sell <amount> <TOKEN> for <TOKEN>` (amount in tokens, e.g. `30 USDC`; tokens by symbol ETH, WETH, USDC, cbBTC, cbETH, wstETH, AERO, or by 0x address), with a one-sentence reason;
  - an amendment: the constitution section and its new text;
  - profit-taking: `Move <amount|all> <TOKEN> to savings` (to the Pi Zero vault), when the constitution's profit rule applies;
  - or "no motion".
- A partner who can't produce a view (error, junk) is recorded as **absent**. Quorum is 4; without it the meeting is a hold.

### 4. Debate
One round: each partner reads the other four views and replies in ≤ 80 words. Basile must give a three-sentence pre-mortem of the leading motion.

### 5. Vote
Each partner votes on every motion: `yes`, `no`, `abstain`, or (Odalys only, citing a constitution clause) `block`. One sentence of reasoning per vote.
- A **trade passes** with ≥ 3 yes and no block. At most **2 trades** pass per meeting (most yes votes first; tie → smaller trade).
- An **amendment passes** with ≥ 4 yes.
- Check a trade is possible before it passes: the Safe must hold the amount being sold (from the portfolio). An impossible trade fails with that reason.

### 6. Record
1. Write `$FUND_DIR/meetings/YYYY-MM-DD.md` from `$FUND_HOME/templates/meeting.md`: brief, five views, motions, debate, votes table, outcome.
2. For each passed trade run:
   ```
   fund-trade propose --meeting YYYY-MM-DD --motion "<one-sentence reason>" --sell <TOKEN> --buy <TOKEN> --amount <n> \
     --votes '[{"partner":"Wren Hollis","vote":"yes","reason":"...","view":"<one line of their view>"}, ...all five...]'
   ```
   For a passed profit-taking motion use `fund-trade withdraw --token <TOKEN> --amount <n|all> --reason "<why>" --meeting YYYY-MM-DD --votes '<same JSON>'`.
   Put the returned proposal id in the minutes' outcome.
3. For each passed amendment, edit `$FUND_DIR/constitution.md` (only the amended section) and add a row to its amendment log: date, the amendment in one line, the vote (e.g. 4–1).
4. Reply with a 3–5 line summary: NAV, motions and results, proposal ids waiting for Konrad, and where the minutes are.

## Rules for the chair
- Never invent data. A number in a view must come from the portfolio, a research note or a source the partner fetched and named.
- Never execute or approve trades. Proposals wait for Konrad.
- "Hold" is a good outcome. Don't manufacture motions to look busy.
- Write for a beginner: a reader who doesn't know DeFi should understand why each motion passed or failed.
