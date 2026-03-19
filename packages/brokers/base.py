"""Abstract broker interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncIterator, Callable, Optional

from packages.core.enums import Interval, OrderSide, OrderType, TimeInForce
from packages.core.models import AccountState, Bar, Fill, Order, Position, Quote


class BaseBroker(ABC):
    """All broker adapters must implement this interface."""

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    # ------------------------------------------------------------------ #
    # Account
    # ------------------------------------------------------------------ #
    @abstractmethod
    async def get_account(self) -> AccountState: ...

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Position]: ...

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    @abstractmethod
    async def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: OrderType = OrderType.market,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: TimeInForce = TimeInForce.day,
        client_order_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
    ) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def cancel_all_orders(self) -> int: ...

    @abstractmethod
    async def get_order(self, order_id: str) -> Optional[Order]: ...

    @abstractmethod
    async def get_open_orders(self) -> list[Order]: ...

    # ------------------------------------------------------------------ #
    # Market data
    # ------------------------------------------------------------------ #
    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[Bar]: ...

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #
    @abstractmethod
    async def subscribe_bars(
        self,
        symbols: list[str],
        interval: Interval,
        callback: Callable[[Bar], None],
    ) -> None: ...

    @abstractmethod
    async def subscribe_quotes(
        self,
        symbols: list[str],
        callback: Callable[[Quote], None],
    ) -> None: ...

    # ------------------------------------------------------------------ #
    # Safety
    # ------------------------------------------------------------------ #
    async def flatten_all_positions(self) -> list[Order]:
        """Close all open positions with market orders."""
        positions = await self.get_positions()
        orders: list[Order] = []
        for pos in positions:
            if pos.qty == 0:
                continue
            side = OrderSide.sell if pos.qty > 0 else OrderSide.buy
            order = await self.submit_order(
                symbol=pos.symbol,
                side=side,
                qty=abs(float(pos.qty)),
                order_type=OrderType.market,
                strategy_id="flatten_all",
            )
            orders.append(order)
        return orders
