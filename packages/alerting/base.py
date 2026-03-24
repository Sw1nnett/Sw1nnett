"""Base types for the alerting system."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class AlertLevel(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


@dataclass
class Alert:
    title: str
    body: str
    level: AlertLevel = AlertLevel.info
    symbol: Optional[str] = None
    strategy_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """Key used to suppress duplicate alerts within a window."""
        return f"{self.title}:{self.level}"


class AlertChannel:
    """Base class for alert delivery channels."""

    async def send(self, alert: Alert) -> bool:
        """Send an alert. Returns True on success."""
        raise NotImplementedError
