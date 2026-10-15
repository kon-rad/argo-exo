"""KyberSwap aggregator on Base: route, then build calldata with the Safe as sender and recipient.
Same API the defi-trader desk ran live on 2026-10-01."""
from __future__ import annotations

from .chain import ChainError, http_json

API = "https://aggregator-api.kyberswap.com/base/api/v1"
HEADERS = {"x-client-id": "argo-fund"}


def quote(token_in: str, token_out: str, amount_in: int, safe: str, slippage_bps: int, get=http_json) -> dict:
    """{router, data, value, amount_out, min_out, amount_in_usd, amount_out_usd}. Raises ChainError when there is no route."""
    route = get(f"{API}/routes?tokenIn={token_in}&tokenOut={token_out}&amountIn={amount_in}", headers=HEADERS)
    if route.get("code") != 0:
        raise ChainError(f"no route: {route.get('message')}")
    build = get(f"{API}/route/build", {"routeSummary": route["data"]["routeSummary"], "sender": safe, "recipient": safe,
                                        "slippageTolerance": slippage_bps, "source": "argo-fund"}, headers=HEADERS)
    if build.get("code") != 0:
        raise ChainError(f"route build failed: {build.get('message')}")
    b = build["data"]
    out = int(b["amountOut"])
    return {"router": b["routerAddress"], "data": b["data"], "value": int(b.get("transactionValue") or 0),
            "amount_out": out, "min_out": out * (10_000 - slippage_bps) // 10_000,
            "amount_in_usd": float(b.get("amountInUsd") or 0), "amount_out_usd": float(b.get("amountOutUsd") or 0)}
