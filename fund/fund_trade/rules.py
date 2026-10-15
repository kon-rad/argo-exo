"""The rule check every trade passes through before it's prepared.

MVP: no rules. Konrad waived them while the fund holds ~100 USDC (2026-10-14); the Safe's 2-of-3 signatures are the
safety. This function is the one place to add them later (token allowlist, max trade size, trades per day,
slippage cap, recipient = Safe). It returns a list of violations; an empty list means "go"."""
from __future__ import annotations

RULES_ACTIVE: list[str] = []   # names of the rules in force, shown in `status` and in every trade-log entry


def check(intent: dict, portfolio: dict) -> list[str]:  # noqa: ARG001 (the signature the real rules will use)
    return []


def describe() -> str:
    return ", ".join(RULES_ACTIVE) if RULES_ACTIVE else "none (MVP: waived)"
