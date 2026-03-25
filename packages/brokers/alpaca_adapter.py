"""Alpaca broker adapter using alpaca-py SDK."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from packages.brokers.base import BaseBroker
from packages.core.enums import BrokerName, Interval, OrderSide, OrderStatus, OrderType, TimeInForce
from packages.core.models import AccountState, Bar, Fill, Order, Position, Quote
from packages.core.utils import generate_client_order_id
from packages.observability.logging import get_logger
from packages.observability.metrics import order_latency_seconds, orders_submitted_total

log = get_logger(__name__)


def _alpaca_tf(interval: Interval) -> str:
    mapping = {
        Interval.min_1: "1Min",
        Interval.min_5: "5Min",
        Interval.min_15: "15Min",
        Interval.min_30: "30Min",
        Interval.hour_1: "1Hour",
        Interval.day_1: "1Day",
    }
    return mapping.get(interval, "1Min")


def _map_order_status(status: str) -> OrderStatus:
    mapping = {
        "new": OrderStatus.acknowledged,
        "partially_filled": OrderStatus.partially_filled,
        "filled": OrderStatus.filled,
        "canceled": OrderStatus.canceled,
        "cancelled": OrderStatus.canceled,
        "rejected": OrderStatus.rejected,
        "expired": OrderStatus.expired,
        "pending_new": OrderStatus.pending,
        "accepted": OrderStatus.acknowledged,
        "pending_cancel": OrderStatus.acknowledged,
        "replaced": OrderStatus.acknowledged,
    }
    return mapping.get(status.lower(), OrderStatus.pending)


class AlpacaAdapter(BaseBroker):
    """Live/paper equity trading via Alpaca Markets."""

    broker_name: BrokerName = BrokerName.alpaca

    def __init__(self, api_key: str, api_secret: str, base_url: str, paper: bool = True) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url
        self._paper = paper
        self._trading_client = None
        self._data_client = None
        self._stream_client = None
        self._bar_callbacks: list[Callable[[Bar], None]] = []
        self._quote_callbacks: list[Callable[[Quote], None]] = []

    async def connect(self) -> None:
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.live import StockDataStream

            self._trading_client = TradingClient(
                self._api_key, self._api_secret, paper=self._paper
            )
            self._data_client = StockHistoricalDataClient(self._api_key, self._api_secret)
            self._stream_client = StockDataStream(self._api_key, self._api_secret)
            log.info("Alpaca adapter connected", paper=self._paper)
        except ImportError as e:
            raise RuntimeError(f"alpaca-py not installed: {e}") from e

    async def disconnect(self) -> None:
        if self._stream_client:
            try:
                await self._stream_client.close()
            except Exception:
                pass
        log.info("Alpaca adapter disconnected")

    async def get_account(self) -> AccountState:
        acct = self._trading_client.get_account()
        return AccountState(
            broker=BrokerName.alpaca,
            cash=Decimal(str(acct.cash)),
            buying_power=Decimal(str(acct.buying_power)),
            equity=Decimal(str(acct.equity)),
            portfolio_value=Decimal(str(acct.portfolio_value)),
            day_trade_count=int(acct.daytrade_count or 0),
        )

    async def get_positions(self) -> list[Position]:
        raw = self._trading_client.get_all_positions()
        return [
            Position(
                symbol=p.symbol,
                qty=Decimal(str(p.qty)),
                avg_entry_price=Decimal(str(p.avg_entry_price)),
                current_price=Decimal(str(p.current_price or p.avg_entry_price)),
            )
            for p in raw
        ]

    async def get_position(self, symbol: str) -> Optional[Position]:
        try:
            p = self._trading_client.get_open_position(symbol)
            return Position(
                symbol=p.symbol,
                qty=Decimal(str(p.qty)),
                avg_entry_price=Decimal(str(p.avg_entry_price)),
                current_price=Decimal(str(p.current_price or p.avg_entry_price)),
            )
        except Exception:
            return None

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
    ) -> Order:
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide as AlpacaSide, TimeInForce as AlpacaTIF

        coid = client_order_id or generate_client_order_id(strategy_id or "", symbol)
        alpaca_side = AlpacaSide.BUY if side == OrderSide.buy else AlpacaSide.SELL
        alpaca_tif = AlpacaTIF.DAY

        t0 = time.monotonic()
        if order_type == OrderType.market:
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=alpaca_side,
                time_in_force=alpaca_tif,
                client_order_id=coid,
            )
        else:
            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=alpaca_side,
                time_in_force=alpaca_tif,
                limit_price=limit_price,
                client_order_id=coid,
            )

        raw = self._trading_client.submit_order(req)
        latency = time.monotonic() - t0
        order_latency_seconds.labels(broker="alpaca").observe(latency)
        orders_submitted_total.labels(symbol=symbol, side=side.value, broker="alpaca").inc()

        return Order(
            id=raw.id,
            client_order_id=coid,
            broker_order_id=str(raw.id),
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=Decimal(str(qty)),
            limit_price=Decimal(str(limit_price)) if limit_price else None,
            stop_price=Decimal(str(stop_price)) if stop_price else None,
            status=_map_order_status(str(raw.status)),
            broker=BrokerName.alpaca,
            strategy_id=strategy_id,
            submitted_at=datetime.now(timezone.utc),
        )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            self._trading_client.cancel_order_by_id(order_id)
            return True
        except Exception:
            return False

    async def cancel_all_orders(self) -> int:
        canceled = self._trading_client.cancel_orders()
        return len(canceled) if canceled else 0

    async def get_order(self, order_id: str) -> Optional[Order]:
        try:
            raw = self._trading_client.get_order_by_id(order_id)
            return Order(
                id=raw.id,
                broker_order_id=str(raw.id),
                symbol=raw.symbol,
                side=OrderSide.buy if str(raw.side).lower() == "buy" else OrderSide.sell,
                order_type=OrderType.market,
                qty=Decimal(str(raw.qty)),
                status=_map_order_status(str(raw.status)),
                filled_qty=Decimal(str(raw.filled_qty or 0)),
                avg_fill_price=Decimal(str(raw.filled_avg_price)) if raw.filled_avg_price else None,
                broker=BrokerName.alpaca,
            )
        except Exception:
            return None

    async def get_open_orders(self) -> list[Order]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        raw_orders = self._trading_client.get_orders(req)
        orders = []
        for raw in (raw_orders or []):
            orders.append(Order(
                id=raw.id,
                broker_order_id=str(raw.id),
                symbol=raw.symbol,
                side=OrderSide.buy if str(raw.side).lower() == "buy" else OrderSide.sell,
                order_type=OrderType.market,
                qty=Decimal(str(raw.qty)),
                status=_map_order_status(str(raw.status)),
                broker=BrokerName.alpaca,
            ))
        return orders

    async def get_bars(
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[Bar]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        tf_str = _alpaca_tf(interval)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            limit=limit,
        )
        data = self._data_client.get_stock_bars(req)
        bars = []
        for raw in (data.get(symbol) or []):
            bars.append(Bar(
                symbol=symbol,
                timestamp=raw.timestamp,
                open=Decimal(str(raw.open)),
                high=Decimal(str(raw.high)),
                low=Decimal(str(raw.low)),
                close=Decimal(str(raw.close)),
                volume=Decimal(str(raw.volume)),
                vwap=Decimal(str(raw.vwap)) if raw.vwap else None,
                interval=interval,
                source="alpaca",
            ))
        return bars

    async def get_quote(self, symbol: str) -> Quote:
        from alpaca.data.requests import StockLatestQuoteRequest

        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        data = self._data_client.get_stock_latest_quote(req)
        raw = data[symbol]
        return Quote(
            symbol=symbol,
            timestamp=raw.timestamp,
            bid=Decimal(str(raw.bid_price)),
            ask=Decimal(str(raw.ask_price)),
            bid_size=Decimal(str(raw.bid_size)),
            ask_size=Decimal(str(raw.ask_size)),
            source="alpaca",
        )

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        from alpaca.data.requests import StockLatestQuoteRequest

        req = StockLatestQuoteRequest(symbol_or_symbols=symbols)
        data = self._data_client.get_stock_latest_quote(req)
        out = {}
        for sym, raw in (data or {}).items():
            out[sym] = Quote(
                symbol=sym,
                timestamp=raw.timestamp,
                bid=Decimal(str(raw.bid_price)),
                ask=Decimal(str(raw.ask_price)),
                source="alpaca",
            )
        return out

    async def subscribe_bars(
        self,
        symbols: list[str],
        interval: Interval,
        callback: Callable[[Bar], None],
    ) -> None:
        self._bar_callbacks.append(callback)

        async def _handler(raw):
            bar = Bar(
                symbol=raw.symbol,
                timestamp=raw.timestamp,
                open=Decimal(str(raw.open)),
                high=Decimal(str(raw.high)),
                low=Decimal(str(raw.low)),
                close=Decimal(str(raw.close)),
                volume=Decimal(str(raw.volume)),
                vwap=Decimal(str(raw.vwap)) if raw.vwap else None,
                interval=interval,
                source="alpaca_stream",
            )
            for cb in self._bar_callbacks:
                cb(bar)

        self._stream_client.subscribe_bars(_handler, *symbols)

    async def subscribe_quotes(
        self,
        symbols: list[str],
        callback: Callable[[Quote], None],
    ) -> None:
        self._quote_callbacks.append(callback)

        async def _handler(raw):
            quote = Quote(
                symbol=raw.symbol,
                timestamp=raw.timestamp,
                bid=Decimal(str(raw.bid_price)),
                ask=Decimal(str(raw.ask_price)),
                bid_size=Decimal(str(raw.bid_size or 0)),
                ask_size=Decimal(str(raw.ask_size or 0)),
                source="alpaca_stream",
            )
            for cb in self._quote_callbacks:
                cb(quote)

        self._stream_client.subscribe_quotes(_handler, *symbols)


class SimulatorAdapter(BaseBroker):
    """In-process paper broker for testing and paper trading."""

    broker_name: BrokerName = BrokerName.simulator

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        commission_per_share: float = 0.005,
        slippage_bps: float = 2.0,
    ) -> None:
        self._cash = Decimal(str(initial_cash))
        self._initial_cash = Decimal(str(initial_cash))
        self._commission = Decimal(str(commission_per_share))
        self._slippage_bps = Decimal(str(slippage_bps))
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._last_prices: dict[str, Decimal] = {}
        self._fill_callbacks: list[Callable[[Fill], None]] = []
        self._bar_callbacks: list[Callable[[Bar], None]] = []
        self._quote_callbacks: list[Callable[[Quote], None]] = []

    async def connect(self) -> None:
        log.info("Simulator adapter connected", cash=float(self._cash))

    async def disconnect(self) -> None:
        log.info("Simulator adapter disconnected")

    def register_fill_callback(self, callback: Callable[[Fill], None]) -> None:
        self._fill_callbacks.append(callback)

    async def get_account(self) -> AccountState:
        portfolio_value = self._cash
        for sym, pos in self._positions.items():
            price = self._last_prices.get(sym, pos.avg_entry_price)
            portfolio_value += abs(pos.qty) * price

        return AccountState(
            broker=BrokerName.simulator,
            cash=self._cash,
            buying_power=self._cash,
            equity=portfolio_value,
            portfolio_value=portfolio_value,
        )

    async def get_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.qty != 0]

    async def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

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
    ) -> Order:
        from datetime import datetime, timezone
        from uuid import uuid4

        coid = client_order_id or generate_client_order_id(strategy_id or "", symbol)
        order_id = str(uuid4())
        qty_dec = Decimal(str(qty))

        # Determine fill price with slippage
        base_price = self._last_prices.get(symbol, Decimal("100"))
        if side == OrderSide.buy:
            slippage_factor = Decimal("1") + self._slippage_bps / Decimal("10000")
        else:
            slippage_factor = Decimal("1") - self._slippage_bps / Decimal("10000")
        fill_price = base_price * slippage_factor

        # Commission
        commission = qty_dec * self._commission
        notional = qty_dec * fill_price

        if side == OrderSide.buy:
            cost = notional + commission
            if cost > self._cash:
                order = Order(
                    id=order_id,
                    client_order_id=coid,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    qty=qty_dec,
                    status=OrderStatus.rejected,
                    broker=BrokerName.simulator,
                    strategy_id=strategy_id,
                )
                self._orders[order_id] = order
                return order
            self._cash -= cost
        else:
            self._cash += notional - commission

        # Update position
        pos = self._positions.get(symbol, Position(symbol=symbol))
        if side == OrderSide.buy:
            if pos.qty < 0:
                pos.avg_entry_price = fill_price
                pos.qty += qty_dec
            elif pos.qty == 0:
                pos.avg_entry_price = fill_price
                pos.qty = qty_dec
            else:
                total_cost = pos.qty * pos.avg_entry_price + qty_dec * fill_price
                pos.qty += qty_dec
                pos.avg_entry_price = total_cost / pos.qty
        else:
            pos.qty -= qty_dec

        if pos.qty == 0:
            pos.avg_entry_price = Decimal("0")
        self._positions[symbol] = pos

        now = datetime.now(timezone.utc)
        order = Order(
            id=order_id,
            client_order_id=coid,
            broker_order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty_dec,
            status=OrderStatus.filled,
            filled_qty=qty_dec,
            avg_fill_price=fill_price,
            submitted_at=now,
            filled_at=now,
            broker=BrokerName.simulator,
            strategy_id=strategy_id,
        )
        self._orders[order_id] = order
        orders_submitted_total.labels(symbol=symbol, side=side.value, broker="simulator").inc()

        fill = Fill(
            order_id=order_id,
            client_order_id=coid,
            broker_order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty_dec,
            price=fill_price,
            commission=commission,
            timestamp=now,
            strategy_id=strategy_id,
        )
        self._fills.append(fill)
        for cb in self._fill_callbacks:
            cb(fill)

        return order

    async def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order and not order.is_terminal:
            order.status = OrderStatus.canceled
            return True
        return False

    async def cancel_all_orders(self) -> int:
        count = 0
        for order in self._orders.values():
            if not order.is_terminal:
                order.status = OrderStatus.canceled
                count += 1
        return count

    async def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    async def get_open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if not o.is_terminal]

    async def get_bars(
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[Bar]:
        return []

    async def get_quote(self, symbol: str) -> Quote:
        from datetime import datetime, timezone

        price = self._last_prices.get(symbol, Decimal("100"))
        spread = price * Decimal("0.0001")
        return Quote(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bid=price - spread / 2,
            ask=price + spread / 2,
            source="simulator",
        )

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: await self.get_quote(s) for s in symbols}

    async def subscribe_bars(
        self,
        symbols: list[str],
        interval: Interval,
        callback: Callable[[Bar], None],
    ) -> None:
        self._bar_callbacks.append(callback)

    async def subscribe_quotes(
        self,
        symbols: list[str],
        callback: Callable[[Quote], None],
    ) -> None:
        self._quote_callbacks.append(callback)

    def feed_bar(self, bar: Bar) -> None:
        """Inject a bar for testing — updates price and fires callbacks."""
        self._last_prices[bar.symbol] = bar.close
        for cb in self._bar_callbacks:
            cb(bar)
