"""Market data ingestion worker."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

from packages.brokers.base import BaseBroker
from packages.core.enums import Interval
from packages.core.models import Bar
from packages.data.redis_client import RedisCache
from packages.observability.logging import get_logger
from packages.observability.metrics import data_feed_lag_seconds


log = get_logger(__name__)

# Outlier filter: reject bars where close is >5x or <0.2x the 20-bar avg
_OUTLIER_FACTOR_HIGH = Decimal("5")
_OUTLIER_FACTOR_LOW = Decimal("0.2")


class MarketDataWorker:
    def __init__(
        self,
        broker: BaseBroker,
        cache: RedisCache,
        symbols: list[str],
        interval: Interval = Interval.min_1,
        lookback_days: int = 5,
    ) -> None:
        self._broker = broker
        self._cache = cache
        self._symbols = symbols
        self._interval = interval
        self._lookback_days = lookback_days
        self._bar_callbacks: list[Callable[[Bar], None]] = []
        self._bar_history: dict[str, list[Bar]] = {}
        self._running = False

    def register_bar_callback(self, cb: Callable[[Bar], None]) -> None:
        self._bar_callbacks.append(cb)

    # ------------------------------------------------------------------ #
    # Historical ingestion
    # ------------------------------------------------------------------ #
    async def fetch_history(self) -> dict[str, list[Bar]]:
        """Fetch and validate historical bars for all symbols."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=self._lookback_days)

        for symbol in self._symbols:
            try:
                raw_bars = await self._broker.get_bars(
                    symbol=symbol,
                    interval=self._interval,
                    start=start,
                    end=end,
                    limit=2000,
                )
                valid_bars = self._validate_and_filter(symbol, raw_bars)
                self._bar_history[symbol] = valid_bars
                log.info(
                    "history_fetched",
                    symbol=symbol,
                    bars=len(valid_bars),
                    raw=len(raw_bars),
                )
            except Exception as e:
                log.error("history_fetch_failed", symbol=symbol, error=str(e))
                self._bar_history[symbol] = []

        return self._bar_history

    def _validate_and_filter(self, symbol: str, bars: list[Bar]) -> list[Bar]:
        if not bars:
            return []

        valid: list[Bar] = []
        closes = [b.close for b in bars]

        for i, bar in enumerate(bars):
            # Basic OHLC sanity
            if not self._validate_bar(bar):
                continue

            # Outlier filter using rolling context
            if i >= 5:
                context = closes[max(0, i - 20):i]
                avg = sum(context) / len(context)
                if avg > 0:
                    if bar.close > avg * _OUTLIER_FACTOR_HIGH:
                        log.warning("outlier_high", symbol=symbol, close=float(bar.close))
                        continue
                    if bar.close < avg * _OUTLIER_FACTOR_LOW:
                        log.warning("outlier_low", symbol=symbol, close=float(bar.close))
                        continue

            valid.append(bar)

        # Gap detection
        if len(valid) > 1:
            gaps = 0
            for i in range(1, len(valid)):
                delta = (valid[i].timestamp - valid[i - 1].timestamp).total_seconds()
                expected_seconds = 60  # 1-minute bars
                if delta > expected_seconds * 3:
                    gaps += 1
            if gaps > 0:
                log.warning("gaps_detected", symbol=symbol, gaps=gaps)

        return valid

    def _validate_bar(self, bar: Bar) -> bool:
        if bar.high < bar.low:
            return False
        if bar.close > bar.high or bar.close < bar.low:
            return False
        if bar.open > bar.high or bar.open < bar.low:
            return False
        if bar.volume < 0:
            return False
        if bar.close <= 0:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #
    async def start_streaming(self) -> None:
        self._running = True
        await self._broker.subscribe_bars(
            symbols=self._symbols,
            interval=self._interval,
            callback=self._on_bar,
        )
        # Start lag monitor
        asyncio.create_task(self._monitor_data_lag())
        log.info("streaming_started", symbols=self._symbols)

    def _on_bar(self, bar: Bar) -> None:
        if not self._validate_bar(bar):
            log.warning("invalid_bar_dropped", symbol=bar.symbol)
            return

        # Update freshness
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda: asyncio.create_task(
                self._cache.update_data_timestamp(bar.symbol)
            )
        )

        # Update history cache
        if bar.symbol not in self._bar_history:
            self._bar_history[bar.symbol] = []
        self._bar_history[bar.symbol].append(bar)
        if len(self._bar_history[bar.symbol]) > 1000:
            self._bar_history[bar.symbol] = self._bar_history[bar.symbol][-500:]

        for cb in self._bar_callbacks:
            try:
                cb(bar)
            except Exception as e:
                log.error("bar_callback_error", error=str(e))

    async def _monitor_data_lag(self) -> None:
        while self._running:
            for symbol in self._symbols:
                age = await self._cache.get_data_age_seconds(symbol)
                if age != float("inf"):
                    data_feed_lag_seconds.labels(symbol=symbol).set(age)
            await asyncio.sleep(10)

    async def stop(self) -> None:
        self._running = False

    @property
    def history(self) -> dict[str, list[Bar]]:
        return self._bar_history
