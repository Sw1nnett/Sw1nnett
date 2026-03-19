"""Central configuration via Pydantic Settings."""
from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.core.enums import BrokerName, TradingMode


class RiskConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore", populate_by_name=True)

    max_daily_loss_pct: Decimal = Field(Decimal("0.02"), alias="MAX_DAILY_LOSS_PCT")
    max_daily_drawdown_pct: Decimal = Field(Decimal("0.03"), alias="MAX_DAILY_DRAWDOWN_PCT")
    max_position_size_pct: Decimal = Field(Decimal("0.10"), alias="MAX_POSITION_SIZE_PCT")
    max_open_positions: int = Field(5, alias="MAX_OPEN_POSITIONS")
    max_gross_exposure_pct: Decimal = Field(Decimal("0.90"), alias="MAX_GROSS_EXPOSURE_PCT")
    max_net_exposure_pct: Decimal = Field(Decimal("0.50"), alias="MAX_NET_EXPOSURE_PCT")
    risk_per_trade_pct: Decimal = Field(Decimal("0.005"), alias="RISK_PER_TRADE_PCT")
    spread_limit_bps: Decimal = Field(Decimal("30"), alias="SPREAD_LIMIT_BPS")
    stale_data_seconds: int = Field(60, alias="STALE_DATA_SECONDS")
    wash_cooldown_seconds: int = Field(300, alias="WASH_COOLDOWN_SECONDS")
    flip_cooldown_seconds: int = Field(120, alias="FLIP_COOLDOWN_SECONDS")
    session_cutoff_minutes: int = Field(15, alias="SESSION_CUTOFF_MINUTES")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # Runtime
    env: str = Field("development", alias="ENV")
    trading_mode: TradingMode = Field(TradingMode.paper, alias="TRADING_MODE")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_dir: str = Field("logs", alias="LOG_DIR")

    # Live trading gates
    live_trading_enabled: bool = Field(False, alias="LIVE_TRADING_ENABLED")
    live_confirmation_token: Optional[str] = Field(None, alias="LIVE_CONFIRMATION_TOKEN")

    # Broker
    broker: BrokerName = Field(BrokerName.simulator, alias="BROKER")
    alpaca_api_key: Optional[str] = Field(None, alias="ALPACA_API_KEY")
    alpaca_api_secret: Optional[str] = Field(None, alias="ALPACA_API_SECRET")
    alpaca_base_url: str = Field(
        "https://paper-api.alpaca.markets", alias="ALPACA_BASE_URL"
    )

    # Database
    database_url: str = Field(
        "postgresql+asyncpg://trader:trader@localhost:5432/tradingbot",
        alias="DATABASE_URL",
    )
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")

    # API
    api_host: str = Field("0.0.0.0", alias="API_HOST")
    api_port: int = Field(8000, alias="API_PORT")
    api_secret_key: str = Field("changeme-in-production", alias="API_SECRET_KEY")
    api_allowed_ips: str = Field("127.0.0.1,::1", alias="API_ALLOWED_IPS")

    # Symbols
    symbol_allowlist: str = Field(
        "SPY,QQQ,IWM,AAPL,MSFT,NVDA,AMD,TSLA,META", alias="SYMBOL_ALLOWLIST"
    )

    # Risk
    risk: RiskConfig = Field(default_factory=RiskConfig)

    # Metrics
    metrics_port: int = Field(9090, alias="METRICS_PORT")

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _lower_mode(cls, v: str) -> str:
        return str(v).lower()

    @model_validator(mode="after")
    def validate_live_mode_gates(self) -> "Settings":
        if self.trading_mode in {TradingMode.live_guarded, TradingMode.live_dry_run}:
            errors: list[str] = []
            if self.env != "live":
                errors.append("ENV must be 'live'")
            if not self.live_trading_enabled:
                errors.append("LIVE_TRADING_ENABLED must be true")
            if not self.live_confirmation_token:
                errors.append("LIVE_CONFIRMATION_TOKEN must be set")
            if errors:
                raise ValueError(
                    f"Live trading requires: {'; '.join(errors)}"
                )
        return self

    @property
    def allowed_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.symbol_allowlist.split(",") if s.strip()]

    @property
    def allowed_ips(self) -> list[str]:
        return [ip.strip() for ip in self.api_allowed_ips.split(",") if ip.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Clear the cached settings instance (for testing)."""
    get_settings.cache_clear()
