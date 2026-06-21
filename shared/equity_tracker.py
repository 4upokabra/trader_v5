"""
Writes equity snapshots every 5 minutes for circuit breaker & Grafana.
Reads equity from both modules via DB tables (Freqtrade trades + Module B positions).
"""
import asyncio
import logging

import asyncpg

from config import DB

logger = logging.getLogger(__name__)
SNAPSHOT_INTERVAL = 300  # 5 minutes


async def _get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=DB.host, port=DB.port, user=DB.user, password=DB.password, database=DB.name,
        min_size=1, max_size=2,
    )


async def _module_a_equity(pool: asyncpg.Pool) -> float | None:
    """Sum open + closed P&L from Freqtrade trades table."""
    row = await pool.fetchrow(
        """SELECT COALESCE(SUM(close_profit_abs), 0) AS pnl
           FROM trades
           WHERE is_open = false"""
    )
    return float(row["pnl"]) if row else None


async def _module_b_equity(pool: asyncpg.Pool) -> float | None:
    row = await pool.fetchrow(
        """SELECT COALESCE(SUM(funding_collected), 0) + COALESCE(SUM(pnl_usdc), 0) AS equity
           FROM module_b_positions"""
    )
    return float(row["equity"]) if row else None


async def _get_peak(pool: asyncpg.Pool) -> float:
    row = await pool.fetchrow(
        "SELECT COALESCE(MAX(peak_equity), 0) AS peak FROM equity_snapshots"
    )
    return float(row["peak"]) if row and row["peak"] else 0.0


async def snapshot_loop() -> None:
    pool = await _get_pool()
    logger.info("Equity tracker started")
    while True:
        try:
            a = await _module_a_equity(pool) or 0.0
            b = await _module_b_equity(pool) or 0.0
            total = a + b
            peak = await _get_peak(pool)
            if total > peak:
                peak = total
            dd = (peak - total) / peak if peak > 0 else 0.0

            await pool.execute(
                """INSERT INTO equity_snapshots
                   (module_a_equity, module_b_equity, total_equity, drawdown_pct, peak_equity)
                   VALUES ($1, $2, $3, $4, $5)""",
                a, b, total, dd, peak,
            )
        except Exception as exc:
            logger.error("Equity snapshot error: %s", exc)
        await asyncio.sleep(SNAPSHOT_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(snapshot_loop())
