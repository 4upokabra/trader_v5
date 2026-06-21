"""
Stats report for Claude — paste output directly into chat for analysis/improvements.

Usage:
    python scripts/stats_report.py [--days 30]

Outputs a structured text block covering:
  - System health & circuit breaker events
  - Module A: LightGBM signal quality (from Freqtrade trades table)
  - Module A: Claude overlay effectiveness
  - Module B: Funding arb performance
  - Top questions / anomalies to investigate
"""
from __future__ import annotations

import argparse
import os
import textwrap
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "trader_v5"),
        user=os.environ.get("POSTGRES_USER", "trader"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def q(conn, sql: str, params=()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def scalar(conn, sql: str, params=()) -> object:
    rows = q(conn, sql, params)
    if not rows:
        return None
    return list(rows[0].values())[0]


def section(title: str) -> str:
    bar = "─" * (60 - len(title) - 3)
    return f"\n── {title} {bar}\n"


def fmt_pct(v) -> str:
    if v is None:
        return "n/a"
    return f"{float(v):.2%}"


def fmt_usdc(v) -> str:
    if v is None:
        return "n/a"
    return f"${float(v):,.2f}"


def fmt_n(v, decimals=3) -> str:
    if v is None:
        return "n/a"
    return f"{float(v):.{decimals}f}"


def build_report(conn, days: int) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    lines: list[str] = []

    lines.append("=" * 62)
    lines.append(f"  TRADER V5 — STATS REPORT")
    lines.append(f"  Period: last {days} days  |  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 62)

    # ── System health ─────────────────────────────────────────────────────────
    lines.append(section("SYSTEM HEALTH"))

    latest_eq = q(conn, """
        SELECT total_equity, module_a_equity, module_b_equity, drawdown_pct, peak_equity, recorded_at
        FROM equity_snapshots ORDER BY recorded_at DESC LIMIT 1
    """)
    if latest_eq:
        r = latest_eq[0]
        lines.append(f"  Total equity (latest):  {fmt_usdc(r['total_equity'])}")
        lines.append(f"    Module A:             {fmt_usdc(r['module_a_equity'])}")
        lines.append(f"    Module B:             {fmt_usdc(r['module_b_equity'])}")
        lines.append(f"  Peak equity:            {fmt_usdc(r['peak_equity'])}")
        lines.append(f"  Current drawdown:       {fmt_pct(r['drawdown_pct'])}")
        lines.append(f"  Snapshot at:            {r['recorded_at'].strftime('%Y-%m-%d %H:%M UTC')}")
    else:
        lines.append("  No equity snapshots yet.")

    max_dd = scalar(conn, "SELECT MAX(drawdown_pct) FROM equity_snapshots WHERE recorded_at >= %s", (since,))
    lines.append(f"  Max drawdown (period):  {fmt_pct(max_dd)}")

    cb_events = q(conn, """
        SELECT occurred_at, level, message FROM system_events
        WHERE source = 'circuit_breaker' AND occurred_at >= %s
        ORDER BY occurred_at DESC LIMIT 5
    """, (since,))
    if cb_events:
        lines.append(f"\n  Circuit breaker events ({len(cb_events)} in period):")
        for e in cb_events:
            lines.append(f"    [{e['level'].upper()}] {e['occurred_at'].strftime('%m-%d %H:%M')} — {e['message']}")
    else:
        lines.append("\n  Circuit breaker: no events in period ✓")

    # ── Module A: LightGBM performance ────────────────────────────────────────
    lines.append(section("MODULE A — LightGBM Signal"))

    # Freqtrade writes to 'trades' table. Try both is_open=false and closed_at.
    try:
        trades = q(conn, """
            SELECT
                COUNT(*)                                    AS total_trades,
                COUNT(*) FILTER (WHERE close_profit > 0)   AS winners,
                AVG(close_profit)                           AS avg_profit_pct,
                SUM(close_profit_abs)                       AS total_pnl_usdc,
                MAX(close_profit)                           AS best_trade,
                MIN(close_profit)                           AS worst_trade,
                AVG(EXTRACT(EPOCH FROM (close_date - open_date))/3600) AS avg_hold_h
            FROM trades
            WHERE is_open = false AND close_date >= %s
        """, (since,))
        if trades and trades[0]['total_trades']:
            r = trades[0]
            total = int(r['total_trades'])
            winners = int(r['winners'])
            win_rate = winners / total if total else 0
            lines.append(f"  Closed trades:          {total}")
            lines.append(f"  Win rate:               {fmt_pct(win_rate)}")
            lines.append(f"  Avg profit/trade:       {fmt_pct(r['avg_profit_pct'])}")
            lines.append(f"  Total P&L:              {fmt_usdc(r['total_pnl_usdc'])}")
            lines.append(f"  Best / Worst trade:     {fmt_pct(r['best_trade'])} / {fmt_pct(r['worst_trade'])}")
            lines.append(f"  Avg hold time:          {fmt_n(r['avg_hold_h'], 1)}h")

            # Profit factor
            pf = q(conn, """
                SELECT
                    ABS(SUM(close_profit_abs) FILTER (WHERE close_profit > 0)) /
                    NULLIF(ABS(SUM(close_profit_abs) FILTER (WHERE close_profit <= 0)), 0) AS profit_factor
                FROM trades WHERE is_open = false AND close_date >= %s
            """, (since,))
            if pf and pf[0]['profit_factor']:
                lines.append(f"  Profit factor:          {fmt_n(pf[0]['profit_factor'], 2)}")

            # Per-pair breakdown
            by_pair = q(conn, """
                SELECT pair,
                    COUNT(*) AS n,
                    COUNT(*) FILTER (WHERE close_profit > 0)::float / COUNT(*) AS win_rate,
                    SUM(close_profit_abs) AS pnl_usdc
                FROM trades WHERE is_open = false AND close_date >= %s
                GROUP BY pair ORDER BY pnl_usdc DESC
            """, (since,))
            if by_pair:
                lines.append("\n  Per-pair breakdown:")
                lines.append(f"    {'Pair':<22} {'N':>4}  {'WR':>7}  {'P&L':>10}")
                for r in by_pair:
                    lines.append(f"    {r['pair']:<22} {int(r['n']):>4}  {fmt_pct(r['win_rate']):>7}  {fmt_usdc(r['pnl_usdc']):>10}")
        else:
            lines.append("  No closed trades in period.")
    except Exception as e:
        lines.append(f"  Trades table not yet available: {e}")

    # ── Module A: overlay decisions ───────────────────────────────────────────
    lines.append(section("MODULE A — Claude Overlay"))

    overlay = q(conn, """
        SELECT
            action,
            COUNT(*)                AS n,
            AVG(sentiment)          AS avg_sentiment,
            AVG(confidence)         AS avg_confidence
        FROM overlay_log
        WHERE created_at >= %s
        GROUP BY action ORDER BY n DESC
    """, (since,))

    if overlay:
        total_calls = sum(int(r['n']) for r in overlay)
        lines.append(f"  Total overlay calls:    {total_calls}")
        lines.append(f"  {'Action':<12} {'Count':>6}  {'Avg sent':>9}  {'Avg conf':>9}")
        for r in overlay:
            lines.append(f"  {r['action']:<12} {int(r['n']):>6}  {fmt_n(r['avg_sentiment'], 2):>9}  {fmt_n(r['avg_confidence'], 2):>9}")

        # Veto/reduce events detail
        anomalies = q(conn, """
            SELECT pair, created_at, sentiment, anomaly_reason, action
            FROM overlay_log
            WHERE action IN ('veto', 'reduce_50') AND created_at >= %s
            ORDER BY created_at DESC LIMIT 10
        """, (since,))
        if anomalies:
            lines.append(f"\n  Veto / Reduce events (last {len(anomalies)}):")
            for a in anomalies:
                reason = (a['anomaly_reason'] or '')[:60]
                lines.append(
                    f"    {a['created_at'].strftime('%m-%d')} {a['pair']:<20} "
                    f"[{a['action']}] sent={fmt_n(a['sentiment'], 2)}  {reason}"
                )
        else:
            lines.append("\n  No veto/reduce events in period.")

        # Overlay effectiveness: compare P&L of trades that had overlay active vs not
        try:
            eff = q(conn, """
                SELECT
                    d.overlay_applied,
                    COUNT(*)              AS n,
                    AVG(t.close_profit)   AS avg_profit
                FROM module_a_decisions d
                JOIN trades t ON t.id = d.trade_id
                WHERE t.close_date >= %s AND t.is_open = false
                GROUP BY d.overlay_applied
            """, (since,))
            if eff and len(eff) >= 2:
                lines.append("\n  Overlay effectiveness (avg profit):")
                for r in eff:
                    label = "with overlay" if r['overlay_applied'] else "no overlay  "
                    lines.append(f"    {label}  n={r['n']}  avg={fmt_pct(r['avg_profit'])}")
        except Exception:
            pass  # module_a_decisions may not be populated yet
    else:
        lines.append("  No overlay data in period yet.")

    # ── Module B ──────────────────────────────────────────────────────────────
    lines.append(section("MODULE B — Funding Rate Arbitrage"))

    b_open = q(conn, """
        SELECT pair, spot_size, entry_spot_px, funding_collected, opened_at
        FROM module_b_positions WHERE status = 'open'
        ORDER BY opened_at
    """)
    lines.append(f"  Open positions:         {len(b_open)}")
    if b_open:
        for p in b_open:
            notional = float(p['spot_size']) * float(p['entry_spot_px'])
            age_h = (datetime.now(timezone.utc) - p['opened_at'].replace(tzinfo=timezone.utc)).total_seconds() / 3600
            lines.append(
                f"    {p['pair']:<22} notional={fmt_usdc(notional)}  "
                f"funding={fmt_usdc(p['funding_collected'])}  age={age_h:.0f}h"
            )

    b_closed = q(conn, """
        SELECT
            COUNT(*)                            AS n,
            SUM(pnl_usdc)                       AS total_pnl,
            AVG(pnl_usdc)                       AS avg_pnl,
            SUM(funding_collected)              AS total_funding,
            AVG(EXTRACT(EPOCH FROM (closed_at - opened_at))/3600) AS avg_hold_h,
            close_reason,
            COUNT(*) FILTER (WHERE pnl_usdc > 0)::float / NULLIF(COUNT(*), 0) AS win_rate
        FROM module_b_positions
        WHERE status = 'closed' AND closed_at >= %s
        GROUP BY close_reason ORDER BY n DESC
    """, (since,))

    if b_closed:
        total_pnl = sum(float(r['total_pnl'] or 0) for r in b_closed)
        total_n = sum(int(r['n']) for r in b_closed)
        total_funding = sum(float(r['total_funding'] or 0) for r in b_closed)
        lines.append(f"\n  Closed positions:       {total_n}")
        lines.append(f"  Total P&L:              {fmt_usdc(total_pnl)}")
        lines.append(f"  Total funding earned:   {fmt_usdc(total_funding)}")
        lines.append(f"\n  By close reason:")
        lines.append(f"    {'Reason':<35} {'N':>4}  {'WR':>7}  {'Total PnL':>10}  {'Avg hold':>8}")
        for r in b_closed:
            lines.append(
                f"    {str(r['close_reason']):<35} {int(r['n']):>4}  "
                f"{fmt_pct(r['win_rate']):>7}  {fmt_usdc(r['total_pnl']):>10}  "
                f"{fmt_n(r['avg_hold_h'], 0):>6}h"
            )

        by_pair = q(conn, """
            SELECT pair,
                COUNT(*) AS n,
                SUM(pnl_usdc) AS pnl,
                SUM(funding_collected) AS funding,
                AVG(EXTRACT(EPOCH FROM (closed_at - opened_at))/3600) AS avg_h
            FROM module_b_positions
            WHERE status = 'closed' AND closed_at >= %s
            GROUP BY pair ORDER BY pnl DESC
        """, (since,))
        if by_pair:
            lines.append(f"\n  Per-pair (closed):")
            lines.append(f"    {'Pair':<22} {'N':>4}  {'P&L':>10}  {'Funding':>10}  {'Avg hold':>8}")
            for r in by_pair:
                lines.append(
                    f"    {r['pair']:<22} {int(r['n']):>4}  "
                    f"{fmt_usdc(r['pnl']):>10}  {fmt_usdc(r['funding']):>10}  "
                    f"{fmt_n(r['avg_h'], 0):>6}h"
                )
    else:
        lines.append("  No closed positions in period.")

    # Recent funding rate snapshot
    funding_snap = q(conn, """
        SELECT DISTINCT ON (pair) pair, funding_rate, recorded_at
        FROM funding_rates
        ORDER BY pair, recorded_at DESC
    """)
    if funding_snap:
        lines.append(f"\n  Latest funding rates:")
        for r in funding_snap:
            annualised = float(r['funding_rate']) * 3 * 365 * 100
            lines.append(
                f"    {r['pair']:<22} {float(r['funding_rate']):.5f}/8h  "
                f"≈{annualised:.1f}% APY  [{r['recorded_at'].strftime('%m-%d %H:%M')}]"
            )

    # ── Flags for review ──────────────────────────────────────────────────────
    lines.append(section("FLAGS FOR REVIEW"))

    flags: list[str] = []

    if max_dd and float(max_dd) > 0.08:
        flags.append(f"⚠ Max drawdown {fmt_pct(max_dd)} is elevated — review stop sizing")

    if overlay:
        veto_count = next((int(r['n']) for r in overlay if r['action'] == 'veto'), 0)
        reduce_count = next((int(r['n']) for r in overlay if r['action'] == 'reduce_50'), 0)
        total_calls = sum(int(r['n']) for r in overlay)
        intervention_rate = (veto_count + reduce_count) / total_calls if total_calls else 0
        if intervention_rate > 0.3:
            flags.append(f"⚠ Overlay intervened {fmt_pct(intervention_rate)} of calls — check if over-sensitive")
        if intervention_rate == 0:
            flags.append("ℹ Overlay: 0 interventions — working as expected or not enough data yet")

    if b_closed:
        neg_funding_closes = next(
            (int(r['n']) for r in b_closed if 'negative_funding' in str(r['close_reason'])), 0
        )
        total_closed = sum(int(r['n']) for r in b_closed)
        if total_closed and neg_funding_closes / total_closed > 0.5:
            flags.append("⚠ >50% of Module B positions closed due to negative funding — consider raising entry threshold")

    if not flags:
        flags.append("✓ No anomalies detected — review thresholds if period is short")

    for f in flags:
        lines.append(f"  {f}")

    lines.append("\n" + "=" * 62)
    lines.append("  END OF REPORT — paste above into Claude chat for analysis")
    lines.append("=" * 62)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate stats report for Claude")
    parser.add_argument("--days", type=int, default=30, help="Lookback period in days (default: 30)")
    args = parser.parse_args()

    try:
        conn = connect()
    except Exception as e:
        print(f"DB connection failed: {e}")
        print("Make sure POSTGRES_* env vars are set (or run via docker compose exec).")
        return

    report = build_report(conn, args.days)
    conn.close()
    print(report)


if __name__ == "__main__":
    main()
