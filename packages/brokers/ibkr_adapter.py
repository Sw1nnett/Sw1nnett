"""IBKR broker adapter — Phase 3 scaffold."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from packages.brokers.base import BaseBroker
from packages.core.enums import Interval, OrderSide, OrderType, TimeInForce
from packages.core.models import AccountState, Bar, Fill, Order, Position, Quote


class IBKRAdapter(BaseBroker):
    """Interactive Brokers adapter — not yet implemented."""

    async def connect(self) -> None:
        raise NotImplementedError("IBKR adapter is Phase 3")

    async def disconnect(self) -> None:
        raise NotImplementedError("IBKR adapter is Phase 3")

    async def get_account(self) -> AccountState:
        raise NotImplementedError

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError

    async def get_position(self, symbol: str) -> Optional[Position]:
        raise NotImplementedError

    async def submit_order(self, symbol, side, qty, order_type=OrderType.market,
                           limit_price=None, stop_price=None,
                           time_in_force=TimeInForce.day,
                           client_order_id=None, strategy_id=None) -> Order:
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    async def cancel_all_orders(self) -> int:
        raise NotImplementedError

    async def get_order(self, order_id: str) -> Optional[Order]:
        raise NotImplementedError

    async def get_open_orders(self) -> list[Order]:
        raise NotImplementedError

    async def get_bars(self, symbol, interval, start, end=None, limit=1000) -> list[Bar]:
        raise NotImplementedError

    async def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        raise NotImplementedError

    async def subscribe_bars(self, symbols, interval, callback) -> None:
        raise NotImplementedError

    async def subscribe_quotes(self, symbols, callback) -> None:
        raise NotImplementedError
