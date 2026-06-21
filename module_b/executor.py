"""
Module B position executor.
Opens and closes delta-neutral funding arb positions:
  - Spot LONG + Perp SHORT of equal notional
  - Isolated margin on the perp leg
  - USDC-denominated (not USDT)
"""
from __future__ import annotations

import logging

import asyncpg
import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)


async def open_position(
    spot_ex: ccxt.okx,
    swap_ex: ccxt.okx,
    pool: asyncpg.Pool,
    pair: str,
    usdc_amount: float,
) -> int | None:
    """
    Open a delta-neutral position:
      1. Market buy spot
      2. Market short perp of same size
    Returns position ID from DB or None on failure.
    """
    base_symbol = pair.split("/")[0]
    spot_symbol = f"{base_symbol}/USDC"
    swap_symbol = f"{base_symbol}/USDC:USDC"

    try:
        # Get current prices
        spot_ticker = await spot_ex.fetch_ticker(spot_symbol)
        spot_price = float(spot_ticker["last"])
        size = usdc_amount / spot_price

        # Round to exchange precision
        markets = await spot_ex.load_markets()
        precision = markets.get(spot_symbol, {}).get("precision", {}).get("amount", 6)
        size = round(size, precision)

        logger.info("Opening funding arb: %s, size=%.6f @ %.2f USDC", pair, size, spot_price)

        # Leg 1: Spot buy
        spot_order = await spot_ex.create_market_buy_order(spot_symbol, size)
        spot_fill_price = float(spot_order.get("average") or spot_price)

        # Leg 2: Perp short (isolated margin)
        await swap_ex.set_margin_mode("isolated", swap_symbol)
        swap_order = await swap_ex.create_market_sell_order(swap_symbol, size)
        swap_fill_price = float(swap_order.get("average") or spot_price)

        # Record in DB
        pos_id = await pool.fetchval(
            """INSERT INTO module_b_positions
               (pair, spot_size, perp_size, entry_spot_px, entry_perp_px)
               VALUES ($1, $2, $3, $4, $5)
               RETURNING id""",
            pair, size, size, spot_fill_price, swap_fill_price,
        )
        logger.info("Position opened: id=%d %s size=%.6f", pos_id, pair, size)
        return pos_id

    except ccxt.InsufficientFunds as exc:
        logger.error("Insufficient funds to open %s: %s", pair, exc)
    except Exception as exc:
        logger.error("Failed to open position for %s: %s", pair, exc)
    return None


async def close_position(
    spot_ex: ccxt.okx,
    swap_ex: ccxt.okx,
    pool: asyncpg.Pool,
    position_id: int,
    reason: str,
) -> bool:
    """Close both legs of a position and record P&L."""
    row = await pool.fetchrow(
        "SELECT * FROM module_b_positions WHERE id = $1 AND status = 'open'",
        position_id,
    )
    if row is None:
        logger.warning("Position %d not found or already closed", position_id)
        return False

    pair = row["pair"]
    size = float(row["spot_size"])
    base_symbol = pair.split("/")[0]
    spot_symbol = f"{base_symbol}/USDC"
    swap_symbol = f"{base_symbol}/USDC:USDC"

    spot_exit_px = None
    swap_exit_px = None

    try:
        # Leg 1: Sell spot
        spot_order = await spot_ex.create_market_sell_order(spot_symbol, size)
        spot_exit_px = float(spot_order.get("average") or 0)

        # Leg 2: Close perp short (buy to cover)
        swap_order = await swap_ex.create_market_buy_order(swap_symbol, size)
        swap_exit_px = float(swap_order.get("average") or 0)

        # P&L: perp P&L (short profit = entry - exit) + funding collected
        perp_pnl = (float(row["entry_perp_px"]) - swap_exit_px) * size
        spot_pnl = (spot_exit_px - float(row["entry_spot_px"])) * size
        total_pnl = perp_pnl + spot_pnl + float(row["funding_collected"])

        await pool.execute(
            """UPDATE module_b_positions
               SET status = 'closed', closed_at = NOW(),
                   exit_spot_px = $1, exit_perp_px = $2,
                   pnl_usdc = $3, close_reason = $4
               WHERE id = $5""",
            spot_exit_px, swap_exit_px, total_pnl, reason, position_id,
        )
        logger.info(
            "Position %d closed: reason=%s pnl=%.4f USDC", position_id, reason, total_pnl
        )
        return True

    except Exception as exc:
        logger.error("Failed to close position %d: %s", position_id, exc)
        # Partial close: update what we have
        if spot_exit_px or swap_exit_px:
            await pool.execute(
                """UPDATE module_b_positions
                   SET exit_spot_px = $1, exit_perp_px = $2, close_reason = $3
                   WHERE id = $4""",
                spot_exit_px, swap_exit_px, f"partial_close_error: {exc}", position_id,
            )
        return False
