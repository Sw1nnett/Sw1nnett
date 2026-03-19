"""Opening range breakout strategy."""
from __future__ import annotations

from datetime import time as dtime
from decimal import Decimal
from typing import Optional

from packages.core.enums import SignalDirection
from packages.core.models import Bar, Signal
from packages.strategies.base import BaseStrategy


class OpeningRangeBreakoutStrategy(BaseStrategy):
    """
    Accumulate the opening range (first N minutes), then trade the breakout.
    Reject if range is too narrow (choppy) or too wide (gap).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._range_high: dict[str, Decimal] = {}
        self._range_low: dict[str, Decimal] = {}
        self._range_set: dict[str, bool] = {}
        self._range_bars_count: dict[str, int] = {}
        self._traded_today: dict[str, bool] = {}

    @property
    def _orb_minutes(self) -> int:
        return int(self.config.params.get("orb_minutes", 15))

    @property
    def _rvol_threshold(self) -> Decimal:
        return Decimal(str(self.config.params.get("rvol_threshold", 1.3)))

    @property
    def _min_range_atr_ratio(self) -> Decimal:
        return Decimal(str(self.config.params.get("min_range_atr_ratio", 0.3)))

    @property
    def _max_range_atr_ratio(self) -> Decimal:
        return Decimal(str(self.config.params.get("max_range_atr_ratio", 3.0)))

    @property
    def _atr_stop_mult(self) -> Decimal:
        return Decimal(str(self.config.params.get("atr_stop_mult", 1.0)))

    async def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._push_bar(bar)
        symbol = bar.symbol

        bar_time = bar.timestamp.time()
        market_open = dtime(9, 30)
        orb_end = dtime(9, 30 + self._orb_minutes)

        # Reset at start of day
        if bar_time == market_open:
            self._range_high[symbol] = bar.high
            self._range_low[symbol] = bar.low
            self._range_set[symbol] = False
            self._range_bars_count[symbol] = 1
            self._traded_today[symbol] = False
            return None

        # Accumulate ORB
        if bar_time < orb_end:
            if symbol in self._range_high:
                self._range_high[symbol] = max(self._range_high[symbol], bar.high)
                self._range_low[symbol] = min(self._range_low[symbol], bar.low)
                self._range_bars_count[symbol] = self._range_bars_count.get(symbol, 0) + 1
            return None

        # Mark range as set on first bar after ORB window
        if not self._range_set.get(symbol):
            self._range_set[symbol] = True

        # Skip if already traded today
        if self._traded_today.get(symbol):
            return None

        if not self._range_set.get(symbol) or symbol not in self._range_high:
            return None

        rng_high = self._range_high[symbol]
        rng_low = self._range_low[symbol]
        rng_size = rng_high - rng_low

        # Validate range against ATR
        atr = self._compute_atr(symbol)
        if not atr or atr <= 0:
            return None

        ratio = rng_size / atr
        if ratio < self._min_range_atr_ratio:
            self._no_trade_signal(symbol, f"range_too_narrow ratio={float(ratio):.2f}")
            return None
        if ratio > self._max_range_atr_ratio:
            self._no_trade_signal(symbol, f"range_too_wide ratio={float(ratio):.2f}")
            return None

        rvol = self._relative_volume(symbol)
        if rvol < self._rvol_threshold:
            self._no_trade_signal(symbol, f"rvol_low={float(rvol):.2f}")
            return None

        # Breakout detection
        if bar.close > rng_high:
            self._traded_today[symbol] = True
            stop = bar.close - atr * self._atr_stop_mult
            target = bar.close + rng_size * Decimal("2")
            return self._emit_signal(
                symbol=symbol,
                direction=SignalDirection.long,
                entry_price=bar.close,
                stop_price=stop,
                target_price=target,
                range_high=float(rng_high),
                range_low=float(rng_low),
                rvol=float(rvol),
            )

        self._no_trade_signal(symbol, "no_breakout")
        return None
