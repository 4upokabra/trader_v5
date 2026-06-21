#!/usr/bin/env bash
# Download historical OHLCV data for backtesting Module A
# Run from project root: bash scripts/download_data.sh

set -euo pipefail

PAIRS="BTC/USDC:USDC ETH/USDC:USDC SOL/USDC:USDC BNB/USDC:USDC AVAX/USDC:USDC"
TIMEFRAMES="1h 4h 1d"
DAYS=1100  # ~3 years

echo "Downloading data for pairs: $PAIRS"
echo "Timeframes: $TIMEFRAMES"
echo "Days: $DAYS"

docker compose run --rm module_a download-data \
  --config /freqtrade/user_data/config.json \
  --exchange okx \
  --pairs $PAIRS \
  --timeframes $TIMEFRAMES \
  --days $DAYS \
  --trading-mode futures

echo "Done. Data stored in module_a/user_data/data/"
