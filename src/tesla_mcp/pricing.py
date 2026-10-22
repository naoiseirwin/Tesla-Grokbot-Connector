"""Published Fleet API prices, in USD per call.

Authentication calls are not billed. Which category a specific read falls into
is documented per endpoint and is treated as data here unless it is a wake.
"""

from __future__ import annotations

# 500 data calls per $1, 50 wakes per $1, 1000 commands per $1.
PRICE_USD = {
    "data": 1 / 500,
    "wake": 1 / 50,
    "command": 1 / 1000,
}


def price_for(category: str, http_status: int | None) -> float:
    """Responses at HTTP 500 and above are not billed."""
    if http_status is not None and http_status >= 500:
        return 0.0
    return PRICE_USD.get(category, PRICE_USD["data"])


def money(amount: float) -> str:
    """Four decimals, because a data call is $0.002."""
    return f"${amount:.4f}"
