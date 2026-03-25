"""Execution engine — risk-gated order submission."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from packages.brokers.base import BaseBroker
from packages.core.enums import BrokerName, OrderSide, OrderStatus, OrderType, TradingMode
from packages.core.models import Fill, Order, Signal
from packages.core.utils import generate_client_order_id
from packages.data.database import get_db_session
from packages.data.orm_models import FillORM, OrderORM
from packages.observability.logging import get_logger
from workers.portfolio.tracker import PortfolioTracker
from workers.risk.engine import RiskEngine


log = get_logger(__name__)


class ExecutionEngine:
    def __init__(
        self,
        broker: BaseBroker,
        risk_engine: RiskEngine,
        portfolio: PortfolioTracker,
        trading_mode: TradingMode = TradingMode.paper,
        database_url: Optional[str] = None,
    ) -> None:
        self._broker = broker
        self._risk = risk_engine
        self._portfolio = portfolio
        self._mode = trading_mode
        self._db_url = database_url
        self._pending_orders: dict[str, Order] = {}
        self._blocked_orders: list[dict] = []

    # ------------------------------------------------------------------ #
    # Signal → Order
    # ------------------------------------------------------------------ #
    async def submit_signal(self, signal: Signal) -> Optional[Order]:
        """Convert a signal to an order, run risk checks, then submit."""
        if not signal.suggested_qty or signal.suggested_qty <= 0:
            log.debug("signal_no_qty", symbol=signal.symbol)
            return None

        coid = generate_client_order_id(signal.strategy_id, signal.symbol)
        broker_name: BrokerName = getattr(self._broker, "broker_name", BrokerName.simulator)
        order = Order(
            client_order_id=coid,
            symbol=signal.symbol,
            side=OrderSide.buy,  # Phase 1: long only
            order_type=OrderType.market,
            qty=signal.suggested_qty,
            broker=broker_name,
            strategy_id=signal.strategy_id,
        )

        return await self.submit_order(signal, order)

    async def submit_order(self, signal: Signal, order: Order) -> Optional[Order]:
        """Run pre-trade risk checks then submit."""
        result = await self._risk.check_pre_trade(signal, order)

        if not result.passed:
            self._blocked_orders.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": signal.symbol,
                "strategy_id": signal.strategy_id,
                "reason": result.blocked_reason,
                "risk_event": result.risk_event.value,
            })
            return None

        if self._mode == TradingMode.live_dry_run:
            log.info(
                "dry_run_order_not_sent",
                symbol=order.symbol,
                side=order.side.value,
                qty=float(order.qty),
            )
            order.status = OrderStatus.canceled
            return order

        submitted = await self._broker.submit_order(
            symbol=order.symbol,
            side=order.side,
            qty=float(order.qty),
            order_type=order.order_type,
            limit_price=float(order.limit_price) if order.limit_price else None,
            client_order_id=order.client_order_id,
            strategy_id=order.strategy_id,
        )
        submitted.submitted_at = datetime.now(timezone.utc)
        self._pending_orders[submitted.client_order_id] = submitted

        if self._db_url:
            await self._persist_order(submitted)

        log.info(
            "order_submitted",
            symbol=submitted.symbol,
            side=submitted.side.value,
            qty=float(submitted.qty),
            client_order_id=submitted.client_order_id,
            status=submitted.status.value,
        )
        return submitted

    # ------------------------------------------------------------------ #
    # Fill processing
    # ------------------------------------------------------------------ #
    def on_fill_received(self, fill: Fill) -> None:
        self._portfolio.on_fill(fill)
        self._risk.update_positions(self._portfolio.positions)
        self._risk.update_account_equity(self._portfolio.equity)
        self._risk.update_daily_pnl(self._portfolio.realized_pnl)
        log.info(
            "fill_received",
            symbol=fill.symbol,
            qty=float(fill.qty),
            price=float(fill.price),
        )
        if self._db_url:
            import asyncio
            asyncio.get_event_loop().call_soon_threadsafe(
                lambda: asyncio.create_task(self._persist_fill(fill))
            )

    # ------------------------------------------------------------------ #
    # Order management
    # ------------------------------------------------------------------ #
    async def cancel_order(self, client_order_id: str) -> bool:
        order = self._pending_orders.get(client_order_id)
        if not order or not order.broker_order_id:
            return False
        return await self._broker.cancel_order(order.broker_order_id)

    async def flatten_all(self) -> list[Order]:
        log.warning("flatten_all_triggered")
        return await self._broker.flatten_all_positions()

    # ------------------------------------------------------------------ #
    # Reconciliation
    # ------------------------------------------------------------------ #
    async def reconcile(self) -> None:
        """Compare internal positions with broker state."""
        broker_positions = await self._broker.get_positions()
        broker_map = {p.symbol: p for p in broker_positions}
        internal_map = self._portfolio.positions

        all_symbols = set(broker_map) | set(internal_map)
        for sym in all_symbols:
            b_qty = broker_map[sym].qty if sym in broker_map else Decimal("0")
            i_qty = internal_map[sym].qty if sym in internal_map else Decimal("0")
            if abs(b_qty - i_qty) > Decimal("0.01"):
                log.warning(
                    "reconciliation_mismatch",
                    symbol=sym,
                    broker_qty=float(b_qty),
                    internal_qty=float(i_qty),
                )

    # ------------------------------------------------------------------ #
    # Accessors
    # ------------------------------------------------------------------ #
    @property
    def blocked_orders(self) -> list[dict]:
        return list(self._blocked_orders)

    @property
    def pending_orders(self) -> dict[str, Order]:
        return dict(self._pending_orders)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    async def _persist_order(self, order: Order) -> None:
        try:
            async with get_db_session(self._db_url) as session:
                orm = OrderORM(
                    id=str(order.id),
                    client_order_id=order.client_order_id,
                    broker_order_id=order.broker_order_id,
                    symbol=order.symbol,
                    side=order.side.value,
                    order_type=order.order_type.value,
                    qty=order.qty,
                    limit_price=order.limit_price,
                    stop_price=order.stop_price,
                    time_in_force=order.time_in_force.value,
                    status=order.status.value,
                    filled_qty=order.filled_qty,
                    avg_fill_price=order.avg_fill_price,
                    submitted_at=order.submitted_at,
                    broker=order.broker.value if hasattr(order.broker, "value") else str(order.broker),
                    strategy_id=order.strategy_id,
                )
                session.add(orm)
        except Exception as e:
            log.error("order_persist_failed", error=str(e))

    async def _persist_fill(self, fill: Fill) -> None:
        try:
            async with get_db_session(self._db_url) as session:
                orm = FillORM(
                    id=str(fill.id),
                    order_id=str(fill.order_id),
                    client_order_id=fill.client_order_id,
                    broker_order_id=fill.broker_order_id,
                    symbol=fill.symbol,
                    side=fill.side.value,
                    qty=fill.qty,
                    price=fill.price,
                    commission=fill.commission,
                    timestamp=fill.timestamp,
                    strategy_id=fill.strategy_id,
                )
                session.add(orm)
        except Exception as e:
            log.error("fill_persist_failed", error=str(e))
