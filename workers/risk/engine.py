"""Pre-trade risk engine with 13 sequential checks."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from packages.core.config import get_settings
from packages.core.enums import RiskEvent
from packages.core.models import Order, Position, RiskCheckResult, Signal
from packages.data.redis_client import RedisCache
from packages.observability.logging import get_logger
from packages.observability.metrics import kill_switch_activations_total, risk_blocks_total


log = get_logger(__name__)


class RiskEngine:
    """Stateful pre-trade risk engine."""

    def __init__(
        self,
        cache: RedisCache,
        account_equity: Decimal = Decimal("100000"),
    ) -> None:
        self._cache = cache
        self._account_equity = account_equity
        self._open_positions: dict[str, Position] = {}
        self._daily_realized_pnl: Decimal = Decimal("0")

    # ------------------------------------------------------------------ #
    # State updates (called from portfolio tracker)
    # ------------------------------------------------------------------ #
    def update_account_equity(self, equity: Decimal) -> None:
        self._account_equity = equity

    def update_positions(self, positions: dict[str, Position]) -> None:
        self._open_positions = positions

    def update_daily_pnl(self, pnl: Decimal) -> None:
        self._daily_realized_pnl = pnl

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    async def check_pre_trade(
        self,
        signal: Signal,
        proposed_order: Order,
    ) -> RiskCheckResult:
        """Run all 13 checks in order. Return first failure."""
        checks = [
            self._check_kill_switch,
            self._check_daily_loss,
            self._check_daily_drawdown,
            self._check_stale_data,
            self._check_duplicate_order,
            self._check_wash_cooldown,
            self._check_flip_cooldown,
            self._check_spread_guard,
            self._check_max_position_size,
            self._check_max_open_positions,
            self._check_gross_exposure,
            self._check_net_exposure,
            self._check_session_cutoff,
        ]
        for check in checks:
            result = await check(signal, proposed_order)
            if not result.passed:
                risk_blocks_total.labels(reason=result.risk_event.value).inc()
                log.warning(
                    "risk_block",
                    risk_event=result.risk_event.value,
                    reason=result.blocked_reason,
                    symbol=signal.symbol,
                    strategy=signal.strategy_id,
                )
                return result
        return RiskCheckResult.ok()

    # ------------------------------------------------------------------ #
    # Kill switch
    # ------------------------------------------------------------------ #
    async def trigger_kill_switch(self, reason: str = "manual", actor: str = "system") -> None:
        await self._cache.activate_kill_switch(reason)
        kill_switch_activations_total.inc()
        log.critical("kill_switch_activated", kill_reason=reason, kill_actor=actor)

    async def reset_kill_switch(self, actor: str = "operator") -> None:
        await self._cache.deactivate_kill_switch()
        log.warning("kill_switch_reset", reset_actor=actor)

    # ------------------------------------------------------------------ #
    # Individual checks
    # ------------------------------------------------------------------ #
    async def _check_kill_switch(self, signal: Signal, order: Order) -> RiskCheckResult:
        if await self._cache.is_kill_switch_active():
            reason = await self._cache.get_kill_switch_reason()
            return RiskCheckResult.block(
                f"Kill switch active: {reason}", RiskEvent.kill_switch
            )
        return RiskCheckResult.ok()

    async def _check_daily_loss(self, signal: Signal, order: Order) -> RiskCheckResult:
        settings = get_settings()
        limit = self._account_equity * settings.risk.max_daily_loss_pct
        if self._daily_realized_pnl < -limit:
            return RiskCheckResult.block(
                f"Daily loss {float(self._daily_realized_pnl):.2f} exceeds limit {float(-limit):.2f}",
                RiskEvent.daily_loss_limit,
                daily_pnl=float(self._daily_realized_pnl),
                limit=float(limit),
            )
        return RiskCheckResult.ok()

    async def _check_daily_drawdown(self, signal: Signal, order: Order) -> RiskCheckResult:
        settings = get_settings()
        high_equity = await self._cache.get_daily_high_equity()
        if high_equity <= 0:
            return RiskCheckResult.ok()
        drawdown = (Decimal(str(high_equity)) - self._account_equity) / Decimal(str(high_equity))
        limit = settings.risk.max_daily_drawdown_pct
        if drawdown > limit:
            return RiskCheckResult.block(
                f"Daily drawdown {float(drawdown):.2%} exceeds limit {float(limit):.2%}",
                RiskEvent.daily_drawdown,
                drawdown=float(drawdown),
            )
        return RiskCheckResult.ok()

    async def _check_stale_data(self, signal: Signal, order: Order) -> RiskCheckResult:
        settings = get_settings()
        age = await self._cache.get_data_age_seconds(signal.symbol)
        if age > settings.risk.stale_data_seconds:
            return RiskCheckResult.block(
                f"Data for {signal.symbol} is {age:.0f}s stale (limit {settings.risk.stale_data_seconds}s)",
                RiskEvent.stale_data,
                age_seconds=age,
            )
        return RiskCheckResult.ok()

    async def _check_duplicate_order(self, signal: Signal, order: Order) -> RiskCheckResult:
        reserved = await self._cache.try_reserve_order(order.client_order_id)
        if not reserved:
            return RiskCheckResult.block(
                f"Duplicate order: {order.client_order_id}",
                RiskEvent.duplicate_order,
                client_order_id=order.client_order_id,
            )
        return RiskCheckResult.ok()

    async def _check_wash_cooldown(self, signal: Signal, order: Order) -> RiskCheckResult:
        if await self._cache.has_wash_cooldown(signal.symbol):
            return RiskCheckResult.block(
                f"Wash cooldown active for {signal.symbol}",
                RiskEvent.wash_cooldown,
            )
        return RiskCheckResult.ok()

    async def _check_flip_cooldown(self, signal: Signal, order: Order) -> RiskCheckResult:
        if await self._cache.has_flip_cooldown(signal.symbol):
            return RiskCheckResult.block(
                f"Flip cooldown active for {signal.symbol}",
                RiskEvent.flip_cooldown,
            )
        return RiskCheckResult.ok()

    async def _check_spread_guard(self, signal: Signal, order: Order) -> RiskCheckResult:
        settings = get_settings()
        if signal.entry_price and order.limit_price:
            spread_bps = abs(order.limit_price - signal.entry_price) / signal.entry_price * 10000
            if spread_bps > settings.risk.spread_limit_bps:
                return RiskCheckResult.block(
                    f"Spread {float(spread_bps):.1f} bps exceeds limit {float(settings.risk.spread_limit_bps):.1f}",
                    RiskEvent.spread_guard,
                    spread_bps=float(spread_bps),
                )
        return RiskCheckResult.ok()

    async def _check_max_position_size(self, signal: Signal, order: Order) -> RiskCheckResult:
        settings = get_settings()
        if not signal.entry_price or signal.entry_price <= 0:
            return RiskCheckResult.ok()
        notional = order.qty * signal.entry_price
        max_notional = self._account_equity * settings.risk.max_position_size_pct
        if notional > max_notional:
            return RiskCheckResult.block(
                f"Position size ${float(notional):.2f} exceeds max ${float(max_notional):.2f}",
                RiskEvent.max_position_size,
                notional=float(notional),
                max_notional=float(max_notional),
            )
        return RiskCheckResult.ok()

    async def _check_max_open_positions(self, signal: Signal, order: Order) -> RiskCheckResult:
        settings = get_settings()
        open_count = sum(1 for p in self._open_positions.values() if p.qty != 0)
        if open_count >= settings.risk.max_open_positions:
            return RiskCheckResult.block(
                f"Open positions {open_count} >= limit {settings.risk.max_open_positions}",
                RiskEvent.max_open_positions,
                open_count=open_count,
            )
        return RiskCheckResult.ok()

    async def _check_gross_exposure(self, signal: Signal, order: Order) -> RiskCheckResult:
        settings = get_settings()
        gross = sum(abs(p.qty) * p.current_price for p in self._open_positions.values())
        max_gross = self._account_equity * settings.risk.max_gross_exposure_pct
        if gross > max_gross:
            return RiskCheckResult.block(
                f"Gross exposure ${float(gross):.2f} exceeds limit ${float(max_gross):.2f}",
                RiskEvent.gross_exposure,
                gross=float(gross),
            )
        return RiskCheckResult.ok()

    async def _check_net_exposure(self, signal: Signal, order: Order) -> RiskCheckResult:
        settings = get_settings()
        net = sum(p.qty * p.current_price for p in self._open_positions.values())
        max_net = self._account_equity * settings.risk.max_net_exposure_pct
        if abs(net) > max_net:
            return RiskCheckResult.block(
                f"Net exposure ${float(net):.2f} exceeds limit ${float(max_net):.2f}",
                RiskEvent.net_exposure,
                net=float(net),
            )
        return RiskCheckResult.ok()

    async def _check_session_cutoff(self, signal: Signal, order: Order) -> RiskCheckResult:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        # 4:00 PM ET = 20:00 UTC (approximate; ignores DST for simplicity)
        close_hour_utc = 20
        cutoff_minutes = settings.risk.session_cutoff_minutes
        cutoff_dt = now.replace(
            hour=close_hour_utc, minute=0, second=0, microsecond=0
        )
        from datetime import timedelta
        cutoff_dt -= timedelta(minutes=cutoff_minutes)
        if now >= cutoff_dt:
            return RiskCheckResult.block(
                f"Session cutoff: no new orders within {cutoff_minutes} min of close",
                RiskEvent.session_cutoff,
            )
        return RiskCheckResult.ok()

    # ------------------------------------------------------------------ #
    # Monitoring
    # ------------------------------------------------------------------ #
    async def get_risk_summary(self) -> dict:
        open_count = sum(1 for p in self._open_positions.values() if p.qty != 0)
        gross = sum(abs(p.qty) * p.current_price for p in self._open_positions.values())
        net = sum(p.qty * p.current_price for p in self._open_positions.values())
        return {
            "kill_switch_active": await self._cache.is_kill_switch_active(),
            "kill_switch_reason": await self._cache.get_kill_switch_reason(),
            "daily_realized_pnl": float(self._daily_realized_pnl),
            "account_equity": float(self._account_equity),
            "open_positions": open_count,
            "gross_exposure": float(gross),
            "net_exposure": float(net),
        }
