"""
Module B position monitor.

Checks:
1. Delta drift — rebalances if spot/perp deviate beyond tolerance
2. Negative funding — closes position after N consecutive negative periods
3. Margin buffer — alerts if buffer < 30% of margin
4. Funding collection — records funding payments received
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict

import asyncpg
import ccxt.async_support as ccxt

from alerts.telegram import send

logger = logging.getLogger(__name__)

DELTA_TOLERANCE = float(os.environ.get("MODULE_B_DELTA_TOLERANCE", "0.005"))
NEGATIVE_FUNDING_N = int(os.environ.get("MODULE_B_NEGATIVE_FUNDING_N", "3"))
MARGIN_BUFFER_MIN = 0.30  # 30% minimum buffer

# Track consecutive negative funding counts per position
_neg_funding_count: dict[int, int] = defaultdict(int)


async def check_positions(
    spot_ex: ccxt.okx,
    swap_ex: ccxt.okx,
    pool: asyncpg.Pool,
) -> list[int]:
    """
    Returns list of position IDs that should be closed.
    Also rebalances delta drift inline.
    """
    close_ids: list[int] = []

    rows = await pool.fetch(
        "SELECT * FROM module_b_positions WHERE status = 'open'"
    )
    if not rows:
        return []

    for row in rows:
        pos_id = row["id"]
        pair = row["pair"]
        base = pair.split("/")[0]
        spot_symbol = f"{base}/USDC"
        swap_symbol = f"{base}/USDC:USDC"

        try:
            # ── Funding rate check ─────────────────────────────────────────
            funding_info = await swap_ex.fetch_funding_rate(swap_symbol)
            current_rate = float(funding_info.get("fundingRate", 0))

            # Record funding collected (paid every 8h; OKX settles automatically)
            if current_rate > 0:
                funding_income = current_rate * float(row["perp_size"]) * float(row["entry_perp_px"])
                await pool.execute(
                    "UPDATE module_b_positions SET funding_collected = funding_collected + $1 WHERE id = $2",
                    funding_income,
                    pos_id,
                )
                _neg_funding_count[pos_id] = 0
            else:
                _neg_funding_count[pos_id] += 1
                if _neg_funding_count[pos_id] >= NEGATIVE_FUNDING_N:
                    logger.warning(
                        "Position %d (%s): %d consecutive negative funding periods → closing",
                        pos_id, pair, _neg_funding_count[pos_id],
                    )
                    close_ids.append(pos_id)
                    continue

            # ── Delta drift check ──────────────────────────────────────────
            spot_ticker = await spot_ex.fetch_ticker(spot_symbol)
            swap_ticker = await swap_ex.fetch_ticker(swap_symbol)
            spot_px = float(spot_ticker["last"])
            swap_px = float(swap_ticker["last"])

            spot_value = float(row["spot_size"]) * spot_px
            perp_value = float(row["perp_size"]) * swap_px
            drift = abs(spot_value - perp_value) / max(spot_value, perp_value)

            if drift > DELTA_TOLERANCE:
                logger.info("Delta drift %.4f for %s — rebalancing", drift, pair)
                await _rebalance(spot_ex, swap_ex, pool, row, spot_px, swap_px)

            # ── Margin buffer check ────────────────────────────────────────
            await _check_margin(swap_ex, pool, pos_id, swap_symbol)

        except Exception as exc:
            logger.error("Error monitoring position %d (%s): %s", pos_id, pair, exc)

    return close_ids


async def _rebalance(
    spot_ex: ccxt.okx,
    swap_ex: ccxt.okx,
    pool: asyncpg.Pool,
    row: asyncpg.Record,
    spot_px: float,
    swap_px: float,
) -> None:
    """Adjust the smaller leg to restore delta neutrality."""
    pos_id = row["id"]
    pair = row["pair"]
    base = pair.split("/")[0]
    spot_symbol = f"{base}/USDC"
    swap_symbol = f"{base}/USDC:USDC"

    spot_val = float(row["spot_size"]) * spot_px
    perp_val = float(row["perp_size"]) * swap_px
    diff = spot_val - perp_val  # positive = spot is larger

    adj_size = abs(diff) / max(spot_px, swap_px)

    try:
        if diff > 0:
            # Spot is too large → sell some spot + short more perp
            await spot_ex.create_market_sell_order(spot_symbol, adj_size)
            await swap_ex.create_market_sell_order(swap_symbol, adj_size)
            await pool.execute(
                "UPDATE module_b_positions SET spot_size = spot_size - $1, perp_size = perp_size + $2 WHERE id = $3",
                adj_size, adj_size, pos_id,
            )
        else:
            # Perp is too large → buy some spot + buy back some perp
            await spot_ex.create_market_buy_order(spot_symbol, adj_size)
            await swap_ex.create_market_buy_order(swap_symbol, adj_size)
            await pool.execute(
                "UPDATE module_b_positions SET spot_size = spot_size + $1, perp_size = perp_size - $2 WHERE id = $3",
                adj_size, adj_size, pos_id,
            )
        logger.info("Rebalanced position %d (%s) by %.6f", pos_id, pair, adj_size)
    except Exception as exc:
        logger.error("Rebalance failed for position %d: %s", pos_id, exc)


async def _check_margin(
    swap_ex: ccxt.okx,
    pool: asyncpg.Pool,
    pos_id: int,
    swap_symbol: str,
) -> None:
    try:
        positions = await swap_ex.fetch_positions([swap_symbol])
        for pos in positions:
            margin = float(pos.get("initialMargin") or 0)
            liq_price = float(pos.get("liquidationPrice") or 0)
            mark_price = float(pos.get("markPrice") or 0)

            if liq_price > 0 and mark_price > 0:
                buffer = abs(mark_price - liq_price) / mark_price
                if buffer < MARGIN_BUFFER_MIN:
                    await send(
                        f"⚠️ Low margin buffer on position {pos_id} ({swap_symbol}): "
                        f"buffer={buffer:.1%}, liq={liq_price:.4f}",
                        "warning",
                    )
    except Exception as exc:
        logger.warning("Margin check failed for position %d: %s", pos_id, exc)
