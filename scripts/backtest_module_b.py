"""
Walk-forward backtest for Module B (Funding Rate Arbitrage).

Usage:
    python scripts/backtest_module_b.py --pair BTC/USDC:USDC --days 365

Data source: funding_rates table in PostgreSQL.
Simulates: entry when rate > threshold+fees, exit on N consecutive negatives.
Reports: total P&L, Sharpe, max drawdown, number of positions.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg2
import numpy as np


@dataclass
class BacktestConfig:
    pair: str
    days: int = 365
    funding_threshold: float = 0.0005
    fee_round_trip: float = 0.0004
    negative_funding_n: int = 3
    initial_capital: float = 2000.0
    max_pair_share: float = 0.25


@dataclass
class Position:
    open_idx: int
    size_usdc: float
    funding_collected: float = 0.0
    close_idx: int | None = None
    close_reason: str = ""


def run_backtest(cfg: BacktestConfig, funding_data: list[tuple[datetime, float]]) -> dict:
    """Simulate funding arb on historical data."""
    min_rate = cfg.funding_threshold + cfg.fee_round_trip

    capital = cfg.initial_capital
    equity_curve: list[float] = [capital]
    positions: list[Position] = []
    open_pos: Position | None = None
    neg_count = 0

    for i, (ts, rate) in enumerate(funding_data):
        # Update open position
        if open_pos is not None:
            if rate > 0:
                # Funding income (simplified: rate × position size every period)
                income = rate * open_pos.size_usdc
                open_pos.funding_collected += income
                capital += income
                neg_count = 0
            else:
                neg_count += 1
                if neg_count >= cfg.negative_funding_n:
                    open_pos.close_idx = i
                    open_pos.close_reason = f"neg_funding_{neg_count}"
                    positions.append(open_pos)
                    open_pos = None
                    neg_count = 0

        # Try to open new position
        if open_pos is None and rate >= min_rate:
            size = capital * cfg.max_pair_share
            open_pos = Position(open_idx=i, size_usdc=size)

        equity_curve.append(capital)

    # Close any open position at end
    if open_pos is not None:
        open_pos.close_idx = len(funding_data) - 1
        open_pos.close_reason = "end_of_period"
        positions.append(open_pos)

    return _compute_stats(equity_curve, positions, cfg)


def _compute_stats(equity: list[float], positions: list[Position], cfg: BacktestConfig) -> dict:
    returns = np.diff(equity) / np.array(equity[:-1])
    sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(365 * 3)) if np.std(returns) > 0 else 0

    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        peak = max(peak, val)
        dd = (peak - val) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    total_pnl = equity[-1] - equity[0]
    total_pct = total_pnl / equity[0] * 100
    funding_total = sum(p.funding_collected for p in positions)

    return {
        "total_pnl_usdc": round(total_pnl, 4),
        "total_pnl_pct": round(total_pct, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "num_positions": len(positions),
        "total_funding_collected": round(funding_total, 4),
        "final_equity": round(equity[-1], 4),
    }


def fetch_funding_history(cfg: BacktestConfig) -> list[tuple[datetime, float]]:
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        dbname=os.environ.get("POSTGRES_DB", "trader_v5"),
        user=os.environ.get("POSTGRES_USER", "trader"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    cutoff = datetime.utcnow() - timedelta(days=cfg.days)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT recorded_at, funding_rate FROM funding_rates
               WHERE pair = %s AND recorded_at >= %s
               ORDER BY recorded_at""",
            (cfg.pair, cutoff),
        )
        rows = cur.fetchall()
    conn.close()
    return [(r[0], float(r[1])) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Module B funding arb backtest")
    parser.add_argument("--pair", default="BTC/USDC:USDC")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--threshold", type=float, default=0.0005)
    parser.add_argument("--capital", type=float, default=2000.0)
    args = parser.parse_args()

    cfg = BacktestConfig(
        pair=args.pair,
        days=args.days,
        funding_threshold=args.threshold,
        initial_capital=args.capital,
    )

    print(f"\nFetching funding rate history for {cfg.pair} ({cfg.days} days)...")
    data = fetch_funding_history(cfg)
    if len(data) < 10:
        print(f"Not enough data ({len(data)} records). Collect funding rates first.")
        sys.exit(1)

    print(f"Running walk-forward backtest on {len(data)} data points...")
    results = run_backtest(cfg, data)

    print("\n─── Backtest Results ─────────────────────────────────────────")
    for key, val in results.items():
        print(f"  {key:<30} {val}")
    print("──────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
