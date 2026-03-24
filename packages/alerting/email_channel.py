"""SMTP email alert channel."""
from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from packages.alerting.base import Alert, AlertChannel, AlertLevel
from packages.observability.logging import get_logger

log = get_logger(__name__)

_LEVEL_SUBJECT_PREFIX = {
    AlertLevel.info: "[INFO]",
    AlertLevel.warning: "[WARNING]",
    AlertLevel.critical: "[CRITICAL]",
}


class EmailChannel(AlertChannel):
    """Delivers alerts via SMTP."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addrs: list[str],
        use_tls: bool = True,
        timeout_seconds: int = 10,
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._username = username
        self._password = password
        self._from = from_addr
        self._to = to_addrs
        self._tls = use_tls
        self._timeout = timeout_seconds

    def _build_message(self, alert: Alert) -> MIMEMultipart:
        prefix = _LEVEL_SUBJECT_PREFIX.get(alert.level, "[ALERT]")
        subject = f"{prefix} {alert.title}"

        lines = [alert.body, ""]
        if alert.symbol:
            lines.append(f"Symbol:   {alert.symbol}")
        if alert.strategy_id:
            lines.append(f"Strategy: {alert.strategy_id}")
        for k, v in alert.tags.items():
            lines.append(f"{k.capitalize()}: {v}")
        lines += ["", f"Time: {alert.timestamp.isoformat()}", "-- TradingBot --"]

        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = ", ".join(self._to)
        msg.attach(MIMEText("\n".join(lines), "plain"))
        return msg

    async def send(self, alert: Alert) -> bool:
        msg = self._build_message(alert)
        try:
            context = ssl.create_default_context()
            if self._tls:
                with smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout, context=context) as server:
                    server.login(self._username, self._password)
                    server.sendmail(self._from, self._to, msg.as_string())
            else:
                with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
                    server.starttls(context=context)
                    server.login(self._username, self._password)
                    server.sendmail(self._from, self._to, msg.as_string())
            return True
        except smtplib.SMTPException as e:
            log.error("email_channel_error", error=str(e))
            return False
        except OSError as e:
            log.error("email_channel_connection_error", error=str(e))
            return False
