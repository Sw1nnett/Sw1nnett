"""Unit tests for the EOD reporter."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from packages.alerting.base import AlertLevel
from packages.alerting.router import AlertRouter
from workers.reporting.eod_reporter import EODReport, send_eod_report


def _make_report(
    realized_pnl: float = 500.0,
    starting: float = 100_000.0,
    ending: float = 100_500.0,
    total_trades: int = 5,
    winning: int = 3,
    losing: int = 2,
    risk_events: list[str] | None = None,
) -> EODReport:
    return EODReport(
        report_date=date(2024, 1, 15),
        starting_equity=Decimal(str(starting)),
        ending_equity=Decimal(str(ending)),
        realized_pnl=Decimal(str(realized_pnl)),
        unrealized_pnl=Decimal("0"),
        total_trades=total_trades,
        winning_trades=winning,
        losing_trades=losing,
        gross_volume=Decimal("250000"),
        max_drawdown_pct=Decimal("-0.5"),
        risk_events=risk_events or [],
        blocked_orders=0,
        symbols_traded=["SPY"],
    )


# ------------------------------------------------------------------ #
# EODReport properties
# ------------------------------------------------------------------ #

def test_eod_report_net_pnl():
    r = _make_report(realized_pnl=500.0)
    assert r.net_pnl == Decimal("500")


def test_eod_report_daily_return_pct():
    r = _make_report(starting=100_000.0, ending=101_000.0)
    assert float(r.daily_return_pct) == pytest.approx(1.0)


def test_eod_report_daily_return_zero_starting():
    r = _make_report(starting=0.0, ending=0.0)
    assert r.daily_return_pct == Decimal("0")


def test_eod_report_win_rate_pct():
    r = _make_report(total_trades=4, winning=3, losing=1)
    assert r.win_rate_pct == pytest.approx(75.0)


def test_eod_report_win_rate_no_trades():
    r = _make_report(total_trades=0, winning=0, losing=0)
    assert r.win_rate_pct == 0.0


# ------------------------------------------------------------------ #
# Alert level determination
# ------------------------------------------------------------------ #

def test_eod_report_positive_day_is_info():
    r = _make_report(starting=100_000, ending=101_000)
    assert r.alert_level() == AlertLevel.info


def test_eod_report_small_loss_is_warning():
    r = _make_report(starting=100_000, ending=99_500)
    assert r.alert_level() == AlertLevel.warning


def test_eod_report_large_loss_is_critical():
    r = _make_report(starting=100_000, ending=98_000)
    assert r.alert_level() == AlertLevel.critical


def test_eod_report_risk_events_bump_to_warning():
    r = _make_report(starting=100_000, ending=100_200, risk_events=["daily_loss"])
    assert r.alert_level() == AlertLevel.warning


# ------------------------------------------------------------------ #
# Format output
# ------------------------------------------------------------------ #

def test_eod_report_format_contains_key_sections():
    r = _make_report()
    text = r.format()
    assert "EOD REPORT" in text
    assert "2024-01-15" in text
    assert "Trades" in text
    assert "Win rate" in text
    assert "SPY" in text


def test_eod_report_format_shows_pnl():
    r = _make_report(realized_pnl=1234.56)
    text = r.format()
    assert "1,234.56" in text


# ------------------------------------------------------------------ #
# send_eod_report
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_send_eod_report_dispatches_alert():
    """send_eod_report should call router.send and return channel count."""
    from packages.alerting.base import Alert
    from packages.alerting.router import AlertRouter

    sent_alerts: list[Alert] = []

    class _TrackingChannel:
        async def send(self, alert: Alert) -> bool:
            sent_alerts.append(alert)
            return True

    from packages.alerting.base import AlertChannel
    class TC(AlertChannel):
        async def send(self, alert: Alert) -> bool:
            sent_alerts.append(alert)
            return True

    router = AlertRouter()
    router.add_channel(TC())

    r = _make_report()
    count = await send_eod_report(r, router)
    assert count == 1
    assert len(sent_alerts) == 1
    assert "EOD Report" in sent_alerts[0].title
