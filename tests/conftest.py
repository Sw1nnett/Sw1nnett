"""Test configuration and fixtures."""
from __future__ import annotations

import os
import pytest

# Set safe test environment BEFORE any imports that trigger settings loading
_BASE_ENV = {
    "TRADING_MODE": "paper",
    "ENV": "development",
    "LIVE_TRADING_ENABLED": "false",
    "LIVE_CONFIRMATION_TOKEN": "",
    "BROKER": "simulator",
    "DATABASE_URL": "postgresql+asyncpg://trader:trader@localhost:5432/tradingbot_test",
    "REDIS_URL": "redis://localhost:6379/1",
    "API_SECRET_KEY": "test-secret-key",
    "LOG_LEVEL": "WARNING",
    "LOG_DIR": "/tmp/test_logs",
}

for k, v in _BASE_ENV.items():
    os.environ.setdefault(k, v)


@pytest.fixture(autouse=True)
def reset_config_cache(monkeypatch):
    """Isolate settings between tests by saving/restoring env vars."""
    saved = {k: os.environ.get(k) for k in _BASE_ENV}
    # Ensure base env is set
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)

    yield

    # Restore
    from packages.core.config import reset_settings
    reset_settings()
    for k, v in saved.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    reset_settings()
