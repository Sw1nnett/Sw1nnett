"""Alert router with deduplication and level filtering."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from packages.alerting.base import Alert, AlertChannel, AlertLevel
from packages.observability.logging import get_logger

log = get_logger(__name__)


class AlertRouter:
    """
    Routes alerts to registered channels with deduplication.

    Deduplication: alerts with the same ``dedup_key`` are suppressed
    within ``dedup_window_seconds`` of the last send.

    Channels can be registered with a minimum level — they only receive
    alerts at or above that level (info < warning < critical).
    """

    _LEVEL_ORDER = {AlertLevel.info: 0, AlertLevel.warning: 1, AlertLevel.critical: 2}

    def __init__(self, dedup_window_seconds: int = 300) -> None:
        self._channels: list[tuple[AlertChannel, AlertLevel]] = []
        self._dedup_window = timedelta(seconds=dedup_window_seconds)
        self._last_sent: dict[str, datetime] = {}

    def add_channel(
        self,
        channel: AlertChannel,
        min_level: AlertLevel = AlertLevel.info,
    ) -> None:
        self._channels.append((channel, min_level))

    def _is_duplicate(self, alert: Alert) -> bool:
        key = alert.dedup_key
        last = self._last_sent.get(key)
        if last is None:
            return False
        return alert.timestamp - last < self._dedup_window

    def _meets_level(self, alert: Alert, min_level: AlertLevel) -> bool:
        return self._LEVEL_ORDER[alert.level] >= self._LEVEL_ORDER[min_level]

    async def send(self, alert: Alert) -> int:
        """
        Dispatch an alert to all eligible channels.
        Returns number of channels that successfully delivered.
        """
        if self._is_duplicate(alert):
            log.debug("alert_suppressed_dedup", key=alert.dedup_key)
            return 0

        self._last_sent[alert.dedup_key] = alert.timestamp

        tasks = [
            channel.send(alert)
            for channel, min_level in self._channels
            if self._meets_level(alert, min_level)
        ]

        if not tasks:
            return 0

        results = await asyncio.gather(*tasks, return_exceptions=True)
        successes = sum(1 for r in results if r is True)

        log.info(
            "alert_dispatched",
            title=alert.title,
            level=alert.level.value,
            channels_ok=successes,
            channels_total=len(tasks),
        )
        return successes

    async def info(self, title: str, body: str, **kwargs) -> int:
        return await self.send(Alert(title=title, body=body, level=AlertLevel.info, **kwargs))

    async def warning(self, title: str, body: str, **kwargs) -> int:
        return await self.send(Alert(title=title, body=body, level=AlertLevel.warning, **kwargs))

    async def critical(self, title: str, body: str, **kwargs) -> int:
        return await self.send(Alert(title=title, body=body, level=AlertLevel.critical, **kwargs))
