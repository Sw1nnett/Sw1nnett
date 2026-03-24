"""Time-aware in-memory cache for backtesting — no Redis dependency."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


class BacktestCache:
    """
    Drop-in replacement for RedisCache during backtests.
    Uses bar timestamps instead of wall-clock time so cooldowns
    and freshness checks work correctly in replayed history.
    """

    def __init__(self) -> None:
        self._kill_switch = False
        self._kill_reason: Optional[str] = None
        self._daily_loss: float = 0.0
        self._daily_high_equity: float = 0.0
        self._order_ids: set[str] = set()
        # symbol -> expiry datetime (bar-time based)
        self._wash_expiry: dict[str, datetime] = {}
        self._flip_expiry: dict[str, datetime] = {}
        self._current_bar_time: datetime = datetime.now(timezone.utc)
        self._event_log: list[dict] = []

    # ------------------------------------------------------------------ #
    # Bar-time management
    # ------------------------------------------------------------------ #
    def advance_time(self, ts: datetime) -> None:
        """Called by BacktestEngine on each bar to advance simulated time."""
        self._current_bar_time = ts

    # ------------------------------------------------------------------ #
    # Kill switch
    # ------------------------------------------------------------------ #
    async def activate_kill_switch(self, reason: str = "manual") -> None:
        self._kill_switch = True
        self._kill_reason = reason
        self._event_log.append({"type": "kill_switch", "reason": reason, "ts": self._current_bar_time})

    async def deactivate_kill_switch(self) -> None:
        self._kill_switch = False
        self._kill_reason = None

    async def is_kill_switch_active(self) -> bool:
        return self._kill_switch

    async def get_kill_switch_reason(self) -> Optional[str]:
        return self._kill_reason

    # ------------------------------------------------------------------ #
    # Daily PnL
    # ------------------------------------------------------------------ #
    async def get_daily_loss(self) -> float:
        return self._daily_loss

    async def update_daily_loss(self, realized_pnl: float) -> None:
        self._daily_loss = realized_pnl

    async def get_daily_high_equity(self) -> float:
        return self._daily_high_equity

    async def update_daily_high_equity(self, equity: float) -> None:
        if equity > self._daily_high_equity:
            self._daily_high_equity = equity

    # ------------------------------------------------------------------ #
    # Order deduplication
    # ------------------------------------------------------------------ #
    async def try_reserve_order(self, client_order_id: str, ttl: int = 86400) -> bool:
        if client_order_id in self._order_ids:
            return False
        self._order_ids.add(client_order_id)
        return True

    # ------------------------------------------------------------------ #
    # Cooldowns (bar-time aware)
    # ------------------------------------------------------------------ #
    async def set_wash_cooldown(self, symbol: str, ttl_seconds: int) -> None:
        self._wash_expiry[symbol] = self._current_bar_time + timedelta(seconds=ttl_seconds)

    async def has_wash_cooldown(self, symbol: str) -> bool:
        expiry = self._wash_expiry.get(symbol)
        if expiry is None:
            return False
        return self._current_bar_time < expiry

    async def set_flip_cooldown(self, symbol: str, ttl_seconds: int) -> None:
        self._flip_expiry[symbol] = self._current_bar_time + timedelta(seconds=ttl_seconds)

    async def has_flip_cooldown(self, symbol: str) -> bool:
        expiry = self._flip_expiry.get(symbol)
        if expiry is None:
            return False
        return self._current_bar_time < expiry

    # ------------------------------------------------------------------ #
    # Data freshness — always "fresh" during backtest replay
    # ------------------------------------------------------------------ #
    async def update_data_timestamp(self, symbol: str) -> None:
        pass

    async def get_data_age_seconds(self, symbol: str) -> float:
        return 0.0  # Replayed data is always fresh

    # ------------------------------------------------------------------ #
    # Daily reset
    # ------------------------------------------------------------------ #
    async def reset_daily_state(self) -> None:
        self._daily_loss = 0.0
        self._daily_high_equity = 0.0
        self._kill_switch = False
        self._kill_reason = None

    # ------------------------------------------------------------------ #
    # Generic (unused in backtest but satisfies interface)
    # ------------------------------------------------------------------ #
    async def get(self, key: str) -> Optional[str]:
        return None

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass
