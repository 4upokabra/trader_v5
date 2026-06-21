"""
Funding rate scanner for Module B.

Polls OKX every 5-15 min, writes to funding_rates table,
returns pairs where funding exceeds the dynamic threshold.

Threshold is dynamic: must cover round-trip fees on both legs.
  OKX taker fee ~0.02% per side → total ~0.08% per 8h interval
  We require funding_rate > fee_buffer (default 0.0005 = 0.05% / 8h)
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal

import asyncpg
import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)

# USDC perpetual pairs to scan (update after verifying availability on OKX EU)
SCAN_PAIRS = [
    "BTC/USDC:USDC",
    "ETH/USDC:USDC",
    "SOL/USDC:USDC",
    "BNB/USDC:USDC",
    "AVAX/USDC:USDC",
    "LINK/USDC:USDC",
    "OP/USDC:USDC",
    "ARB/USDC:USDC",
]

# Round-trip fee estimate per 8h interval (taker × 2 legs × 2 orders)
FEE_ROUND_TRIP = 0.0004  # 4 × 0.01% (conservative)


async def fetch_funding_rates(exchange: ccxt.okx) -> dict[str, float]:
    """Fetch current funding rates for all scan pairs."""
    rates: dict[str, float] = {}
    for pair in SCAN_PAIRS:
        try:
            info = await exchange.fetch_funding_rate(pair)
            rate = float(info.get("fundingRate", 0))
            rates[pair] = rate
        except Exception as exc:
            logger.warning("Failed to fetch funding rate for %s: %s", pair, exc)
    return rates


async def record_funding_rates(pool: asyncpg.Pool, rates: dict[str, float]) -> None:
    for pair, rate in rates.items():
        try:
            await pool.execute(
                """INSERT INTO funding_rates (pair, funding_rate)
                   VALUES ($1, $2)
                   ON CONFLICT DO NOTHING""",
                pair,
                rate,
            )
        except Exception as exc:
            logger.warning("Failed to record funding rate for %s: %s", pair, exc)


def profitable_pairs(
    rates: dict[str, float],
    threshold: float | None = None,
) -> list[tuple[str, float]]:
    """
    Return pairs with funding rate above threshold (after fee coverage).
    threshold defaults to env var MODULE_B_FUNDING_THRESHOLD.
    """
    if threshold is None:
        threshold = float(os.environ.get("MODULE_B_FUNDING_THRESHOLD", "0.0005"))

    min_rate = threshold + FEE_ROUND_TRIP

    return [
        (pair, rate)
        for pair, rate in rates.items()
        if rate >= min_rate
    ]
