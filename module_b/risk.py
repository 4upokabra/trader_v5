"""
Module B risk management.

Enforces:
- Max capital per pair: 25% of module B allocated capital
- Circuit breaker check before any new position
- Capital allocation calculation
"""
from __future__ import annotations

import logging
import os

import asyncpg

logger = logging.getLogger(__name__)

MAX_PAIR_SHARE = float(os.environ.get("MODULE_B_MAX_PAIR_SHARE", "0.25"))


async def available_capital_for_pair(
    pool: asyncpg.Pool,
    pair: str,
    total_module_b_capital: float,
) -> float:
    """
    Returns USDC amount available to allocate to a new position on this pair.
    Respects max 25% per pair concentration limit.
    """
    # Current open exposure on this pair
    row = await pool.fetchrow(
        """SELECT COALESCE(SUM(spot_size * entry_spot_px), 0) AS exposure
           FROM module_b_positions
           WHERE pair = $1 AND status = 'open'""",
        pair,
    )
    current_exposure = float(row["exposure"]) if row else 0.0
    max_allowed = total_module_b_capital * MAX_PAIR_SHARE
    available = max(0.0, max_allowed - current_exposure)
    return available


async def is_halted(pool: asyncpg.Pool) -> bool:
    """Check system-wide circuit breaker from the shared events table."""
    row = await pool.fetchrow(
        """SELECT details->>'halt' AS halt
           FROM system_events
           WHERE source = 'circuit_breaker' AND level = 'critical'
           ORDER BY occurred_at DESC
           LIMIT 1"""
    )
    if row is None:
        return False
    return row["halt"] == "true"


async def total_open_exposure(pool: asyncpg.Pool) -> float:
    """Total notional value of all open Module B positions."""
    row = await pool.fetchrow(
        """SELECT COALESCE(SUM(spot_size * entry_spot_px), 0) AS total
           FROM module_b_positions WHERE status = 'open'"""
    )
    return float(row["total"]) if row else 0.0
