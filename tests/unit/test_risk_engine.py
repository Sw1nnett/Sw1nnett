"""Tests for the pre-trade risk engine."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.core.enums import OrderSide, OrderType, RiskEvent, SignalDirection
from packages.core.models import Order, Position, RiskCheckResult, Signal
from tests.fixtures.factories import make_order, make_position
from workers.risk.engine import RiskEngine


class MockCache:
    def __init__(self):
        self._kill_switch = False
        self._kill_reason = None
        self._daily_loss = 0.0
        self._daily_high = 0.0
        self._data_ages = {}
        self._order_ids = set()
        self._wash = set()
        self._flip = set()

    async def is_kill_switch_active(self): return self._kill_switch
    async def get_kill_switch_reason(self): return self._kill_reason
    async def activate_kill_switch(self, reason="manual"): self._kill_switch = True; self._kill_reason = reason
    async def deactivate_kill_switch(self): self._kill_switch = False; self._kill_reason = None
    async def get_daily_loss(self): return self._daily_loss
    async def get_daily_high_equity(self): return self._daily_high
    async def update_daily_high_equity(self, v): self._daily_high = max(self._daily_high, v)
    async def get_data_age_seconds(self, sym): return self._data_ages.get(sym, 0.0)
    async def try_reserve_order(self, coid, ttl=86400):
        if coid in self._order_ids: return False
        self._order_ids.add(coid); return True
    async def has_wash_cooldown(self, sym): return sym in self._wash
    async def has_flip_cooldown(self, sym): return sym in self._flip
    async def set_wash_cooldown(self, sym, ttl): self._wash.add(sym)
    async def set_flip_cooldown(self, sym, ttl): self._flip.add(sym)


def make_signal(symbol="SPY", entry_price=450.0, stop_price=447.0, qty=10.0):
    return Signal(
        strategy_id="test",
        symbol=symbol,
        direction=SignalDirection.long,
        entry_price=Decimal(str(entry_price)),
        stop_price=Decimal(str(stop_price)),
        suggested_qty=Decimal(str(qty)),
    )


@pytest.fixture
def cache():
    return MockCache()


@pytest.fixture
def engine(cache):
    e = RiskEngine(cache, account_equity=Decimal("100000"))
    # Patch session cutoff to always pass so tests don't depend on time of day
    async def _no_session_cutoff(signal, order):
        return RiskCheckResult.ok()
    e._check_session_cutoff = _no_session_cutoff
    return e


@pytest.mark.asyncio
async def test_all_checks_pass(engine, cache):
    cache._data_ages["SPY"] = 5.0  # fresh data
    signal = make_signal()
    order = make_order(symbol="SPY", qty=10.0)
    result = await engine.check_pre_trade(signal, order)
    assert result.passed


@pytest.mark.asyncio
async def test_kill_switch_blocks(engine, cache):
    await cache.activate_kill_switch("test")
    signal = make_signal()
    order = make_order()
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.kill_switch


@pytest.mark.asyncio
async def test_daily_loss_blocks(engine, cache):
    engine.update_daily_pnl(Decimal("-2500"))  # 2.5% of 100k equity → >2% limit
    signal = make_signal()
    order = make_order()
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.daily_loss_limit


@pytest.mark.asyncio
async def test_daily_drawdown_blocks(engine, cache):
    cache._daily_high = 110000.0  # High was 110k
    engine.update_account_equity(Decimal("100000"))  # Current is 100k → 9% drawdown > 3% limit
    signal = make_signal()
    order = make_order()
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.daily_drawdown


@pytest.mark.asyncio
async def test_stale_data_blocks(engine, cache):
    cache._data_ages["SPY"] = 999.0  # Very stale
    signal = make_signal()
    order = make_order()
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.stale_data


@pytest.mark.asyncio
async def test_duplicate_order_blocks(engine, cache):
    signal = make_signal()
    order = make_order()
    order.client_order_id = "duplicate-id"
    cache._order_ids.add("duplicate-id")
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.duplicate_order


@pytest.mark.asyncio
async def test_wash_cooldown_blocks(engine, cache):
    cache._wash.add("SPY")
    cache._data_ages["SPY"] = 5.0
    signal = make_signal()
    order = make_order()
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.wash_cooldown


@pytest.mark.asyncio
async def test_flip_cooldown_blocks(engine, cache):
    cache._flip.add("SPY")
    cache._data_ages["SPY"] = 5.0
    signal = make_signal()
    order = make_order()
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.flip_cooldown


@pytest.mark.asyncio
async def test_max_position_size_blocks(engine, cache):
    cache._data_ages["SPY"] = 5.0
    # 10% of 100k = 10k; 200 shares @ 450 = 90k → over limit
    signal = make_signal(entry_price=450.0, qty=200.0)
    order = make_order(qty=200.0)
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.max_position_size


@pytest.mark.asyncio
async def test_max_open_positions_blocks(engine, cache):
    cache._data_ages["SPY"] = 5.0
    engine.update_positions({
        f"SYM{i}": make_position(f"SYM{i}", qty=10.0) for i in range(5)
    })
    signal = make_signal()
    order = make_order()
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.max_open_positions


@pytest.mark.asyncio
async def test_gross_exposure_blocks(engine, cache):
    cache._data_ages["SPY"] = 5.0
    # 95k > 90% of 100k = 90k → gross exposure exceeded
    engine.update_positions({
        "AAPL": make_position("AAPL", qty=211.0, current_price=450.0)
    })
    signal = make_signal()
    order = make_order()
    result = await engine.check_pre_trade(signal, order)
    assert not result.passed
    assert result.risk_event == RiskEvent.gross_exposure


@pytest.mark.asyncio
async def test_trigger_kill_switch(engine, cache):
    await engine.trigger_kill_switch("test_reason", "test_actor")
    assert await cache.is_kill_switch_active()
    assert await cache.get_kill_switch_reason() == "test_reason"


@pytest.mark.asyncio
async def test_reset_kill_switch(engine, cache):
    await cache.activate_kill_switch("test")
    await engine.reset_kill_switch("operator")
    assert not await cache.is_kill_switch_active()


@pytest.mark.asyncio
async def test_risk_summary(engine, cache):
    summary = await engine.get_risk_summary()
    assert "kill_switch_active" in summary
    assert "daily_realized_pnl" in summary
    assert "open_positions" in summary
