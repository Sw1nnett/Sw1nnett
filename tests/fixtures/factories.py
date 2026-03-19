"""Test data factories."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from packages.core.enums import BrokerName, OrderSide, OrderStatus, OrderType
from packages.core.models import AccountState, Bar, Fill, Order, Position, Quote, StrategyConfig


def make_bar(
    symbol: str = "SPY",
    close: float = 450.0,
    volume: float = 100_000.0,
    timestamp: datetime | None = None,
    interval: str = "1m",
) -> Bar:
    from packages.core.enums import Interval
    ts = timestamp or datetime.now(timezone.utc)
    spread = close * 0.001
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=Decimal(str(close - spread)),
        high=Decimal(str(close + spread * 2)),
        low=Decimal(str(close - spread * 2)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
        interval=Interval.min_1,
        source="test",
    )


def make_bars(
    symbol: str = "SPY",
    n: int = 50,
    start_price: float = 450.0,
    start_time: datetime | None = None,
) -> list[Bar]:
    bars = []
    price = start_price
    ts = start_time or datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
    for i in range(n):
        price *= 1 + random.gauss(0, 0.001)
        volume = random.uniform(80_000, 150_000)
        bars.append(make_bar(symbol=symbol, close=price, volume=volume, timestamp=ts))
        ts += timedelta(minutes=1)
    return bars


def make_quote(
    symbol: str = "SPY",
    bid: float = 449.95,
    ask: float = 450.05,
) -> Quote:
    return Quote(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        source="test",
    )


def make_order(
    symbol: str = "SPY",
    side: OrderSide = OrderSide.buy,
    qty: float = 10.0,
    status: OrderStatus = OrderStatus.pending,
    strategy_id: str = "test_strategy",
) -> Order:
    return Order(
        client_order_id=f"test-{uuid4().hex[:8]}",
        symbol=symbol,
        side=side,
        order_type=OrderType.market,
        qty=Decimal(str(qty)),
        status=status,
        broker=BrokerName.simulator,
        strategy_id=strategy_id,
    )


def make_fill(
    symbol: str = "SPY",
    side: OrderSide = OrderSide.buy,
    qty: float = 10.0,
    price: float = 450.0,
    strategy_id: str = "test_strategy",
) -> Fill:
    order_id = uuid4()
    return Fill(
        order_id=order_id,
        client_order_id=f"test-{order_id.hex[:8]}",
        symbol=symbol,
        side=side,
        qty=Decimal(str(qty)),
        price=Decimal(str(price)),
        commission=Decimal("0.05"),
        timestamp=datetime.now(timezone.utc),
        strategy_id=strategy_id,
    )


def make_account(
    cash: float = 100_000.0,
    equity: float = 100_000.0,
    broker: BrokerName = BrokerName.simulator,
) -> AccountState:
    return AccountState(
        broker=broker,
        cash=Decimal(str(cash)),
        buying_power=Decimal(str(cash)),
        equity=Decimal(str(equity)),
        portfolio_value=Decimal(str(equity)),
    )


def make_position(
    symbol: str = "SPY",
    qty: float = 10.0,
    avg_entry_price: float = 450.0,
    current_price: float = 455.0,
) -> Position:
    return Position(
        symbol=symbol,
        qty=Decimal(str(qty)),
        avg_entry_price=Decimal(str(avg_entry_price)),
        current_price=Decimal(str(current_price)),
    )


def make_config(
    cls_name: str = "MomentumBreakoutStrategy",
    symbols: list[str] | None = None,
) -> StrategyConfig:
    return StrategyConfig(
        cls_name=cls_name,
        symbols=symbols or ["SPY", "QQQ"],
        params={},
    )
