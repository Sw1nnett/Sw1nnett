"""Redis client with typed helpers for trading state."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import redis.asyncio as aioredis


class CacheKeys:
    KILL_SWITCH = "trading:kill_switch"
    DAILY_LOSS = "trading:daily_loss"
    DAILY_HIGH_EQUITY = "trading:daily_high_equity"

    @staticmethod
    def order_dedup(client_order_id: str) -> str:
        return f"trading:order_dedup:{client_order_id}"

    @staticmethod
    def wash_cooldown(symbol: str) -> str:
        return f"trading:wash_cooldown:{symbol}"

    @staticmethod
    def flip_cooldown(symbol: str) -> str:
        return f"trading:flip_cooldown:{symbol}"

    @staticmethod
    def data_freshness(symbol: str) -> str:
        return f"trading:data_ts:{symbol}"


class RedisCache:
    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(self._url, decode_responses=True)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _r(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("RedisCache not connected")
        return self._client

    # ------------------------------------------------------------------ #
    # Kill switch
    # ------------------------------------------------------------------ #
    async def activate_kill_switch(self, reason: str = "manual") -> None:
        await self._r().set(CacheKeys.KILL_SWITCH, reason)

    async def deactivate_kill_switch(self) -> None:
        await self._r().delete(CacheKeys.KILL_SWITCH)

    async def is_kill_switch_active(self) -> bool:
        val = await self._r().exists(CacheKeys.KILL_SWITCH)
        return bool(val)

    async def get_kill_switch_reason(self) -> Optional[str]:
        return await self._r().get(CacheKeys.KILL_SWITCH)

    # ------------------------------------------------------------------ #
    # Daily PnL tracking
    # ------------------------------------------------------------------ #
    async def get_daily_loss(self) -> float:
        val = await self._r().get(CacheKeys.DAILY_LOSS)
        return float(val) if val else 0.0

    async def update_daily_loss(self, realized_pnl: float) -> None:
        # Store negative value as "loss"
        await self._r().set(CacheKeys.DAILY_LOSS, str(realized_pnl))

    async def get_daily_high_equity(self) -> float:
        val = await self._r().get(CacheKeys.DAILY_HIGH_EQUITY)
        return float(val) if val else 0.0

    async def update_daily_high_equity(self, equity: float) -> None:
        current = await self.get_daily_high_equity()
        if equity > current:
            await self._r().set(CacheKeys.DAILY_HIGH_EQUITY, str(equity))

    # ------------------------------------------------------------------ #
    # Order deduplication
    # ------------------------------------------------------------------ #
    async def try_reserve_order(self, client_order_id: str, ttl: int = 86400) -> bool:
        """Return True if the order slot was reserved (new), False if duplicate."""
        result = await self._r().set(
            CacheKeys.order_dedup(client_order_id), "1", nx=True, ex=ttl
        )
        return result is True

    # ------------------------------------------------------------------ #
    # Cooldowns
    # ------------------------------------------------------------------ #
    async def set_wash_cooldown(self, symbol: str, ttl_seconds: int) -> None:
        await self._r().set(CacheKeys.wash_cooldown(symbol), "1", ex=ttl_seconds)

    async def has_wash_cooldown(self, symbol: str) -> bool:
        return bool(await self._r().exists(CacheKeys.wash_cooldown(symbol)))

    async def set_flip_cooldown(self, symbol: str, ttl_seconds: int) -> None:
        await self._r().set(CacheKeys.flip_cooldown(symbol), "1", ex=ttl_seconds)

    async def has_flip_cooldown(self, symbol: str) -> bool:
        return bool(await self._r().exists(CacheKeys.flip_cooldown(symbol)))

    # ------------------------------------------------------------------ #
    # Data freshness
    # ------------------------------------------------------------------ #
    async def update_data_timestamp(self, symbol: str) -> None:
        await self._r().set(CacheKeys.data_freshness(symbol), str(time.time()))

    async def get_data_age_seconds(self, symbol: str) -> float:
        ts = await self._r().get(CacheKeys.data_freshness(symbol))
        if ts is None:
            return float("inf")
        return time.time() - float(ts)

    # ------------------------------------------------------------------ #
    # Generic get/set
    # ------------------------------------------------------------------ #
    async def get(self, key: str) -> Optional[str]:
        return await self._r().get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        await self._r().set(key, value, ex=ex)

    async def delete(self, key: str) -> None:
        await self._r().delete(key)

    async def reset_daily_state(self) -> None:
        """Clear daily accumulators — called at session start."""
        await self._r().delete(CacheKeys.DAILY_LOSS)
        await self._r().delete(CacheKeys.DAILY_HIGH_EQUITY)


@asynccontextmanager
async def redis_lock(
    cache: RedisCache, key: str, ttl: int = 30
) -> AsyncGenerator[bool, None]:
    """Simple advisory lock using Redis SET NX."""
    lock_key = f"lock:{key}"
    acquired = await cache._r().set(lock_key, "1", nx=True, ex=ttl)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            await cache._r().delete(lock_key)
