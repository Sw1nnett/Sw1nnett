"""Unit tests for database repository classes.

Uses unittest.mock to avoid needing a real database or SQLite dialect
compatibility issues (JSONB isn't supported by SQLite).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from packages.data.repositories import (
    AuditRepository,
    DailyReportRepository,
    FillRepository,
    OrderRepository,
    PositionRepository,
    RiskEventRepository,
)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _mock_session():
    """Return a mock AsyncSession with an execute method."""
    session = MagicMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.get = AsyncMock(return_value=None)

    # Mock for session.execute(...)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)

    return session, result


# ------------------------------------------------------------------ #
# OrderRepository
# ------------------------------------------------------------------ #

class TestOrderRepository:
    @pytest.mark.asyncio
    async def test_save_calls_add(self):
        session, _ = _mock_session()
        repo = OrderRepository(session)

        from packages.data.orm_models import OrderORM
        orm = MagicMock(spec=OrderORM)
        await repo.save(orm)

        session.add.assert_called_once_with(orm)

    @pytest.mark.asyncio
    async def test_get_by_client_id_not_found(self):
        session, result = _mock_session()
        result.scalar_one_or_none.return_value = None
        repo = OrderRepository(session)

        found = await repo.get_by_client_id("nonexistent")
        assert found is None

    @pytest.mark.asyncio
    async def test_get_by_client_id_found(self):
        session, result = _mock_session()
        mock_order = MagicMock()
        mock_order.client_order_id = "coid-001"
        mock_order.symbol = "SPY"
        result.scalar_one_or_none.return_value = mock_order
        repo = OrderRepository(session)

        found = await repo.get_by_client_id("coid-001")
        assert found is mock_order
        assert found.symbol == "SPY"

    @pytest.mark.asyncio
    async def test_update_status_executes_update(self):
        session, _ = _mock_session()
        # update() returns a result with rowcount
        update_result = MagicMock()
        update_result.rowcount = 1
        session.execute = AsyncMock(return_value=update_result)
        repo = OrderRepository(session)

        count = await repo.update_status(
            "coid-001",
            status="filled",
            filled_qty=Decimal("10"),
            avg_fill_price=Decimal("450.00"),
        )
        assert count == 1
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_today_returns_list(self):
        session, result = _mock_session()
        mock_orders = [MagicMock(), MagicMock()]
        result.scalars.return_value.all.return_value = mock_orders
        repo = OrderRepository(session)

        orders = await repo.list_today()
        assert len(orders) == 2


# ------------------------------------------------------------------ #
# FillRepository
# ------------------------------------------------------------------ #

class TestFillRepository:
    @pytest.mark.asyncio
    async def test_save_calls_add(self):
        session, _ = _mock_session()
        repo = FillRepository(session)

        fill = MagicMock()
        await repo.save(fill)
        session.add.assert_called_once_with(fill)

    @pytest.mark.asyncio
    async def test_list_by_order_returns_list(self):
        session, result = _mock_session()
        fills = [MagicMock(), MagicMock(), MagicMock()]
        result.scalars.return_value.all.return_value = fills
        repo = FillRepository(session)

        found = await repo.list_by_order("order-id-abc")
        assert len(found) == 3

    @pytest.mark.asyncio
    async def test_list_today_empty(self):
        session, result = _mock_session()
        result.scalars.return_value.all.return_value = []
        repo = FillRepository(session)

        fills = await repo.list_today()
        assert fills == []

    @pytest.mark.asyncio
    async def test_gross_pnl_today_empty(self):
        session, result = _mock_session()
        result.scalars.return_value.all.return_value = []
        repo = FillRepository(session)

        pnl = await repo.gross_pnl_today()
        assert pnl == Decimal("0")

    @pytest.mark.asyncio
    async def test_gross_pnl_calculates_correctly(self):
        """Sell fills contribute positively, buy fills negatively."""
        session, result = _mock_session()

        buy_fill = MagicMock()
        buy_fill.side = "buy"
        buy_fill.qty = Decimal("10")
        buy_fill.price = Decimal("100")

        sell_fill = MagicMock()
        sell_fill.side = "sell"
        sell_fill.qty = Decimal("10")
        sell_fill.price = Decimal("110")

        result.scalars.return_value.all.return_value = [buy_fill, sell_fill]
        repo = FillRepository(session)

        pnl = await repo.gross_pnl_today()
        # sell: +10*110 = +1100, buy: -10*100 = -1000 → net +100
        assert pnl == Decimal("100")


# ------------------------------------------------------------------ #
# PositionRepository
# ------------------------------------------------------------------ #

class TestPositionRepository:
    @pytest.mark.asyncio
    async def test_upsert_creates_new_when_missing(self):
        session, result = _mock_session()
        result.scalar_one_or_none.return_value = None  # no existing position
        repo = PositionRepository(session)

        pos = await repo.upsert("SPY", Decimal("10"), Decimal("450.00"), "strat-1")
        session.add.assert_called_once()
        assert pos.symbol == "SPY"
        assert pos.qty == Decimal("10")

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self):
        session, result = _mock_session()
        existing = MagicMock()
        existing.symbol = "SPY"
        existing.qty = Decimal("5")
        existing.avg_entry_price = Decimal("440.00")
        result.scalar_one_or_none.return_value = existing
        repo = PositionRepository(session)

        pos = await repo.upsert("SPY", Decimal("15"), Decimal("445.00"))
        # Should mutate the existing object, not add a new one
        session.add.assert_not_called()
        assert existing.qty == Decimal("15")
        assert existing.avg_entry_price == Decimal("445.00")

    @pytest.mark.asyncio
    async def test_delete_existing(self):
        session, result = _mock_session()
        existing = MagicMock()
        result.scalar_one_or_none.return_value = existing
        repo = PositionRepository(session)

        count = await repo.delete("AAPL")
        assert count == 1
        session.delete.assert_called_once_with(existing)

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        session, result = _mock_session()
        result.scalar_one_or_none.return_value = None
        repo = PositionRepository(session)

        count = await repo.delete("NONEXISTENT")
        assert count == 0
        session.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_open_returns_positions(self):
        session, result = _mock_session()
        positions = [MagicMock(), MagicMock()]
        result.scalars.return_value.all.return_value = positions
        repo = PositionRepository(session)

        open_pos = await repo.list_open()
        assert len(open_pos) == 2


# ------------------------------------------------------------------ #
# RiskEventRepository
# ------------------------------------------------------------------ #

class TestRiskEventRepository:
    @pytest.mark.asyncio
    async def test_save_creates_orm(self):
        session, _ = _mock_session()
        repo = RiskEventRepository(session)

        event = await repo.save(
            event_type="kill_switch_triggered",
            symbol="SPY",
            strategy_id="strat-1",
            reason="drawdown exceeded",
            details={"pnl": -5000},
        )
        session.add.assert_called_once()
        assert event.event_type == "kill_switch_triggered"
        assert event.symbol == "SPY"

    @pytest.mark.asyncio
    async def test_save_with_no_optional_fields(self):
        session, _ = _mock_session()
        repo = RiskEventRepository(session)

        event = await repo.save(event_type="stale_data")
        assert event.event_type == "stale_data"
        assert event.symbol is None
        assert event.details == {}

    @pytest.mark.asyncio
    async def test_list_today_returns_events(self):
        session, result = _mock_session()
        mock_events = [MagicMock(), MagicMock()]
        result.scalars.return_value.all.return_value = mock_events
        repo = RiskEventRepository(session)

        events = await repo.list_today()
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_count_today_by_type_empty(self):
        session, result = _mock_session()
        result.scalars.return_value.all.return_value = []
        repo = RiskEventRepository(session)

        counts = await repo.count_today_by_type()
        assert counts == {}


# ------------------------------------------------------------------ #
# DailyReportRepository
# ------------------------------------------------------------------ #

class TestDailyReportRepository:
    @pytest.mark.asyncio
    async def test_upsert_creates_new(self):
        session, result = _mock_session()
        result.scalar_one_or_none.return_value = None
        repo = DailyReportRepository(session)

        report = await repo.upsert(
            report_date=date.today(),
            strategy_id="strat-1",
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
            gross_pnl=Decimal("500"),
            net_pnl=Decimal("475"),
            total_commission=Decimal("25"),
            blocked_trades=2,
        )
        session.add.assert_called_once()
        assert report.total_trades == 10
        assert report.net_pnl == Decimal("475")

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self):
        session, result = _mock_session()
        existing = MagicMock()
        existing.total_trades = 5
        result.scalar_one_or_none.return_value = existing
        repo = DailyReportRepository(session)

        await repo.upsert(
            report_date=date.today(),
            strategy_id="strat-1",
            total_trades=12,
            winning_trades=7,
            losing_trades=5,
            gross_pnl=Decimal("600"),
            net_pnl=Decimal("570"),
            total_commission=Decimal("30"),
            blocked_trades=3,
        )
        session.add.assert_not_called()
        assert existing.total_trades == 12

    @pytest.mark.asyncio
    async def test_get_by_date_not_found(self):
        session, result = _mock_session()
        result.scalar_one_or_none.return_value = None
        repo = DailyReportRepository(session)

        found = await repo.get_by_date(date(2000, 1, 1), "nonexistent")
        assert found is None


# ------------------------------------------------------------------ #
# AuditRepository
# ------------------------------------------------------------------ #

class TestAuditRepository:
    @pytest.mark.asyncio
    async def test_log_creates_audit_event(self):
        session, _ = _mock_session()
        repo = AuditRepository(session)

        event = await repo.log(
            action="kill_switch_activated",
            actor="admin",
            details={"reason": "drawdown exceeded"},
        )
        session.add.assert_called_once()
        assert event.action == "kill_switch_activated"
        assert event.actor == "admin"
        assert event.details["reason"] == "drawdown exceeded"

    @pytest.mark.asyncio
    async def test_log_defaults(self):
        session, _ = _mock_session()
        repo = AuditRepository(session)

        event = await repo.log(action="flatten_all")
        assert event.actor is None
        assert event.details == {}
