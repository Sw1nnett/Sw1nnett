"""Slack webhook alert channel."""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Optional

from packages.alerting.base import Alert, AlertChannel, AlertLevel
from packages.observability.logging import get_logger

log = get_logger(__name__)

_LEVEL_EMOJI = {
    AlertLevel.info: ":information_source:",
    AlertLevel.warning: ":warning:",
    AlertLevel.critical: ":rotating_light:",
}

_LEVEL_COLOR = {
    AlertLevel.info: "#36a64f",
    AlertLevel.warning: "#f0a500",
    AlertLevel.critical: "#d00000",
}


class SlackChannel(AlertChannel):
    """Delivers alerts to a Slack webhook URL."""

    def __init__(self, webhook_url: str, timeout_seconds: int = 5) -> None:
        self._webhook_url = webhook_url
        self._timeout = timeout_seconds

    def _build_payload(self, alert: Alert) -> dict:
        emoji = _LEVEL_EMOJI.get(alert.level, ":bell:")
        color = _LEVEL_COLOR.get(alert.level, "#cccccc")
        fields = []
        if alert.symbol:
            fields.append({"title": "Symbol", "value": alert.symbol, "short": True})
        if alert.strategy_id:
            fields.append({"title": "Strategy", "value": alert.strategy_id, "short": True})
        for k, v in alert.tags.items():
            fields.append({"title": k.capitalize(), "value": v, "short": True})

        return {
            "attachments": [
                {
                    "color": color,
                    "title": f"{emoji} {alert.title}",
                    "text": alert.body,
                    "fields": fields,
                    "footer": "TradingBot",
                    "ts": int(alert.timestamp.timestamp()),
                }
            ]
        }

    async def send(self, alert: Alert) -> bool:
        payload = self._build_payload(alert)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                ok = resp.status == 200
                if not ok:
                    log.warning("slack_send_failed", status=resp.status)
                return ok
        except urllib.error.URLError as e:
            log.error("slack_channel_error", error=str(e))
            return False
