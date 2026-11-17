"""
Live paper-trading runner: strategy signals → OKX Demo (or other connected venues).

Polls OKX public candles, evaluates SMA / RSI / MACD, and places spot market
orders via LiveTradingManager. Spot mode is long/flat only (SELL closes LONG).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

OKX_PUBLIC_BASE = "https://www.okx.com"


@dataclass
class LiveRunState:
    strategy_id: str
    exchange: str
    instrument: str
    quantity: float
    bar: str
    interval_sec: int
    strategy_type: str
    config: Dict[str, Any]
    position: str = "flat"  # flat | long
    last_signal: Optional[str] = None
    last_error: Optional[str] = None
    last_bar_ts: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ticks: int = 0


class LiveStrategyRunner:
    """Background asyncio loops keyed by strategy_id."""

    def __init__(self, live_manager: Any) -> None:
        self._live_manager = live_manager
        self._tasks: Dict[str, asyncio.Task] = {}
        self._states: Dict[str, LiveRunState] = {}
        self._lock = asyncio.Lock()

    def get_status(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(strategy_id)
        if not st:
            return None
        return {
            "running": strategy_id in self._tasks and not self._tasks[strategy_id].done(),
            "exchange": st.exchange,
            "instrument": st.instrument,
            "quantity": st.quantity,
            "bar": st.bar,
            "position": st.position,
            "last_signal": st.last_signal,
            "last_error": st.last_error,
            "last_bar_ts": st.last_bar_ts,
            "started_at": st.started_at,
            "ticks": st.ticks,
        }

    def list_running(self) -> List[Dict[str, Any]]:
        return [
            {"strategy_id": sid, **(self.get_status(sid) or {})}
            for sid in list(self._tasks.keys())
        ]

    async def start(
        self,
        *,
        strategy_id: str,
        strategy_type: str,
        config: Dict[str, Any],
        exchange: str = "okx",
        instrument: str,
        quantity: float,
        bar: str = "1m",
        interval_sec: int = 60,
    ) -> Dict[str, Any]:
        async with self._lock:
            if strategy_id in self._tasks and not self._tasks[strategy_id].done():
                return {"success": True, "message": "already running", "status": self.get_status(strategy_id)}

            if not instrument or not str(instrument).strip():
                raise RuntimeError("Live instrument is required (from strategy config).")
            qty = float(quantity)
            if qty <= 0:
                raise RuntimeError("Live quantity must be > 0 (from strategy trade_size).")

            venue_id = "okx" if str(exchange).lower() in ("okx", "okx_demo") else str(exchange).lower()
            if not self._live_manager.is_connected(venue_id):
                await self._live_manager.ensure_connected_from_db()
            if not self._live_manager.is_connected(venue_id):
                raise RuntimeError(
                    f"Exchange '{venue_id}' is not connected. "
                    "Connect OKX Demo in Adapters first."
                )

            state = LiveRunState(
                strategy_id=strategy_id,
                exchange=venue_id,
                instrument=str(instrument).strip(),
                quantity=qty,
                bar=bar,
                interval_sec=max(15, int(interval_sec)),
                strategy_type=strategy_type,
                config=dict(config or {}),
            )
            self._states[strategy_id] = state
            task = asyncio.create_task(
                self._run_loop(state),
                name=f"live-strategy-{strategy_id}",
            )
            self._tasks[strategy_id] = task
            logger.info(
                "Live strategy started id=%s type=%s exchange=%s instrument=%s qty=%s",
                strategy_id,
                strategy_type,
                venue_id,
                instrument,
                quantity,
            )
            return {"success": True, "message": "live paper trading started", "status": self.get_status(strategy_id)}

    async def stop(self, strategy_id: str) -> Dict[str, Any]:
        async with self._lock:
            task = self._tasks.pop(strategy_id, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            st = self._states.get(strategy_id)
            if st:
                st.last_signal = "stopped"
            logger.info("Live strategy stopped id=%s", strategy_id)
            return {"success": True, "message": "stopped", "status": self.get_status(strategy_id)}

    async def _run_loop(self, state: LiveRunState) -> None:
        try:
            while True:
                try:
                    closes, bar_ts = await self._fetch_closes(
                        state.exchange, state.instrument, state.bar, limit=120
                    )
                    state.last_bar_ts = bar_ts
                    state.ticks += 1
                    signal = self._evaluate_signal(state.strategy_type, state.config, closes)
                    state.last_signal = signal
                    state.last_error = None
                    await self._maybe_trade(state, signal)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    state.last_error = str(exc)
                    logger.warning(
                        "Live strategy tick failed id=%s: %s", state.strategy_id, exc
                    )
                await asyncio.sleep(state.interval_sec)
        except asyncio.CancelledError:
            logger.info("Live strategy loop cancelled id=%s", state.strategy_id)
            raise

    async def _maybe_trade(self, state: LiveRunState, signal: Optional[str]) -> None:
        if signal not in ("buy", "sell"):
            return
        # Spot: buy opens/adds long; sell only when long (close)
        if signal == "buy" and state.position == "long":
            return
        if signal == "sell" and state.position != "long":
            return

        side = "BUY" if signal == "buy" else "SELL"
        order = {
            "instrument": state.instrument,
            "side": side,
            "type": "MARKET",
            "quantity": state.quantity,
            "exchange": state.exchange,
            "adapter_id": state.exchange,
        }
        result = await self._live_manager.submit_order(order)
        logger.info(
            "Live strategy order id=%s signal=%s result=%s",
            state.strategy_id,
            signal,
            result,
        )
        if signal == "buy":
            state.position = "long"
        else:
            state.position = "flat"

        # Persist order row for Orders page visibility
        try:
            import database

            await database.create_order(
                instrument=state.instrument,
                side=side,
                order_type="MARKET",
                quantity=state.quantity,
                price=result.get("price") if isinstance(result, dict) else None,
                exchange_order_id=(
                    (result or {}).get("exchange_order_id")
                    or (result or {}).get("order_id")
                ),
                status=str((result or {}).get("status") or "FILLED").upper(),
                filled_qty=float((result or {}).get("filled_qty") or state.quantity),
                exchange=str((result or {}).get("exchange") or state.exchange).upper(),
            )
        except Exception as exc:
            logger.warning("Failed to persist live strategy order: %s", exc)

    @classmethod
    async def _fetch_closes(
        cls, exchange: str, instrument: str, bar: str, limit: int = 100
    ) -> tuple[List[float], Optional[str]]:
        venue = (exchange or "okx").lower()
        if venue in ("binance", "binance_futures"):
            try:
                return await cls._fetch_binance_closes(instrument, bar, limit=limit)
            except Exception as exc:
                logger.warning("Binance candles failed (%s); falling back to OKX public", exc)
        # OKX / Bybit / unknown: use OKX public market data for signals
        return await cls._fetch_okx_closes(instrument, bar, limit=limit)

    @staticmethod
    async def _fetch_okx_closes(
        instrument: str, bar: str, limit: int = 100
    ) -> tuple[List[float], Optional[str]]:
        """Return oldest→newest close prices and newest candle timestamp."""
        inst = instrument.replace("/", "-").upper()
        if "-" not in inst and inst.endswith("USDT"):
            inst = f"{inst[:-4]}-USDT"
        params = {"instId": inst, "bar": bar, "limit": str(limit)}
        url = f"{OKX_PUBLIC_BASE}/api/v5/market/candles"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
        if str(payload.get("code")) != "0":
            raise RuntimeError(f"OKX candles error: {payload.get('msg') or payload}")
        rows = list(payload.get("data") or [])
        # OKX returns newest first
        rows.reverse()
        closes = [float(r[4]) for r in rows if len(r) > 4]
        newest_ts = str(rows[-1][0]) if rows else None
        if len(closes) < 5:
            raise RuntimeError("Not enough candle data yet")
        return closes, newest_ts

    @staticmethod
    async def _fetch_binance_closes(
        instrument: str, bar: str, limit: int = 100
    ) -> tuple[List[float], Optional[str]]:
        """Binance public klines → oldest→newest closes."""
        symbol = instrument.replace("/", "").replace("-", "").upper().split(".")[0]
        interval_map = {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "1H": "1h",
            "1h": "1h",
            "4H": "4h",
            "1D": "1d",
        }
        interval = interval_map.get(bar, "1m")
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        url = "https://api.binance.com/api/v3/klines"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            rows = resp.json()
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("Binance returned no klines")
        # Binance: [open_time, open, high, low, close, ...] oldest first
        closes = [float(r[4]) for r in rows if len(r) > 4]
        newest_ts = str(rows[-1][0]) if rows else None
        if len(closes) < 5:
            raise RuntimeError("Not enough Binance candle data yet")
        return closes, newest_ts

    @staticmethod
    def _sma(values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        window = values[-period:]
        return sum(window) / period

    @staticmethod
    def _ema_series(values: List[float], period: int) -> List[float]:
        if not values:
            return []
        k = 2 / (period + 1)
        out = [values[0]]
        for v in values[1:]:
            out.append(v * k + out[-1] * (1 - k))
        return out

    @classmethod
    def _rsi(cls, values: List[float], period: int) -> Optional[float]:
        if len(values) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(values)):
            diff = values[i] - values[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _evaluate_signal(
        self, strategy_type: str, config: Dict[str, Any], closes: List[float]
    ) -> Optional[str]:
        stype = (strategy_type or "").lower()
        if stype == "sma_crossover":
            fast_p = int(config.get("fast_period") or 10)
            slow_p = int(config.get("slow_period") or 20)
            if len(closes) < slow_p + 2:
                return None
            fast_now = self._sma(closes, fast_p)
            slow_now = self._sma(closes, slow_p)
            fast_prev = self._sma(closes[:-1], fast_p)
            slow_prev = self._sma(closes[:-1], slow_p)
            if None in (fast_now, slow_now, fast_prev, slow_prev):
                return None
            if fast_prev <= slow_prev and fast_now > slow_now:
                return "buy"
            if fast_prev >= slow_prev and fast_now < slow_now:
                return "sell"
            return None

        if stype == "rsi":
            period = int(config.get("rsi_period") or 14)
            oversold = float(config.get("oversold_level") or 30)
            overbought = float(config.get("overbought_level") or 70)
            rsi_now = self._rsi(closes, period)
            rsi_prev = self._rsi(closes[:-1], period)
            if rsi_now is None or rsi_prev is None:
                return None
            if rsi_prev <= oversold < rsi_now:
                return "buy"
            if rsi_prev >= overbought > rsi_now:
                return "sell"
            return None

        if stype == "macd":
            fast_p = int(config.get("fast_period") or 12)
            slow_p = int(config.get("slow_period") or 26)
            signal_p = int(config.get("signal_period") or 9)
            if len(closes) < slow_p + signal_p + 2:
                return None
            ema_fast = self._ema_series(closes, fast_p)
            ema_slow = self._ema_series(closes, slow_p)
            macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
            signal_line = self._ema_series(macd_line, signal_p)
            if len(macd_line) < 2 or len(signal_line) < 2:
                return None
            m0, m1 = macd_line[-2], macd_line[-1]
            s0, s1 = signal_line[-2], signal_line[-1]
            if m0 <= s0 and m1 > s1:
                return "buy"
            if m0 >= s0 and m1 < s1:
                return "sell"
            return None

        return None
