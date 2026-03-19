"""Relative volume momentum strategy."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from packages.core.enums import SignalDirection
from packages.core.models import Bar, Signal
from packages.strategies.base import BaseStrategy


class RelativeVolumeMomentumStrategy(BaseStrategy):
    """
    Trade when relative volume exceeds threshold AND price has upward momentum.
    Guards against exhaustion candles.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._daily_trades: dict[str, int] = {}

    @property
    def _min_bars(self) -> int:
        return int(self.config.params.get("min_bars", 25))

    @property
    def _rvol_threshold(self) -> Decimal:
        return Decimal(str(self.config.params.get("rvol_threshold", 2.0)))

    @property
    def _momentum_bars(self) -> int:
        return int(self.config.params.get("momentum_bars", 3))

    @property
    def _atr_stop_mult(self) -> Decimal:
        return Decimal(str(self.config.params.get("atr_stop_mult", 1.5)))

    @property
    def _atr_tp_mult(self) -> Decimal:
        return Decimal(str(self.config.params.get("atr_tp_mult", 2.5)))

    @property
    def _max_daily_trades(self) -> int:
        return int(self.config.params.get("max_daily_trades", 3))

    async def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._push_bar(bar)
        symbol = bar.symbol

        if not self._has_enough_bars(symbol, self._min_bars):
            self._no_trade_signal(symbol, "insufficient_bars")
            return None

        # Daily trade limit
        trades_today = self._daily_trades.get(symbol, 0)
        if trades_today >= self._max_daily_trades:
            self._no_trade_signal(symbol, f"daily_limit={self._max_daily_trades}")
            return None

        rvol = self._relative_volume(symbol)
        if rvol < self._rvol_threshold:
            self._no_trade_signal(symbol, f"rvol={float(rvol):.2f} < {float(self._rvol_threshold):.2f}")
            return None

        # Momentum: last N bars closing higher
        bars = self._get_bars(symbol)
        momentum_bars = bars[-self._momentum_bars - 1:]
        if len(momentum_bars) < self._momentum_bars + 1:
            return None

        closes = [b.close for b in momentum_bars]
        if not all(closes[i] > closes[i - 1] for i in range(1, len(closes))):
            self._no_trade_signal(symbol, "no_upward_momentum")
            return None

        # Exhaustion candle guard
        current = bars[-1]
        bar_range = current.high - current.low
        if bar_range > 0:
            body = abs(current.close - current.open)
            if current.close < current.open and body / bar_range > Decimal("0.6"):
                self._no_trade_signal(symbol, "exhaustion_candle")
                return None

        atr = self._compute_atr(symbol)
        if not atr or atr <= 0:
            return None

        price = bar.close
        stop = price - atr * self._atr_stop_mult
        target = price + atr * self._atr_tp_mult

        self._daily_trades[symbol] = trades_today + 1
        return self._emit_signal(
            symbol=symbol,
            direction=SignalDirection.long,
            entry_price=price,
            stop_price=stop,
            target_price=target,
            rvol=float(rvol),
            atr=float(atr),
        )
