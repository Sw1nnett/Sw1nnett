"""Unit tests for ExecutionEngine — signal submission, fill persistence, reconciliation."""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from packages.core.enums import BrokerName, OrderSide, OrderStatus, OrderType, SignalDirection, TradingMode
from packages.core.models import Fill, Order, Signal
from tests.fixtures.factories import make_fill, make_order, make_position


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _make_signal(
    symbol: str = "SPY",
    qty: float = 10.0,
    entry_price: float = 450.0,
    stop_price: float = 445.0,
    strategy_id: str = "test",
) -> Signal:
    return Signal(
        symbol=symbol,
        strategy_id=strategy_id,
        direction=SignalDirection.long,
        entry_price=Decimal(str(entry_price)),
        stop_price=Decimal(str(stop_price)),
        suggested_qty=Decimal(str(qty)),
    )


def _make_engine(trading_mode=TradingMode.paper, db_url=None):
    from workers.execution.engine import ExecutionEngine
    from workers.portfolio.tracker import PortfolioTracker

    mock_broker = AsyncMock()
    mock_broker.__class__.__name__ = "SimulatorAdapter"
    mock_broker.broker_name = BrokerName.simulator

    submitted_order = make_order(status=OrderStatus.pending)
    submitted_order.submitted_at = datetime.now(timezone.utc)
    mock_broker.submit_order.return_value = submitted_order

    mock_risk = MagicMock()
    mock_risk.check_pre_trade = AsyncMock()
    from packages.core.enums import RiskEvent
    mock_result = MagicMock()
    mock_result.passed = True
    mock_result.blocked_reason = None
    mock_result.risk_event = RiskEvent.max_position_size
    mock_risk.check_pre_trade.return_value = mock_result

    portfolio = PortfolioTracker()

    engine = ExecutionEngine(
        broker=mock_broker,
        risk_engine=mock_risk,
        portfolio=portfolio,
        trading_mode=trading_mode,
        database_url=db_url,
    )
    return engine, mock_broker, mock_risk, portfolio


# ------------------------------------------------------------------ #
# submit_signal
# ------------------------------------------------------------------ #

class TestSubmitSignal:
    @pytest.mark.asyncio
    async def test_submit_signal_happy_path(self):
        engine, broker, risk, _ = _make_engine()
        signal = _make_signal()

        order = await engine.submit_signal(signal)

        assert order is not None
        broker.submit_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_signal_zero_qty_returns_none(self):
        engine, broker, _, _ = _make_engine()
        signal = _make_signal(qty=0)

        order = await engine.submit_signal(signal)

        assert order is None
        broker.submit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_submit_signal_blocked_by_risk(self):
        engine, broker, risk, _ = _make_engine()

        from packages.core.enums import RiskEvent
        blocked_result = MagicMock()
        blocked_result.passed = False
        blocked_result.blocked_reason = "daily loss limit hit"
        blocked_result.risk_event = RiskEvent.daily_loss_limit
        risk.check_pre_trade.return_value = blocked_result

        signal = _make_signal()
        order = await engine.submit_signal(signal)

        assert order is None
        broker.submit_order.assert_not_called()
        assert len(engine.blocked_orders) == 1
        assert engine.blocked_orders[0]["reason"] == "daily loss limit hit"

    @pytest.mark.asyncio
    async def test_dry_run_does_not_submit(self):
        engine, broker, _, _ = _make_engine(trading_mode=TradingMode.live_dry_run)
        signal = _make_signal()

        order = await engine.submit_signal(signal)

        assert order is not None
        assert order.status == OrderStatus.canceled
        broker.submit_order.assert_not_called()


# ------------------------------------------------------------------ #
# on_fill_received
# ------------------------------------------------------------------ #

class TestOnFillReceived:
    def test_fill_updates_portfolio(self):
        engine, _, risk, portfolio = _make_engine()
        fill = make_fill(symbol="SPY", side=OrderSide.buy, qty=10.0, price=450.0)

        engine.on_fill_received(fill)

        # Portfolio should have an open position
        assert "SPY" in portfolio.positions
        pos = portfolio.positions["SPY"]
        assert float(pos.qty) == pytest.approx(10.0)

    def test_fill_triggers_risk_update(self):
        engine, _, risk, portfolio = _make_engine()
        fill = make_fill()

        engine.on_fill_received(fill)

        risk.update_positions.assert_called_once()
        risk.update_account_equity.assert_called_once()
        risk.update_daily_pnl.assert_called_once()


# ------------------------------------------------------------------ #
# cancel_order / pending_orders
# ------------------------------------------------------------------ #

class TestOrderManagement:
    @pytest.mark.asyncio
    async def test_pending_orders_tracked(self):
        engine, broker, _, _ = _make_engine()
        signal = _make_signal()

        order = await engine.submit_signal(signal)
        assert order is not None
        # The engine tracks by client_order_id
        assert order.client_order_id in engine.pending_orders

    @pytest.mark.asyncio
    async def test_cancel_order_unknown_id(self):
        engine, broker, _, _ = _make_engine()

        result = await engine.cancel_order("nonexistent-id")
        assert result is False
        broker.cancel_order.assert_not_called()


# ------------------------------------------------------------------ #
# reconcile
# ------------------------------------------------------------------ #

class TestReconcile:
    @pytest.mark.asyncio
    async def test_reconcile_no_mismatch(self):
        engine, broker, _, portfolio = _make_engine()

        # No broker positions, no internal positions → clean
        broker.get_positions.return_value = []
        await engine.reconcile()  # Should not raise

    @pytest.mark.asyncio
    async def test_reconcile_logs_mismatch(self, capsys):
        engine, broker, _, portfolio = _make_engine()

        # Internal has SPY, broker says 0
        from packages.core.enums import OrderSide
        fill = make_fill(symbol="SPY", side=OrderSide.buy, qty=10.0)
        engine.on_fill_received(fill)

        broker.get_positions.return_value = []  # broker thinks no positions

        await engine.reconcile()

        # structlog writes to stdout
        captured = capsys.readouterr()
        assert "reconciliation_mismatch" in captured.out or "SPY" in captured.out
