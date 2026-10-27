"""The rule check every trade passes through before it's prepared.

Minimal on purpose (Konrad, 2026-10-28: trade live from day one, rules very light). The Safe's 2-of-3 signatures and
the deck's own checks (chain, Safe, destination, hash) stay the main guard. Each rule returns a reason string; an
empty list means "go". Add rules here, nowhere else."""
from __future__ import annotations

MAX_SLIPPAGE_BPS = 100          # 1%
MAX_TRADES_PER_DAY = 2          # executed swaps per UTC day

RULES_ACTIVE: list[str] = [f"slippage<={MAX_SLIPPAGE_BPS}bps", f"trades/day<={MAX_TRADES_PER_DAY}"]


def check(intent: dict, portfolio: dict, ctx: dict | None = None) -> list[str]:  # noqa: ARG001
    """ctx: {"slippage_bps": int, "executed_today": int}. Swaps only; withdrawals to savings are never limited here."""
    ctx = ctx or {}
    out: list[str] = []
    if int(ctx.get("slippage_bps", 0)) > MAX_SLIPPAGE_BPS:
        out.append(f"slippage {ctx['slippage_bps']} bps is over the {MAX_SLIPPAGE_BPS} bps cap")
    if int(ctx.get("executed_today", 0)) >= MAX_TRADES_PER_DAY:
        out.append(f"{ctx['executed_today']} trades already executed today (max {MAX_TRADES_PER_DAY})")
    return out


def describe() -> str:
    return ", ".join(RULES_ACTIVE) if RULES_ACTIVE else "none"
