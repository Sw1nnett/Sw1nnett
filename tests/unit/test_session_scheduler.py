"""Unit tests for the session scheduler."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

import pytest

from workers.session.scheduler import SessionScheduler


class TestSessionScheduler:
    @pytest.mark.asyncio
    async def test_no_callbacks_no_errors(self):
        """Scheduler with no callbacks should run without raising."""
        scheduler = SessionScheduler(tick_seconds=1)
        await scheduler._check_triggers()  # should not raise

    @pytest.mark.asyncio
    async def test_trigger_fires_once_per_day(self):
        called = []

        async def callback():
            called.append(True)

        scheduler = SessionScheduler(
            on_session_open=callback,
            tick_seconds=1,
        )

        # Manually set the time to be past session-open
        with patch("workers.session.scheduler.datetime") as mock_dt:
            mock_now = datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc)  # 15:00 UTC > 14:30
            mock_dt.now.return_value = mock_now

            await scheduler._check_triggers()
            await scheduler._check_triggers()  # second call same day → no re-fire

        assert len(called) == 1  # fired exactly once

    @pytest.mark.asyncio
    async def test_eod_flatten_fires(self):
        flatten_calls = []

        async def on_flatten():
            flatten_calls.append(True)

        scheduler = SessionScheduler(on_eod_flatten=on_flatten, tick_seconds=1)

        with patch("workers.session.scheduler.datetime") as mock_dt:
            mock_now = datetime(2024, 1, 15, 21, 0, tzinfo=timezone.utc)  # 21:00 > 20:58
            mock_dt.now.return_value = mock_now

            await scheduler._check_triggers()

        assert len(flatten_calls) == 1

    @pytest.mark.asyncio
    async def test_eod_report_fires_after_flatten(self):
        report_calls = []

        async def on_report():
            report_calls.append(True)

        scheduler = SessionScheduler(on_eod_report=on_report, tick_seconds=1)

        with patch("workers.session.scheduler.datetime") as mock_dt:
            mock_now = datetime(2024, 1, 15, 21, 10, tzinfo=timezone.utc)  # 21:10 > 21:05
            mock_dt.now.return_value = mock_now

            await scheduler._check_triggers()

        assert len(report_calls) == 1

    @pytest.mark.asyncio
    async def test_triggers_do_not_fire_before_time(self):
        open_calls = []

        async def on_open():
            open_calls.append(True)

        scheduler = SessionScheduler(on_session_open=on_open, tick_seconds=1)

        with patch("workers.session.scheduler.datetime") as mock_dt:
            mock_now = datetime(2024, 1, 15, 9, 0, tzinfo=timezone.utc)  # 09:00 < 14:30
            mock_dt.now.return_value = mock_now

            await scheduler._check_triggers()

        assert open_calls == []

    @pytest.mark.asyncio
    async def test_callback_exception_is_caught(self):
        """A failing callback must not kill the scheduler loop."""
        async def bad_callback():
            raise RuntimeError("boom")

        scheduler = SessionScheduler(on_session_open=bad_callback, tick_seconds=1)

        with patch("workers.session.scheduler.datetime") as mock_dt:
            mock_now = datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc)
            mock_dt.now.return_value = mock_now

            # Should not raise
            await scheduler._check_triggers()

    @pytest.mark.asyncio
    async def test_stop_halts_scheduler(self):
        scheduler = SessionScheduler(tick_seconds=1)
        scheduler._running = True
        await scheduler.stop()
        assert not scheduler._running
