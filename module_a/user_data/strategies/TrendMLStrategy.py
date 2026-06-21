"""
Module A — TrendMLStrategy
LightGBM classifier via FreqAI with Triple Barrier labeling.

Signal logic:
  - Triple Barrier labels (adaptive to market regime)
  - Features: EMA stack, MACD, Supertrend, RSI, ADX, Bollinger Bands, Volume
  - Regime detection: EMA24/EMA96 ratio
  - SHAP-driven feature pruning after first training
  - Claude overlay modifies position size via DB flag (checked on every entry)

Exits: Triple Barrier stop/take handled by the model targets; hard stoploss is a
safety net only and should never trigger in normal operation.
"""
from __future__ import annotations

import json
import logging
import os
from functools import reduce

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame
from technical import qtpylib

from freqtrade.strategy import IStrategy, merge_informative_pair
from freqai.base_models.FreqaiMultiOutputClassifier import FreqaiMultiOutputClassifier

logger = logging.getLogger(__name__)


class TrendMLStrategy(IStrategy):
    """Freqtrade strategy using FreqAI LightGBM with Triple Barrier labeling."""

    INTERFACE_VERSION = 3
    can_short = True
    use_exit_signal = True
    exit_profit_only = False

    # Freqtrade will use the stoploss set in the model's Triple Barrier targets;
    # the stoploss below is a hard safety net.
    stoploss = -0.15
    trailing_stop = False
    minimal_roi = {"0": 100}  # Let model drive exits

    timeframe = "1h"
    process_only_new_candles = True
    startup_candle_count = 200

    # ── FreqAI model ──────────────────────────────────────────────────────────
    freqai_info: dict = {}  # populated from config

    def feature_engineering_expand_all(self, dataframe: DataFrame, period: int, **kwargs) -> DataFrame:
        """
        Called by FreqAI for every period in indicator_periods_candles.
        """
        # EMA stack
        dataframe[f"%-ema_{period}"] = ta.EMA(dataframe, timeperiod=period)

        # RSI vs 50 (not extremes — per ТЗ)
        dataframe[f"%-rsi_{period}"] = ta.RSI(dataframe, timeperiod=period) - 50

        # ATR for normalisation
        dataframe[f"%-atr_{period}"] = ta.ATR(dataframe, timeperiod=period)

        # Bollinger width (regime)
        upper, mid, lower = ta.BBANDS(dataframe["close"], timeperiod=period)
        dataframe[f"%-bb_width_{period}"] = (upper - lower) / mid

        # Volume trend
        dataframe[f"%-volume_roc_{period}"] = (
            dataframe["volume"].pct_change(period).replace([np.inf, -np.inf], 0)
        )

        return dataframe

    def feature_engineering_expand_basic(self, dataframe: DataFrame, **kwargs) -> DataFrame:
        """
        Features computed once (not per period).
        """
        # MACD histogram slope
        macd, signal, hist = ta.MACD(dataframe)
        dataframe["%-macd_hist"] = hist
        dataframe["%-macd_hist_slope"] = hist.diff()

        # ADX (regime strength)
        dataframe["%-adx"] = ta.ADX(dataframe, timeperiod=14)

        # Supertrend (simplified via ATR)
        atr = ta.ATR(dataframe, timeperiod=10)
        dataframe["%-supertrend_bull"] = (
            dataframe["close"] > (dataframe["close"].rolling(10).mean() - 3 * atr)
        ).astype(int)

        # Rate of change
        dataframe["%-roc_12"] = ta.ROC(dataframe, timeperiod=12)
        dataframe["%-roc_48"] = ta.ROC(dataframe, timeperiod=48)

        return dataframe

    def feature_engineering_standard(self, dataframe: DataFrame, **kwargs) -> DataFrame:
        """
        Market regime flag: bull when EMA24 > EMA96.
        """
        ema24 = ta.EMA(dataframe, timeperiod=24)
        ema96 = ta.EMA(dataframe, timeperiod=96)
        dataframe["%-regime_bull"] = (ema24 > ema96).astype(int)
        dataframe["%-ema_ratio"] = ema24 / ema96

        # Day-of-week / hour seasonality
        dataframe["%-hour"] = dataframe["date"].dt.hour
        dataframe["%-dow"] = dataframe["date"].dt.dayofweek

        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, **kwargs) -> DataFrame:
        """
        Triple Barrier labeling adaptive to market regime.

        In bull regime:  TP = 1.2σ, SL = 2.0σ  (trend following)
        In bear regime:  TP = 2.0σ, SL = 1.2σ  (mean reversion / tighter stop)

        Labels: 1 = long won, -1 = short won, 0 = timeout
        """
        # Local sigma = rolling std of returns over label_period_candles
        period = self.freqai_info.get("feature_parameters", {}).get("label_period_candles", 24)
        ret_std = dataframe["close"].pct_change().rolling(period).std()

        # Regime: bull when ema24 > ema96
        ema24 = ta.EMA(dataframe, timeperiod=24)
        ema96 = ta.EMA(dataframe, timeperiod=96)
        bull = ema24 > ema96

        tp_mult = np.where(bull, 1.2, 2.0)
        sl_mult = np.where(bull, 2.0, 1.2)

        tp = dataframe["close"] * (1 + tp_mult * ret_std)
        sl = dataframe["close"] * (1 - sl_mult * ret_std)

        labels = []
        closes = dataframe["close"].values
        highs = dataframe["high"].values
        lows = dataframe["low"].values
        tp_arr = tp.values
        sl_arr = sl.values

        for i in range(len(dataframe)):
            end = min(i + period, len(dataframe) - 1)
            label = 0
            for j in range(i + 1, end + 1):
                if highs[j] >= tp_arr[i]:
                    label = 1
                    break
                if lows[j] <= sl_arr[i]:
                    label = -1
                    break
            labels.append(label)

        dataframe["&-target"] = labels
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self.freqai.start(dataframe, metadata, self)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # FreqAI prediction column
        pred_col = "prediction"
        if pred_col not in dataframe.columns:
            dataframe["enter_long"] = 0
            dataframe["enter_short"] = 0
            return dataframe

        # Overlay: check position size multiplier from overlay_state table
        pair = metadata["pair"]
        size_mult = self._get_overlay_multiplier(pair)

        # Long entry: model predicts 1 with reasonable confidence
        enter_long = (
            (dataframe[pred_col] == 1) &
            (dataframe["do_predict"] == 1) &
            (size_mult > 0)
        )
        # Short entry
        enter_short = (
            (dataframe[pred_col] == -1) &
            (dataframe["do_predict"] == 1) &
            (size_mult > 0)
        )

        dataframe.loc[enter_long, "enter_long"] = 1
        dataframe.loc[enter_short, "enter_short"] = 1

        # Store multiplier so custom_stake_amount can use it
        dataframe["overlay_size_mult"] = size_mult

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit when model flips direction or confidence drops
        pred_col = "prediction"
        if pred_col not in dataframe.columns:
            return dataframe

        dataframe.loc[dataframe[pred_col] == -1, "exit_long"] = 1
        dataframe.loc[dataframe[pred_col] == 1, "exit_short"] = 1

        return dataframe

    def custom_stake_amount(
        self, current_time, current_rate, proposed_stake, min_stake, max_stake,
        leverage, entry_tag, side, **kwargs
    ) -> float:
        """Apply overlay size reduction and 1%-risk position sizing."""
        trade_capital = self.wallets.get_available_stake_amount()
        risk_amount = trade_capital * 0.01  # 1% risk per trade

        # Overlay multiplier: 1.0 = full size, 0.5 = halved, 0.0 = veto (won't reach here)
        pair = kwargs.get("pair", "")
        mult = self._get_overlay_multiplier(pair)
        stake = min(risk_amount * mult, max_stake)
        return max(stake, min_stake)

    # ── Circuit breaker check ─────────────────────────────────────────────────

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force,
                            current_time, entry_tag, side, **kwargs) -> bool:
        if self._is_halted():
            logger.warning("Circuit breaker active — blocking entry for %s", pair)
            return False
        return True

    # ── Internal helpers ──────────────────────────────────────────────────────

    # Path to the JSON file written by the overlay service (shared volume)
    _OVERLAY_STATE_FILE = os.environ.get("OVERLAY_STATE_FILE", "/overlay_state/overlay_state.json")
    _HALT_FLAG_FILE = os.environ.get("HALT_FLAG_FILE", "/overlay_state/halt.flag")

    def _get_overlay_multiplier(self, pair: str) -> float:
        """
        Returns 0.0 (veto), 0.5 (reduced), or 1.0 (full size).
        Reads from a JSON file on the shared volume written by overlay_service.
        Fails open (1.0) if overlay is disabled, file missing, or unreadable.
        """
        if os.environ.get("CLAUDE_OVERLAY_ENABLED", "false").lower() != "true":
            return 1.0
        try:
            with open(self._OVERLAY_STATE_FILE) as f:
                state = json.load(f)
            entry = state.get(pair)
            if entry is None:
                return 1.0
            action = entry.get("action", "pass")
            if action == "veto":
                return 0.0
            if action == "reduce_50":
                return 0.5
            return 1.0
        except Exception as exc:
            logger.warning("Overlay state file unreadable (%s) — using full size", exc)
            return 1.0

    def _is_halted(self) -> bool:
        """Circuit breaker: halt flag is written as a plain file by the shared service."""
        return os.path.exists(self._HALT_FLAG_FILE)
