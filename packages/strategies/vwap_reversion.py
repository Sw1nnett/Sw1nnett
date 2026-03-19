"""VWAP mean-reversion strategy."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from packages.core.enums import SignalDirection
from packages.core.models import Bar, Signal
from packages.strategies.base import BaseStrategy


class VWAPReversionStrategy(BaseStrategy):
    """
    Enter long when price is below VWAP by more than N standard deviations
    with elevated relative volume (mean-reversion setup).
    """

    @property
    def _min_bars(self) -> int:
        return int(self.config.params.get("min_bars", 30))

    @property
    def _stddev_threshold(self) -> Decimal:
        return Decimal(str(self.config.params.get("stddev_threshold", 1.5)))

    @property
    def _rvol_threshold(self) -> Decimal:
        return Decimal(str(self.config.params.get("rvol_threshold", 1.2)))

    @property
    def _atr_stop_mult(self) -> Decimal:
        return Decimal(str(self.config.params.get("atr_stop_mult", 1.0)))

    @property
    def _vwap_window(self) -> int:
        return int(self.config.params.get("vwap_window", 390))  # full session default

    async def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._push_bar(bar)
        symbol = bar.symbol

        if not self._has_enough_bars(symbol, self._min_bars):
            self._no_trade_signal(symbol, "insufficient_bars")
            return None

        vwap = self._compute_vwap(symbol)
        if vwap is None:
            return None

        stddev = self._vwap_stddev(symbol, vwap)
        if not stddev or stddev <= 0:
            return None

        price = bar.close
        distance_from_vwap = vwap - price
        z_score = distance_from_vwap / stddev

        # Long entry: price significantly below VWAP
        if z_score < self._stddev_threshold:
            self._no_trade_signal(symbol, f"z={float(z_score):.2f} < threshold")
            return None

        rvol = self._relative_volume(symbol)
        if rvol < self._rvol_threshold:
            self._no_trade_signal(symbol, f"rvol_low={float(rvol):.2f}")
            return None

        atr = self._compute_atr(symbol)
        if not atr or atr <= 0:
            return None

        stop = price - atr * self._atr_stop_mult
        target = vwap + stddev  # Revert to VWAP+1σ

        return self._emit_signal(
            symbol=symbol,
            direction=SignalDirection.long,
            entry_price=price,
            stop_price=stop,
            target_price=target,
            vwap=float(vwap),
            z_score=float(z_score),
            rvol=float(rvol),
        )
