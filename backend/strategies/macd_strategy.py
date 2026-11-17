"""
MACD (Moving Average Convergence Divergence) Strategy — Sprint 3 (S3-02).

Entry signals:
  - BUY  when MACD line crosses above signal line (bullish crossover)
  - SELL when MACD line crosses below signal line (bearish crossover)
"""

from __future__ import annotations

import inspect
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

# nautilus_trader moved MACD out of indicators.macd (that module no longer exists
# in current releases). Prefer the package export, then trend submodule.
try:
    from nautilus_trader.indicators import MovingAverageConvergenceDivergence
except ImportError:  # pragma: no cover
    try:
        from nautilus_trader.indicators.trend import MovingAverageConvergenceDivergence
    except ImportError:
        from nautilus_trader.indicators.macd import MovingAverageConvergenceDivergence  # type: ignore


def _build_macd(fast_period: int, slow_period: int, signal_period: int):
    """Construct MACD across nautilus_trader API variants."""
    cls = MovingAverageConvergenceDivergence
    params = inspect.signature(cls.__init__).parameters
    kwargs = {}
    if "fast_period" in params:
        kwargs["fast_period"] = fast_period
        kwargs["slow_period"] = slow_period
        if "signal_period" in params:
            kwargs["signal_period"] = signal_period
        return cls(**kwargs)
    # Positional fallback (older bindings)
    try:
        return cls(fast_period, slow_period, signal_period)
    except TypeError:
        return cls(fast_period, slow_period)


class MACDStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str = "EUR/USD.SIM"
    bar_type: str = "EUR/USD.SIM-1-MINUTE-BID-INTERNAL"
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    trade_size: Decimal = Decimal("100000")


class MACDStrategy(Strategy):
    """MACD crossover strategy for Nautilus Trader."""

    def __init__(self, config: MACDStrategyConfig) -> None:
        super().__init__(config)
        self.macd = _build_macd(
            config.fast_period,
            config.slow_period,
            config.signal_period,
        )
        self._signal_period = int(config.signal_period)
        self._signal_ema: float | None = None
        self._prev_macd: float = 0.0
        self._prev_signal: float = 0.0
        self._initialized_prev: bool = False

    def on_start(self) -> None:
        self.bar_type = self.config.bar_type
        self.instrument_id = InstrumentId.from_str(str(self.config.instrument_id))
        self.register_indicator_for_bars(self.bar_type, self.macd)
        self.subscribe_bars(self.bar_type)

    def _signal_value(self, macd_val: float) -> float:
        """Use indicator.signal when present; otherwise EMA of MACD line."""
        if hasattr(self.macd, "signal"):
            try:
                return float(self.macd.signal)
            except Exception:
                pass
        # EMA of MACD line as signal
        k = 2 / (self._signal_period + 1)
        if self._signal_ema is None:
            self._signal_ema = macd_val
        else:
            self._signal_ema = macd_val * k + self._signal_ema * (1 - k)
        return float(self._signal_ema)

    def on_bar(self, bar: Bar) -> None:
        if not self.macd.initialized:
            return

        macd_val = float(self.macd.value)
        signal_val = self._signal_value(macd_val)

        if self._initialized_prev:
            # Bullish crossover: MACD crosses above signal
            if self._prev_macd <= self._prev_signal and macd_val > signal_val:
                if not self.portfolio.is_net_long(self.instrument_id):
                    self._buy()
            # Bearish crossover: MACD crosses below signal
            elif self._prev_macd >= self._prev_signal and macd_val < signal_val:
                if not self.portfolio.is_net_short(self.instrument_id):
                    self._sell()

        self._prev_macd = macd_val
        self._prev_signal = signal_val
        self._initialized_prev = True

    def _buy(self) -> None:
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)

    def _sell(self) -> None:
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)

    def on_stop(self) -> None:
        self.close_all_positions(self.instrument_id)
