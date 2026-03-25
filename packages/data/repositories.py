"""Repository classes — typed data access over ORM models.

Each repository wraps an AsyncSession and exposes focused query/save methods.
All public methods are async and accept/return domain types where possible.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.data.orm_models import (
    AuditEventORM,
    BrokerReconciliationORM,
    DailyReportORM,
    FillORM,
    OrderORM,
    PositionORM,
    RiskEventORM,
)
from packages.observability.logging import get_logger

log = get_logger(__name__)


# ------------------------------------------------------------------ #
# Order repository
# ------------------------------------------------------------------ #

class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, orm: OrderORM) -> None:
        self._s.add(orm)

    async def get_by_client_id(self, client_order_id: str) -> Optional[OrderORM]:
        result = await self._s.execute(
            select(OrderORM).where(OrderORM.client_order_id == client_order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, order_id: str) -> Optional[OrderORM]:
        return await self._s.get(OrderORM, order_id)

    async def update_status(
        self,
        client_order_id: str,
        status: str,
        filled_qty: Optional[Decimal] = None,
        avg_fill_price: Optional[Decimal] = None,
        filled_at: Optional[datetime] = None,
    ) -> int:
        values: dict = {"status": status}
        if filled_qty is not None:
            values["filled_qty"] = filled_qty
        if avg_fill_price is not None:
            values["avg_fill_price"] = avg_fill_price
        if filled_at is not None:
            values["filled_at"] = filled_at
        result = await self._s.execute(
            update(OrderORM)
            .where(OrderORM.client_order_id == client_order_id)
            .values(**values)
        )
        return result.rowcount

    async def list_by_symbol(self, symbol: str, status: Optional[str] = None) -> list[OrderORM]:
        q = select(OrderORM).where(OrderORM.symbol == symbol)
        if status:
            q = q.where(OrderORM.status == status)
        result = await self._s.execute(q.order_by(OrderORM.created_at.desc()))
        return list(result.scalars().all())

    async def list_today(self, strategy_id: Optional[str] = None) -> list[OrderORM]:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        q = select(OrderORM).where(OrderORM.submitted_at >= today_start)
        if strategy_id:
            q = q.where(OrderORM.strategy_id == strategy_id)
        result = await self._s.execute(q.order_by(OrderORM.submitted_at.asc()))
        return list(result.scalars().all())


# ------------------------------------------------------------------ #
# Fill repository
# ------------------------------------------------------------------ #

class FillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, orm: FillORM) -> None:
        self._s.add(orm)

    async def list_by_order(self, order_id: str) -> list[FillORM]:
        result = await self._s.execute(
            select(FillORM).where(FillORM.order_id == order_id)
            .order_by(FillORM.timestamp.asc())
        )
        return list(result.scalars().all())

    async def list_today(self, strategy_id: Optional[str] = None) -> list[FillORM]:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        q = select(FillORM).where(FillORM.timestamp >= today_start)
        if strategy_id:
            q = q.where(FillORM.strategy_id == strategy_id)
        result = await self._s.execute(q.order_by(FillORM.timestamp.asc()))
        return list(result.scalars().all())

    async def gross_pnl_today(self) -> Decimal:
        """Return sum of (qty * price * side_sign) for today's fills."""
        fills = await self.list_today()
        total = Decimal("0")
        for f in fills:
            sign = Decimal("1") if f.side == "sell" else Decimal("-1")
            total += sign * f.qty * f.price
        return total


# ------------------------------------------------------------------ #
# Position repository
# ------------------------------------------------------------------ #

class PositionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self,
        symbol: str,
        qty: Decimal,
        avg_entry_price: Decimal,
        strategy_id: Optional[str] = None,
    ) -> PositionORM:
        result = await self._s.execute(
            select(PositionORM).where(PositionORM.symbol == symbol)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.qty = qty
            existing.avg_entry_price = avg_entry_price
            if strategy_id:
                existing.strategy_id = strategy_id
            return existing
        else:
            orm = PositionORM(
                symbol=symbol,
                qty=qty,
                avg_entry_price=avg_entry_price,
                strategy_id=strategy_id,
                opened_at=datetime.now(timezone.utc),
            )
            self._s.add(orm)
            return orm

    async def delete(self, symbol: str) -> int:
        result = await self._s.execute(
            select(PositionORM).where(PositionORM.symbol == symbol)
        )
        existing = result.scalar_one_or_none()
        if existing:
            await self._s.delete(existing)
            return 1
        return 0

    async def list_open(self) -> list[PositionORM]:
        result = await self._s.execute(
            select(PositionORM).where(PositionORM.qty != Decimal("0"))
        )
        return list(result.scalars().all())


# ------------------------------------------------------------------ #
# RiskEvent repository
# ------------------------------------------------------------------ #

class RiskEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(
        self,
        event_type: str,
        symbol: Optional[str] = None,
        strategy_id: Optional[str] = None,
        reason: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> RiskEventORM:
        orm = RiskEventORM(
            event_type=event_type,
            symbol=symbol,
            strategy_id=strategy_id,
            reason=reason,
            details=details or {},
            timestamp=datetime.now(timezone.utc),
        )
        self._s.add(orm)
        return orm

    async def list_today(self) -> list[RiskEventORM]:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result = await self._s.execute(
            select(RiskEventORM)
            .where(RiskEventORM.timestamp >= today_start)
            .order_by(RiskEventORM.timestamp.asc())
        )
        return list(result.scalars().all())

    async def count_today_by_type(self) -> dict[str, int]:
        events = await self.list_today()
        counts: dict[str, int] = {}
        for e in events:
            counts[e.event_type] = counts.get(e.event_type, 0) + 1
        return counts


# ------------------------------------------------------------------ #
# DailyReport repository
# ------------------------------------------------------------------ #

class DailyReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, orm: DailyReportORM) -> None:
        self._s.add(orm)

    async def get_by_date(
        self,
        report_date: date,
        strategy_id: Optional[str] = None,
    ) -> Optional[DailyReportORM]:
        date_str = report_date.isoformat()
        q = select(DailyReportORM).where(DailyReportORM.date == date_str)
        if strategy_id:
            q = q.where(DailyReportORM.strategy_id == strategy_id)
        result = await self._s.execute(q)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        report_date: date,
        strategy_id: Optional[str],
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        gross_pnl: Decimal,
        net_pnl: Decimal,
        total_commission: Decimal,
        blocked_trades: int,
    ) -> DailyReportORM:
        existing = await self.get_by_date(report_date, strategy_id)
        if existing:
            existing.total_trades = total_trades
            existing.winning_trades = winning_trades
            existing.losing_trades = losing_trades
            existing.gross_pnl = gross_pnl
            existing.net_pnl = net_pnl
            existing.total_commission = total_commission
            existing.blocked_trades = blocked_trades
            return existing
        else:
            orm = DailyReportORM(
                date=report_date.isoformat(),
                strategy_id=strategy_id,
                total_trades=total_trades,
                winning_trades=winning_trades,
                losing_trades=losing_trades,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                total_commission=total_commission,
                blocked_trades=blocked_trades,
            )
            self._s.add(orm)
            return orm


# ------------------------------------------------------------------ #
# Audit repository
# ------------------------------------------------------------------ #

class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def log(
        self,
        action: str,
        actor: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> AuditEventORM:
        orm = AuditEventORM(
            action=action,
            actor=actor,
            details=details or {},
            timestamp=datetime.now(timezone.utc),
        )
        self._s.add(orm)
        return orm
