#!/usr/bin/env bash
# Walk-forward backtest for Module A (LightGBM strategy via Freqtrade)
# Run from project root: bash scripts/run_backtest_a.sh

set -euo pipefail

TIMERANGE="${1:-20220101-20250101}"

echo "Running Module A backtest for timerange: $TIMERANGE"

docker compose run --rm module_a backtesting \
  --config /freqtrade/user_data/config.json \
  --strategy TrendMLStrategy \
  --timerange "$TIMERANGE" \
  --enable-protections \
  --export trades \
  --export-filename /freqtrade/user_data/backtest_results/backtest_$(date +%Y%m%d).json

echo "Backtest complete. Results in module_a/user_data/backtest_results/"
