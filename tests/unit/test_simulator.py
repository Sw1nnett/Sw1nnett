"""Tests for the SimulatorAdapter."""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone

import pytest

from packages.brokers.alpaca_adapter import SimulatorAdapter
from packages.core.enums import BrokerName, OrderSide, OrderStatus, OrderType
from packages.core.models import Fill
from tests.fixtures.factories import make_bar


@pytest.fixture
def sim():
    return SimulatorAdapter(initial_cash=100_000.0)


@pytest.mark.asyncio
async def test_connect_and_account(sim):
    await sim.connect()
    account = await sim.get_account()
    assert account.broker == BrokerName.simulator
    assert account.cash == Decimal("100000")
    assert account.equity == Decimal("100000")


@pytest.mark.asyncio
async def test_buy_order_fills(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("450")
    order = await sim.submit_order("SPY", OrderSide.buy, 10)
    assert order.status == OrderStatus.filled
    assert order.filled_qty == Decimal("10")
    assert order.avg_fill_price is not None


@pytest.mark.asyncio
async def test_buy_reduces_cash(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("100")
    initial_cash = sim._cash
    await sim.submit_order("SPY", OrderSide.buy, 10)
    assert sim._cash < initial_cash


@pytest.mark.asyncio
async def test_sell_increases_cash(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("100")
    # First buy
    await sim.submit_order("SPY", OrderSide.buy, 10)
    after_buy = sim._cash
    # Then sell
    await sim.submit_order("SPY", OrderSide.sell, 10)
    assert sim._cash > after_buy


@pytest.mark.asyncio
async def test_position_after_buy(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("450")
    await sim.submit_order("SPY", OrderSide.buy, 5)
    pos = await sim.get_position("SPY")
    assert pos is not None
    assert pos.qty == Decimal("5")


@pytest.mark.asyncio
async def test_position_flat_after_round_trip(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("450")
    await sim.submit_order("SPY", OrderSide.buy, 10)
    await sim.submit_order("SPY", OrderSide.sell, 10)
    pos = await sim.get_position("SPY")
    assert pos is None or pos.qty == Decimal("0")


@pytest.mark.asyncio
async def test_insufficient_funds_rejects(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("1000")
    # 200 shares @ 1000 = 200k, but only have 100k
    order = await sim.submit_order("SPY", OrderSide.buy, 200)
    assert order.status == OrderStatus.rejected


@pytest.mark.asyncio
async def test_cancel_order(sim):
    await sim.connect()
    # Create an open order manually to cancel
    from packages.core.models import Order
    from uuid import uuid4
    order = Order(
        id=str(uuid4()),
        client_order_id="can-test",
        symbol="SPY",
        side=OrderSide.buy,
        order_type=OrderType.market,
        qty=Decimal("10"),
        status=OrderStatus.acknowledged,
        broker=BrokerName.simulator,
    )
    sim._orders[str(order.id)] = order
    result = await sim.cancel_order(str(order.id))
    assert result is True
    assert order.status == OrderStatus.canceled


@pytest.mark.asyncio
async def test_get_positions(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("450")
    sim._last_prices["QQQ"] = Decimal("380")
    await sim.submit_order("SPY", OrderSide.buy, 5)
    await sim.submit_order("QQQ", OrderSide.buy, 3)
    positions = await sim.get_positions()
    symbols = {p.symbol for p in positions}
    assert "SPY" in symbols
    assert "QQQ" in symbols


@pytest.mark.asyncio
async def test_fill_callback_fired(sim):
    fills = []
    sim.register_fill_callback(fills.append)
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("450")
    await sim.submit_order("SPY", OrderSide.buy, 5)
    assert len(fills) == 1
    assert fills[0].symbol == "SPY"


@pytest.mark.asyncio
async def test_slippage_on_buy(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("100")
    order = await sim.submit_order("SPY", OrderSide.buy, 1)
    # Buy price should be slightly above 100 due to slippage
    assert order.avg_fill_price > Decimal("100")


@pytest.mark.asyncio
async def test_slippage_on_sell(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("100")
    await sim.submit_order("SPY", OrderSide.buy, 10)
    sell_order = await sim.submit_order("SPY", OrderSide.sell, 10)
    # Sell price should be slightly below 100 due to slippage
    assert sell_order.avg_fill_price < Decimal("100")


@pytest.mark.asyncio
async def test_quote(sim):
    await sim.connect()
    sim._last_prices["SPY"] = Decimal("450")
    quote = await sim.get_quote("SPY")
    assert quote.bid < Decimal("450") < quote.ask


@pytest.mark.asyncio
async def test_feed_bar_updates_price(sim):
    await sim.connect()
    bar = make_bar(symbol="SPY", close=999.0)
    sim.feed_bar(bar)
    assert sim._last_prices["SPY"] == Decimal("999")


@pytest.mark.asyncio
async def test_open_orders_empty_initially(sim):
    await sim.connect()
    orders = await sim.get_open_orders()
    assert orders == []
