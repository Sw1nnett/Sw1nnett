"""Core Pydantic domain models."""
from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field

from packages.core.enums import (
    BrokerName,
    Interval,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskEvent,
    SignalDirection,
    StrategyState,
    TimeInForce,
)


class Bar(BaseModel):
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Optional[Decimal] = None
    interval: Interval = Interval.min_1
    source: str = "unknown"

    @computed_field  # type: ignore[misc]
    @property
    def mid(self) -> Decimal:
        return (self.high + self.low) / 2


class Quote(BaseModel):
    symbol: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    bid_size: Decimal = Decimal("0")
    ask_size: Decimal = Decimal("0")
    source: str = "unknown"

    @computed_field  # type: ignore[misc]
    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @computed_field  # type: ignore[misc]
    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @computed_field  # type: ignore[misc]
    @property
    def spread_bps(self) -> Decimal:
        if self.mid == 0:
            return Decimal("0")
        return (self.spread / self.mid) * Decimal("10000")


class Order(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    client_order_id: str = Field(default_factory=lambda: str(uuid4()))
    broker_order_id: Optional[str] = None
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: Decimal
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    time_in_force: TimeInForce = TimeInForce.day
    status: OrderStatus = OrderStatus.pending
    filled_qty: Decimal = Decimal("0")
    avg_fill_price: Optional[Decimal] = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    broker: BrokerName = BrokerName.simulator
    strategy_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[misc]
    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OrderStatus.filled,
            OrderStatus.canceled,
            OrderStatus.rejected,
            OrderStatus.expired,
        }

    @computed_field  # type: ignore[misc]
    @property
    def remaining_qty(self) -> Decimal:
        return self.qty - self.filled_qty


class Fill(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    client_order_id: str
    broker_order_id: Optional[str] = None
    symbol: str
    side: OrderSide
    qty: Decimal
    price: Decimal
    commission: Decimal = Decimal("0")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    strategy_id: Optional[str] = None

    @computed_field  # type: ignore[misc]
    @property
    def notional(self) -> Decimal:
        return self.qty * self.price


class Position(BaseModel):
    symbol: str
    qty: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    strategy_id: Optional[str] = None
    opened_at: Optional[datetime] = None

    def update_price(self, price: Decimal) -> None:
        self.current_price = price

    @computed_field  # type: ignore[misc]
    @property
    def side(self) -> str:
        if self.qty > 0:
            return "long"
        if self.qty < 0:
            return "short"
        return "flat"

    @computed_field  # type: ignore[misc]
    @property
    def notional(self) -> Decimal:
        return abs(self.qty) * self.current_price

    @computed_field  # type: ignore[misc]
    @property
    def unrealized_pnl(self) -> Decimal:
        if self.qty == 0 or self.avg_entry_price == 0:
            return Decimal("0")
        return self.qty * (self.current_price - self.avg_entry_price)

    @computed_field  # type: ignore[misc]
    @property
    def unrealized_pnl_pct(self) -> Decimal:
        if self.avg_entry_price == 0 or self.qty == 0:
            return Decimal("0")
        return (self.current_price - self.avg_entry_price) / self.avg_entry_price * Decimal("100")


class Signal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    strategy_id: str
    symbol: str
    direction: SignalDirection
    confidence: Decimal = Decimal("1.0")
    entry_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    target_price: Optional[Decimal] = None
    suggested_qty: Optional[Decimal] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskCheckResult(BaseModel):
    passed: bool
    blocked_reason: Optional[str] = None
    risk_event: RiskEvent = RiskEvent.allowed
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def ok(cls) -> "RiskCheckResult":
        return cls(passed=True, risk_event=RiskEvent.allowed)

    @classmethod
    def block(cls, reason: str, event: RiskEvent, **details: Any) -> "RiskCheckResult":
        return cls(passed=False, blocked_reason=reason, risk_event=event, details=details)


class AccountState(BaseModel):
    broker: BrokerName
    cash: Decimal
    buying_power: Decimal
    equity: Decimal
    portfolio_value: Decimal
    day_trade_count: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AlertMessage(BaseModel):
    level: str
    title: str
    body: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrategyConfig(BaseModel):
    strategy_id: str = Field(default_factory=lambda: str(uuid4()))
    cls_name: str
    symbols: list[str]
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    state: StrategyState = StrategyState.idle


class DailyReport(BaseModel):
    date: date
    strategy_id: Optional[str]
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    gross_pnl: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    total_commission: Decimal = Decimal("0")
    total_slippage_bps: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    blocked_trades: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
