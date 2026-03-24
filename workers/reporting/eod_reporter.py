"""End-of-day reporting worker.

Collects session statistics from the portfolio tracker and risk engine,
formats a concise daily summary, and dispatches it via the alert router.

Usage (standalone)::

    asyncio.run(run_eod_report(portfolio, risk_engine, router))

Or as a scheduled call from the main trading loop at session close.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from packages.alerting.base import Alert, AlertLevel
from packages.alerting.router import AlertRouter
from packages.observability.logging import get_logger

log = get_logger(__name__)

_LINE = "─" * 44


def _fmt_decimal(v: Decimal, prefix: str = "$", decimals: int = 2) -> str:
    sign = "+" if v > 0 else ""
    return f"{prefix}{sign}{float(v):,.{decimals}f}"


def _fmt_pct(v: Decimal) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{float(v):.2f}%"


class EODReport:
    """Immutable daily trading session summary."""

    def __init__(
        self,
        report_date: date,
        starting_equity: Decimal,
        ending_equity: Decimal,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        gross_volume: Decimal,
        max_drawdown_pct: Decimal,
        risk_events: list[str],
        blocked_orders: int,
        symbols_traded: list[str],
    ) -> None:
        self.report_date = report_date
        self.starting_equity = starting_equity
        self.ending_equity = ending_equity
        self.realized_pnl = realized_pnl
        self.unrealized_pnl = unrealized_pnl
        self.total_trades = total_trades
        self.winning_trades = winning_trades
        self.losing_trades = losing_trades
        self.gross_volume = gross_volume
        self.max_drawdown_pct = max_drawdown_pct
        self.risk_events = risk_events
        self.blocked_orders = blocked_orders
        self.symbols_traded = symbols_traded

    @property
    def net_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def daily_return_pct(self) -> Decimal:
        if self.starting_equity == 0:
            return Decimal("0")
        return (self.ending_equity - self.starting_equity) / self.starting_equity * 100

    @property
    def win_rate_pct(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    def format(self) -> str:
        lines = [
            _LINE,
            f"  EOD REPORT — {self.report_date.isoformat()}",
            _LINE,
            f"  Starting equity : {_fmt_decimal(self.starting_equity)}",
            f"  Ending equity   : {_fmt_decimal(self.ending_equity)}",
            f"  Daily return    : {_fmt_pct(self.daily_return_pct)}",
            f"  Realized PnL    : {_fmt_decimal(self.realized_pnl)}",
            f"  Unrealized PnL  : {_fmt_decimal(self.unrealized_pnl)}",
            _LINE,
            f"  Trades          : {self.total_trades}",
            f"  Winners / Losers: {self.winning_trades} / {self.losing_trades}",
            f"  Win rate        : {self.win_rate_pct:.1f}%",
            f"  Gross volume    : {_fmt_decimal(self.gross_volume)}",
            f"  Max drawdown    : {_fmt_pct(self.max_drawdown_pct)}",
            _LINE,
            f"  Symbols traded  : {', '.join(self.symbols_traded) or 'none'}",
            f"  Blocked orders  : {self.blocked_orders}",
        ]
        if self.risk_events:
            lines.append(f"  Risk events     : {', '.join(self.risk_events)}")
        lines.append(_LINE)
        return "\n".join(lines)

    def alert_level(self) -> AlertLevel:
        """Determine alert severity based on session outcome."""
        if self.daily_return_pct < Decimal("-1.5"):
            return AlertLevel.critical
        if self.daily_return_pct < Decimal("0") or self.risk_events:
            return AlertLevel.warning
        return AlertLevel.info


async def send_eod_report(report: EODReport, router: AlertRouter) -> int:
    """Format and send EOD report via alert router."""
    body = report.format()
    alert = Alert(
        title=f"EOD Report — {report.report_date.isoformat()}",
        body=body,
        level=report.alert_level(),
        tags={
            "return": _fmt_pct(report.daily_return_pct),
            "trades": str(report.total_trades),
        },
    )
    sent = await router.send(alert)
    log.info(
        "eod_report_sent",
        date=report.report_date.isoformat(),
        return_pct=float(report.daily_return_pct),
        channels=sent,
    )
    return sent
