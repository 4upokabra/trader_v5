"""
System-wide circuit breaker.

Polls equity snapshots every 60 s. Fires Telegram alerts at warning threshold
and halts both modules at hard-stop threshold by writing a flag to DB.

Both Module A (Freqtrade) and Module B (custom) check this flag before
placing new orders.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

import asyncpg

from alerts.telegram import send
from config import DB, RiskParams

logger = logging.getLogger(__name__)

HALT_FLAG_KEY = "circuit_breaker_halt"
POLL_INTERVAL = 60  # seconds


async def _get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=DB.host,
        port=DB.port,
        user=DB.user,
        password=DB.password,
        database=DB.name,
        min_size=1,
        max_size=3,
    )


async def _log_event(pool: asyncpg.Pool, level: str, message: str, details: dict | None = None) -> None:
    await pool.execute(
        """INSERT INTO system_events (source, level, message, details)
           VALUES ('circuit_breaker', $1, $2, $3)""",
        level,
        message,
        details,
    )


async def _set_halt(pool: asyncpg.Pool, halt: bool) -> None:
    await pool.execute(
        """INSERT INTO system_events (source, level, message, details)
           VALUES ('circuit_breaker', 'critical', $1, $2)""",
        "HALT engaged — manual review required before restart" if halt else "HALT cleared",
        {"halt": halt},
    )
    # Write to a simple key-value table (reuse system_events as flag store via details)
    # Both modules query: SELECT details->>'halt' FROM system_events
    #   WHERE source='circuit_breaker' ORDER BY occurred_at DESC LIMIT 1


async def is_halted(pool: asyncpg.Pool) -> bool:
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


async def monitor_loop() -> None:
    pool = await _get_pool()
    warning_sent = False
    halt_active = False

    logger.info("Circuit breaker monitor started")
    while True:
        try:
            row = await pool.fetchrow(
                """SELECT total_equity, peak_equity, drawdown_pct
                   FROM equity_snapshots
                   ORDER BY recorded_at DESC
                   LIMIT 1"""
            )
            if row and row["drawdown_pct"] is not None:
                dd = float(row["drawdown_pct"])

                if dd >= RiskParams.circuit_breaker_drawdown and not halt_active:
                    halt_active = True
                    await _set_halt(pool, True)
                    await _log_event(pool, "critical", f"Circuit breaker HALT: drawdown {dd:.1%}")
                    await send(
                        f"🛑 *CIRCUIT BREAKER HALT*\nDrawdown {dd:.1%} ≥ {RiskParams.circuit_breaker_drawdown:.0%}\n"
                        f"All trading stopped. Manual review required.",
                        "critical",
                    )

                elif dd >= RiskParams.circuit_breaker_warning and not warning_sent:
                    warning_sent = True
                    await _log_event(pool, "warning", f"Drawdown warning: {dd:.1%}")
                    await send(
                        f"Drawdown {dd:.1%} approaching circuit breaker ({RiskParams.circuit_breaker_drawdown:.0%})",
                        "warning",
                    )

                elif dd < RiskParams.circuit_breaker_warning:
                    warning_sent = False

        except Exception as exc:
            logger.error("Circuit breaker poll error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(monitor_loop())
