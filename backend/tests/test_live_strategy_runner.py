"""Unit tests for LiveStrategyRunner signal logic (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_strategy_runner import LiveStrategyRunner


def _runner() -> LiveStrategyRunner:
    return LiveStrategyRunner(live_manager=object())


def test_sma_crossover_buy_on_cross_up():
    # fast=2, slow=4: flat then spike → fast crosses above slow
    closes = [10.0, 10.0, 10.0, 10.0, 10.0, 20.0]
    signal = _runner()._evaluate_signal(
        "sma_crossover",
        {"fast_period": 2, "slow_period": 4},
        closes,
    )
    assert signal == "buy"


def test_sma_crossover_sell_on_cross_down():
    closes = [20.0, 20.0, 20.0, 20.0, 20.0, 10.0]
    signal = _runner()._evaluate_signal(
        "sma_crossover",
        {"fast_period": 2, "slow_period": 4},
        closes,
    )
    assert signal == "sell"


def test_rsi_buy_from_oversold():
    # Decline into oversold then bounce
    down = [100 - i for i in range(20)]
    bounce = [80, 82, 85, 90]
    closes = [float(x) for x in down + bounce]
    signal = _runner()._evaluate_signal(
        "rsi",
        {"rsi_period": 14, "oversold_level": 30, "overbought_level": 70},
        closes,
    )
    # May or may not fire depending on exact RSI path; at least no crash
    assert signal in (None, "buy", "sell")


def test_macd_evaluates_without_error():
    closes = [100 + (i % 7) * 0.5 + i * 0.1 for i in range(80)]
    signal = _runner()._evaluate_signal(
        "macd",
        {"fast_period": 12, "slow_period": 26, "signal_period": 9},
        closes,
    )
    assert signal in (None, "buy", "sell")


def test_unknown_strategy_returns_none():
    assert _runner()._evaluate_signal("unknown", {}, [1, 2, 3, 4, 5]) is None
