"""Intraday momentum breakout strategy."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from packages.core.enums import SignalDirection
from packages.core.models import Bar, Signal
from packages.strategies.base import BaseStrategy


class MomentumBreakoutStrategy(BaseStrategy):
    """
    Enter long when price breaks above the N-bar high with elevated relative volume.
    Exit on ATR-based stop or take-profit.
    """

    @property
    def _min_bars(self) -> int:
        return int(self.config.params.get("min_bars", 30))

    @property
    def _breakout_period(self) -> int:
        return int(self.config.params.get("breakout_period", 20))

    @property
    def _rvol_threshold(self) -> Decimal:
        return Decimal(str(self.config.params.get("rvol_threshold", 1.5)))

    @property
    def _atr_stop_mult(self) -> Decimal:
        return Decimal(str(self.config.params.get("atr_stop_mult", 1.5)))

    @property
    def _atr_tp_mult(self) -> Decimal:
        return Decimal(str(self.config.params.get("atr_tp_mult", 3.0)))

    @property
    def _risk_pct(self) -> Decimal:
        return Decimal(str(self.config.params.get("risk_pct", 0.005)))

    async def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._push_bar(bar)
        symbol = bar.symbol

        if not self._has_enough_bars(symbol, self._min_bars):
            self._no_trade_signal(symbol, "insufficient_bars")
            return None

        bars = self._get_bars(symbol)
        period_bars = bars[-self._breakout_period - 1:-1]
        if not period_bars:
            return None

        prev_high = max(b.high for b in period_bars)
        current_close = bar.close

        # Breakout condition
        if current_close <= prev_high:
            self._no_trade_signal(symbol, "no_breakout")
            return None

        # Volume confirmation
        rvol = self._relative_volume(symbol)
        if rvol < self._rvol_threshold:
            self._no_trade_signal(symbol, f"rvol_low={float(rvol):.2f}")
            return None

        # Exhaustion candle guard — reject wide-body bearish bars
        bar_range = bar.high - bar.low
        if bar_range > 0:
            body = abs(bar.close - bar.open)
            if bar.close < bar.open and body / bar_range > Decimal("0.6"):
                self._no_trade_signal(symbol, "exhaustion_candle")
                return None

        atr = self._compute_atr(symbol)
        if not atr or atr <= 0:
            return None

        stop = current_close - atr * self._atr_stop_mult
        target = current_close + atr * self._atr_tp_mult

        return self._emit_signal(
            symbol=symbol,
            direction=SignalDirection.long,
            entry_price=current_close,
            stop_price=stop,
            target_price=target,
            breakout_level=float(prev_high),
            rvol=float(rvol),
            atr=float(atr),
        )
