"""
Module B — Funding Rate Arbitrage main loop.

Cycle (every 5-15 min):
1. Scan funding rates for all watched pairs
2. Record to DB
3. For profitable pairs: check risk limits → open position if allowed
4. Monitor existing positions: delta drift, negative funding, margin
5. Close positions that hit exit conditions
"""
from __future__ import annotations

import asyncio
import logging
import os

import asyncpg

import exchange as ex_module
import executor
import monitor
import risk
import scanner
from alerts.telegram import send  # from shared/ via PYTHONPATH

logger = logging.getLogger(__name__)

SCAN_INTERVAL = int(os.environ.get("MODULE_B_SCAN_INTERVAL", "600"))  # 10 min default
# Module B allocated capital (USDC) — set via env or read from DB
MODULE_B_CAPITAL = float(os.environ.get("MODULE_B_CAPITAL_USDC", "2000"))


async def get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "trader"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        database=os.environ.get("POSTGRES_DB", "trader_v5"),
        min_size=2,
        max_size=5,
    )


async def main() -> None:
    logger.info("Module B starting — Funding Rate Arbitrage")
    pool = await get_pool()

    spot_ex = ex_module.create_exchange()
    swap_ex = ex_module.create_swap_exchange()

    await spot_ex.load_markets()
    await swap_ex.load_markets()

    await send("Module B (Funding Arb) started", "info")

    while True:
        try:
            await cycle(spot_ex, swap_ex, pool)
        except Exception as exc:
            logger.error("Main cycle error: %s", exc)
            await send(f"Module B cycle error: {exc}", "warning")

        await asyncio.sleep(SCAN_INTERVAL)


async def cycle(
    spot_ex,
    swap_ex,
    pool: asyncpg.Pool,
) -> None:
    # ── Circuit breaker ────────────────────────────────────────────────────────
    if await risk.is_halted(pool):
        logger.info("Circuit breaker active — skipping cycle")
        return

    # ── Scan funding rates ─────────────────────────────────────────────────────
    rates = await scanner.fetch_funding_rates(swap_ex)
    await scanner.record_funding_rates(pool, rates)

    profitable = scanner.profitable_pairs(rates)
    if profitable:
        logger.info("Profitable pairs: %s", [(p, f"{r:.4%}") for p, r in profitable])

    # ── Monitor existing positions ─────────────────────────────────────────────
    close_ids = await monitor.check_positions(spot_ex, swap_ex, pool)

    for pos_id in close_ids:
        row = await pool.fetchrow(
            "SELECT pair FROM module_b_positions WHERE id = $1", pos_id
        )
        reason = f"negative_funding_{monitor.NEGATIVE_FUNDING_N}_periods"
        await executor.close_position(spot_ex, swap_ex, pool, pos_id, reason)
        await send(f"Closed position {pos_id} ({row['pair']}): {reason}", "info")

    # ── Open new positions for profitable pairs ────────────────────────────────
    for pair, funding_rate in profitable:
        # Check if already have open position on this pair
        existing = await pool.fetchval(
            "SELECT COUNT(*) FROM module_b_positions WHERE pair = $1 AND status = 'open'",
            pair,
        )
        if existing and existing > 0:
            continue

        capital = await risk.available_capital_for_pair(pool, pair, MODULE_B_CAPITAL)
        if capital < 10:  # Min position size 10 USDC
            logger.info("No available capital for %s (%.2f USDC)", pair, capital)
            continue

        pos_id = await executor.open_position(spot_ex, swap_ex, pool, pair, capital)
        if pos_id:
            await send(
                f"Opened funding arb: {pair} rate={funding_rate:.4%} size={capital:.2f} USDC",
                "info",
            )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    asyncio.run(main())
