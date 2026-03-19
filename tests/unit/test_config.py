"""Tests for configuration and settings."""
from __future__ import annotations

import os
import pytest

from packages.core.config import RiskConfig, Settings, get_settings, reset_settings
from packages.core.enums import TradingMode


def test_default_trading_mode_is_paper():
    s = Settings()
    assert s.trading_mode == TradingMode.paper


def test_symbol_allowlist_parsed():
    s = Settings()
    symbols = s.allowed_symbols
    assert "SPY" in symbols
    assert "QQQ" in symbols


def test_allowed_ips_parsed():
    s = Settings()
    ips = s.allowed_ips
    assert isinstance(ips, list)


def test_live_mode_requires_env_live(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live_guarded")
    monkeypatch.setenv("ENV", "development")  # wrong env
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_CONFIRMATION_TOKEN", "token123")
    reset_settings()
    with pytest.raises(Exception, match="ENV must be"):
        Settings()
    reset_settings()


def test_live_mode_requires_enabled_flag(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live_guarded")
    monkeypatch.setenv("ENV", "live")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_CONFIRMATION_TOKEN", "token123")
    reset_settings()
    with pytest.raises(Exception, match="LIVE_TRADING_ENABLED"):
        Settings()
    reset_settings()


def test_live_mode_requires_confirmation_token(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live_guarded")
    monkeypatch.setenv("ENV", "live")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_CONFIRMATION_TOKEN", "")
    reset_settings()
    with pytest.raises(Exception, match="LIVE_CONFIRMATION_TOKEN"):
        Settings()
    reset_settings()


def test_live_mode_all_gates_pass(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live_guarded")
    monkeypatch.setenv("ENV", "live")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_CONFIRMATION_TOKEN", "token123")
    reset_settings()
    s = Settings()
    assert s.trading_mode == TradingMode.live_guarded
    reset_settings()


def test_risk_config_defaults():
    r = RiskConfig()
    assert r.max_daily_loss_pct > 0
    assert r.max_open_positions > 0


def test_get_settings_cached():
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_reset_settings():
    s1 = get_settings()
    reset_settings()
    s2 = get_settings()
    assert s1 is not s2
