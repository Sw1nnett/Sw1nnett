"""Session scheduler — EOD flatten, EOD report, daily reset.

Runs as a background asyncio task inside TradingBot. Fires three events
timed against US equity market hours (Eastern Time):

  09:30 ET  — Daily reset (PnL counters, kill-switch state)
  15:58 ET  — EOD flatten (close all open positions before market close)
  16:05 ET  — EOD report (send daily summary via AlertRouter)

Times are expressed in UTC assuming EST (UTC-5) without DST correction.
For production use, replace with a proper timezone-aware scheduler.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable, Coroutine, Optional

from packages.observability.logging import get_logger

log = get_logger(__name__)

# ── Market hours in UTC (no DST — use 5h offset for EST) ──────────── #
_OPEN_HOUR_UTC = 14       # 09:30 ET ≈ 14:30 UTC
_OPEN_MIN_UTC = 30
_EOD_FLATTEN_HOUR_UTC = 20  # 15:58 ET ≈ 20:58 UTC
_EOD_FLATTEN_MIN_UTC = 58
_EOD_REPORT_HOUR_UTC = 21   # 16:05 ET ≈ 21:05 UTC
_EOD_REPORT_MIN_UTC = 5


class SessionScheduler:
    """
    Lightweight time-based scheduler for the trading session lifecycle.

    Callbacks are plain async callables (no arguments).
    The scheduler ticks every 30 seconds to check whether a trigger
    should fire; each trigger fires at most once per calendar day.
    """

    def __init__(
        self,
        on_session_open: Optional[Callable[[], Coroutine]] = None,
        on_eod_flatten: Optional[Callable[[], Coroutine]] = None,
        on_eod_report: Optional[Callable[[], Coroutine]] = None,
        tick_seconds: int = 30,
    ) -> None:
        self._on_open = on_session_open
        self._on_flatten = on_eod_flatten
        self._on_report = on_eod_report
        self._tick = tick_seconds
        self._running = False
        self._fired_open: Optional[str] = None      # YYYY-MM-DD last fired
        self._fired_flatten: Optional[str] = None
        self._fired_report: Optional[str] = None

    async def start(self) -> None:
        self._running = True
        log.info("session_scheduler_started")
        while self._running:
            await self._check_triggers()
            await asyncio.sleep(self._tick)

    async def stop(self) -> None:
        self._running = False
        log.info("session_scheduler_stopped")

    async def _check_triggers(self) -> None:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()

        # Session open / daily reset
        if self._fired_open != today:
            if (now.hour > _OPEN_HOUR_UTC or
                    (now.hour == _OPEN_HOUR_UTC and now.minute >= _OPEN_MIN_UTC)):
                self._fired_open = today
                await self._fire("session_open", self._on_open)

        # EOD flatten
        if self._fired_flatten != today:
            if (now.hour > _EOD_FLATTEN_HOUR_UTC or
                    (now.hour == _EOD_FLATTEN_HOUR_UTC and now.minute >= _EOD_FLATTEN_MIN_UTC)):
                self._fired_flatten = today
                await self._fire("eod_flatten", self._on_flatten)

        # EOD report (after flatten)
        if self._fired_report != today:
            if (now.hour > _EOD_REPORT_HOUR_UTC or
                    (now.hour == _EOD_REPORT_HOUR_UTC and now.minute >= _EOD_REPORT_MIN_UTC)):
                self._fired_report = today
                await self._fire("eod_report", self._on_report)

    async def _fire(self, name: str, cb: Optional[Callable[[], Coroutine]]) -> None:
        if cb is None:
            return
        log.info("scheduler_trigger_firing", trigger=name)
        try:
            await cb()
        except Exception as e:
            log.error("scheduler_trigger_failed", trigger=name, error=str(e))
