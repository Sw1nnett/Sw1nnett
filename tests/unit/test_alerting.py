"""Unit tests for the alerting system."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.alerting.base import Alert, AlertChannel, AlertLevel
from packages.alerting.router import AlertRouter


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _alert(title="Test Alert", body="body text", level=AlertLevel.info, **kwargs) -> Alert:
    return Alert(title=title, body=body, level=level, **kwargs)


class _FakeChannel(AlertChannel):
    """Records every send call."""

    def __init__(self, succeed: bool = True):
        self.received: list[Alert] = []
        self._succeed = succeed

    async def send(self, alert: Alert) -> bool:
        self.received.append(alert)
        return self._succeed


# ------------------------------------------------------------------ #
# Alert dataclass
# ------------------------------------------------------------------ #

def test_alert_dedup_key_includes_title_and_level():
    a = _alert(title="Kill Switch", level=AlertLevel.critical)
    assert "Kill Switch" in a.dedup_key
    assert "critical" in a.dedup_key


def test_alert_default_timestamp_is_utc():
    a = _alert()
    assert a.timestamp.tzinfo is not None


def test_alert_level_ordering():
    levels = [AlertLevel.info, AlertLevel.warning, AlertLevel.critical]
    order = {AlertLevel.info: 0, AlertLevel.warning: 1, AlertLevel.critical: 2}
    assert order[AlertLevel.critical] > order[AlertLevel.warning] > order[AlertLevel.info]


# ------------------------------------------------------------------ #
# AlertRouter — basic dispatch
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_router_sends_to_registered_channel():
    ch = _FakeChannel()
    router = AlertRouter()
    router.add_channel(ch)
    sent = await router.send(_alert())
    assert sent == 1
    assert len(ch.received) == 1


@pytest.mark.asyncio
async def test_router_sends_to_multiple_channels():
    ch1, ch2 = _FakeChannel(), _FakeChannel()
    router = AlertRouter()
    router.add_channel(ch1)
    router.add_channel(ch2)
    sent = await router.send(_alert())
    assert sent == 2
    assert len(ch1.received) == 1
    assert len(ch2.received) == 1


@pytest.mark.asyncio
async def test_router_failed_channel_not_counted():
    good = _FakeChannel(succeed=True)
    bad = _FakeChannel(succeed=False)
    router = AlertRouter()
    router.add_channel(good)
    router.add_channel(bad)
    sent = await router.send(_alert())
    assert sent == 1


# ------------------------------------------------------------------ #
# AlertRouter — level filtering
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_router_level_filter_blocks_below_min():
    ch = _FakeChannel()
    router = AlertRouter()
    router.add_channel(ch, min_level=AlertLevel.warning)
    sent = await router.send(_alert(level=AlertLevel.info))
    assert sent == 0
    assert len(ch.received) == 0


@pytest.mark.asyncio
async def test_router_level_filter_passes_at_min():
    ch = _FakeChannel()
    router = AlertRouter()
    router.add_channel(ch, min_level=AlertLevel.warning)
    sent = await router.send(_alert(level=AlertLevel.warning))
    assert sent == 1


@pytest.mark.asyncio
async def test_router_level_filter_passes_above_min():
    ch = _FakeChannel()
    router = AlertRouter()
    router.add_channel(ch, min_level=AlertLevel.warning)
    sent = await router.send(_alert(level=AlertLevel.critical))
    assert sent == 1


# ------------------------------------------------------------------ #
# AlertRouter — deduplication
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_router_dedup_suppresses_duplicate_within_window():
    ch = _FakeChannel()
    router = AlertRouter(dedup_window_seconds=300)
    router.add_channel(ch)

    a = _alert(title="Dup Test")
    await router.send(a)
    # Same dedup key, same window
    sent = await router.send(a)
    assert sent == 0
    assert len(ch.received) == 1


@pytest.mark.asyncio
async def test_router_dedup_allows_after_window_expires():
    ch = _FakeChannel()
    router = AlertRouter(dedup_window_seconds=1)
    router.add_channel(ch)

    a = _alert(title="Window Test")
    await router.send(a)

    # Simulate time passing by backdating the last_sent entry
    key = a.dedup_key
    router._last_sent[key] = router._last_sent[key] - timedelta(seconds=2)

    sent = await router.send(a)
    assert sent == 1
    assert len(ch.received) == 2


@pytest.mark.asyncio
async def test_router_dedup_different_titles_not_suppressed():
    ch = _FakeChannel()
    router = AlertRouter(dedup_window_seconds=300)
    router.add_channel(ch)

    await router.send(_alert(title="Alert A"))
    sent = await router.send(_alert(title="Alert B"))
    assert sent == 1
    assert len(ch.received) == 2


# ------------------------------------------------------------------ #
# AlertRouter — convenience methods
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_router_info_helper():
    ch = _FakeChannel()
    router = AlertRouter()
    router.add_channel(ch)
    await router.info("Info Title", "info body")
    assert ch.received[0].level == AlertLevel.info


@pytest.mark.asyncio
async def test_router_warning_helper():
    ch = _FakeChannel()
    router = AlertRouter()
    router.add_channel(ch)
    await router.warning("Warn Title", "warn body")
    assert ch.received[0].level == AlertLevel.warning


@pytest.mark.asyncio
async def test_router_critical_helper():
    ch = _FakeChannel()
    router = AlertRouter()
    router.add_channel(ch)
    await router.critical("Critical", "critical body")
    assert ch.received[0].level == AlertLevel.critical


# ------------------------------------------------------------------ #
# AlertRouter — no channels
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_router_no_channels_returns_zero():
    router = AlertRouter()
    sent = await router.send(_alert())
    assert sent == 0
