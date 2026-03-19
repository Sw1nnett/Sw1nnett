"""Base strategy ABC with common helpers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from decimal import Decimal
from typing import Any, Optional

from packages.core.enums import SignalDirection, StrategyState
from packages.core.models import Bar, Fill, Order, Signal, StrategyConfig
from packages.core.utils import safe_divide
from packages.observability.logging import get_logger


class BaseStrategy(ABC):
    """All strategies inherit from this class."""

    def __init__(self, config: StrategyConfig, max_bars: int = 500) -> None:
        self.config = config
        self.state = StrategyState.idle
        self._bars: dict[str, deque[Bar]] = {}
        self._max_bars = max_bars
        self._signals: list[Signal] = []
        self.log = get_logger(f"strategy.{config.strategy_id}")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def prepare(self, historical_bars: dict[str, list[Bar]]) -> None:
        """Load historical data. Called once at startup."""
        for symbol, bars in historical_bars.items():
            q: deque[Bar] = deque(maxlen=self._max_bars)
            q.extend(bars[-self._max_bars:])
            self._bars[symbol] = q
        self.state = StrategyState.active

    @abstractmethod
    async def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Called on each new bar. Return a Signal or None."""
        ...

    async def on_fill(self, fill: Fill) -> None:
        """Called when an order is filled."""

    async def on_close(self) -> None:
        """Called at end of trading session."""
        self.state = StrategyState.idle

    # ------------------------------------------------------------------ #
    # Bar management
    # ------------------------------------------------------------------ #
    def _push_bar(self, bar: Bar) -> None:
        if bar.symbol not in self._bars:
            self._bars[bar.symbol] = deque(maxlen=self._max_bars)
        self._bars[bar.symbol].append(bar)

    def _get_bars(self, symbol: str) -> list[Bar]:
        return list(self._bars.get(symbol, []))

    def _has_enough_bars(self, symbol: str, n: int) -> bool:
        return len(self._bars.get(symbol, [])) >= n

    # ------------------------------------------------------------------ #
    # Technical indicators
    # ------------------------------------------------------------------ #
    def _compute_atr(self, symbol: str, period: int = 14) -> Optional[Decimal]:
        bars = self._get_bars(symbol)
        if len(bars) < period + 1:
            return None
        trs = []
        for i in range(1, len(bars)):
            hl = bars[i].high - bars[i].low
            hc = abs(bars[i].high - bars[i - 1].close)
            lc = abs(bars[i].low - bars[i - 1].close)
            trs.append(max(hl, hc, lc))
        recent_trs = trs[-period:]
        return sum(recent_trs) / len(recent_trs)

    def _compute_vwap(self, symbol: str, n_bars: Optional[int] = None) -> Optional[Decimal]:
        bars = self._get_bars(symbol)
        if n_bars:
            bars = bars[-n_bars:]
        if not bars:
            return None
        total_vol = sum(b.volume for b in bars)
        if total_vol == 0:
            return None
        weighted = sum(((b.high + b.low + b.close) / 3) * b.volume for b in bars)
        return weighted / total_vol

    def _compute_sma(self, symbol: str, period: int) -> Optional[Decimal]:
        bars = self._get_bars(symbol)
        if len(bars) < period:
            return None
        closes = [b.close for b in bars[-period:]]
        return sum(closes) / len(closes)

    def _compute_ema(self, symbol: str, period: int) -> Optional[Decimal]:
        bars = self._get_bars(symbol)
        if len(bars) < period:
            return None
        closes = [b.close for b in bars]
        k = Decimal("2") / (period + 1)
        ema = closes[0]
        for close in closes[1:]:
            ema = close * k + ema * (1 - k)
        return ema

    def _relative_volume(self, symbol: str, avg_period: int = 20) -> Decimal:
        bars = self._get_bars(symbol)
        if len(bars) < avg_period + 1:
            return Decimal("1")
        avg_vol = sum(b.volume for b in bars[-avg_period - 1:-1]) / avg_period
        return safe_divide(bars[-1].volume, avg_vol, Decimal("1"))

    def _vwap_stddev(self, symbol: str, vwap: Decimal, n_bars: int = 20) -> Optional[Decimal]:
        bars = self._get_bars(symbol)[-n_bars:]
        if len(bars) < 5:
            return None
        prices = [(b.high + b.low + b.close) / 3 for b in bars]
        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        # Integer sqrt approximation via Newton's method for Decimal
        if variance <= 0:
            return Decimal("0")
        x = variance
        y = (x + 1) / 2
        while y < x:
            x = y
            y = (x + variance / x) / 2
        return x

    # ------------------------------------------------------------------ #
    # Position sizing
    # ------------------------------------------------------------------ #
    def _volatility_sizing(
        self,
        capital: Decimal,
        risk_pct: Decimal,
        atr: Decimal,
        max_capital_pct: Decimal = Decimal("0.10"),
    ) -> Decimal:
        """ATR-based position sizing."""
        if atr <= 0:
            return Decimal("0")
        dollar_risk = capital * risk_pct
        raw_qty = dollar_risk / atr
        max_qty_by_capital = (capital * max_capital_pct) / (atr * 10)  # rough price estimate
        return min(raw_qty, max_qty_by_capital).quantize(Decimal("1"))

    # ------------------------------------------------------------------ #
    # Signal helpers
    # ------------------------------------------------------------------ #
    def _emit_signal(
        self,
        symbol: str,
        direction: SignalDirection,
        entry_price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        target_price: Optional[Decimal] = None,
        suggested_qty: Optional[Decimal] = None,
        **metadata: Any,
    ) -> Signal:
        signal = Signal(
            strategy_id=self.config.strategy_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            suggested_qty=suggested_qty,
            metadata=metadata,
        )
        self._signals.append(signal)
        return signal

    def _no_trade_signal(self, symbol: str, reason: str = "no_setup") -> None:
        """Log a no-trade decision without emitting a signal."""
        self.log.debug("no_trade", symbol=symbol, reason=reason)
