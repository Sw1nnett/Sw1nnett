"""Integration tests — full end-to-end backtest runs."""
from __future__ import annotations

from decimal import Decimal

import pytest

from packages.backtest.analytics import compute_metrics, max_drawdown, profit_factor, win_rate
from packages.backtest.data_loader import generate_synthetic_bars
from packages.backtest.engine import BacktestEngine
from packages.backtest.tearsheet import build_tearsheet, export_all
from packages.core.models import StrategyConfig


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def spy_bars():
    return generate_synthetic_bars("SPY", n_bars=800, start_price=450.0, seed=42)


@pytest.fixture
def multi_bars():
    return {
        "SPY": generate_synthetic_bars("SPY", n_bars=600, start_price=450.0, seed=1),
        "QQQ": generate_synthetic_bars("QQQ", n_bars=600, start_price=380.0, seed=2),
    }


def _engine(strategy_cls: str = "MomentumBreakoutStrategy", **params) -> BacktestEngine:
    cfg = StrategyConfig(
        cls_name=strategy_cls,
        symbols=["SPY"],
        params=params or {"min_bars": 30, "breakout_period": 20, "rvol_threshold": 0.5},
    )
    return BacktestEngine(
        strategy_configs=[cfg],
        initial_capital=100_000.0,
        warmup_bars=40,
    )


# ------------------------------------------------------------------ #
# Smoke tests — just check it runs and produces sane output
# ------------------------------------------------------------------ #

def test_full_backtest_runs(spy_bars):
    engine = _engine()
    result = engine.run({"SPY": spy_bars})
    assert result is not None
    assert result.start_date <= result.end_date
    assert result.initial_capital == Decimal("100000")
    assert result.final_capital > 0


def test_equity_curve_populated(spy_bars):
    engine = _engine()
    result = engine.run({"SPY": spy_bars})
    # Should have equity point per test bar (after warmup)
    assert len(result.equity_curve) > 0
    # All equity values should be positive
    assert all(ep.equity > 0 for ep in result.equity_curve)


def test_trades_have_valid_pnl(spy_bars):
    engine = _engine()
    result = engine.run({"SPY": spy_bars})
    for trade in result.trades:
        assert trade.qty > 0
        assert trade.entry_price > 0
        assert trade.exit_price > 0
        assert trade.bars_held >= 0
        assert trade.exit_reason in {"target", "stop", "eod", "signal"}


def test_metrics_computable(spy_bars):
    engine = _engine()
    result = engine.run({"SPY": spy_bars})
    m = compute_metrics(result)
    # All scalar values should be finite numbers
    for k, v in m.items():
        if isinstance(v, float) and not isinstance(v, bool):
            assert not (v != v), f"NaN in metric: {k}"  # NaN check


def test_capital_conservation(spy_bars):
    """Final capital should be within reasonable bounds of initial."""
    engine = _engine()
    result = engine.run({"SPY": spy_bars})
    # Should not lose more than 50% or gain more than 500% (sanity check)
    ratio = float(result.final_capital / result.initial_capital)
    assert 0.5 <= ratio <= 5.0


# ------------------------------------------------------------------ #
# Multi-symbol
# ------------------------------------------------------------------ #

def test_multi_symbol_backtest(multi_bars):
    cfg = StrategyConfig(
        cls_name="MomentumBreakoutStrategy",
        symbols=["SPY", "QQQ"],
        params={"min_bars": 30, "breakout_period": 20, "rvol_threshold": 0.5},
    )
    engine = BacktestEngine(strategy_configs=[cfg], initial_capital=100_000.0, warmup_bars=40)
    result = engine.run(multi_bars)
    assert result is not None
    assert len(result.symbols) == 2
    # May have trades on both symbols
    symbols_traded = {t.symbol for t in result.trades}
    assert len(symbols_traded) >= 1


# ------------------------------------------------------------------ #
# Strategy variants
# ------------------------------------------------------------------ #

def test_vwap_reversion_backtest(spy_bars):
    cfg = StrategyConfig(
        cls_name="VWAPReversionStrategy",
        symbols=["SPY"],
        params={"min_bars": 30, "stddev_threshold": 0.5, "rvol_threshold": 0.3},
    )
    engine = BacktestEngine(strategy_configs=[cfg], initial_capital=100_000.0, warmup_bars=40)
    result = engine.run({"SPY": spy_bars})
    assert result is not None
    assert result.final_capital > 0


def test_orb_backtest(spy_bars):
    cfg = StrategyConfig(
        cls_name="OpeningRangeBreakoutStrategy",
        symbols=["SPY"],
        params={"orb_minutes": 5, "rvol_threshold": 0.3},
    )
    engine = BacktestEngine(strategy_configs=[cfg], initial_capital=100_000.0, warmup_bars=40)
    result = engine.run({"SPY": spy_bars})
    assert result is not None


def test_rvol_momentum_backtest(spy_bars):
    cfg = StrategyConfig(
        cls_name="RelativeVolumeMomentumStrategy",
        symbols=["SPY"],
        params={"min_bars": 25, "rvol_threshold": 0.8, "momentum_bars": 2},
    )
    engine = BacktestEngine(strategy_configs=[cfg], initial_capital=100_000.0, warmup_bars=40)
    result = engine.run({"SPY": spy_bars})
    assert result is not None


# ------------------------------------------------------------------ #
# Risk engine integration
# ------------------------------------------------------------------ #

def test_risk_blocks_recorded(spy_bars):
    """Blocked orders should be recorded in result."""
    engine = _engine()
    result = engine.run({"SPY": spy_bars})
    # All blocked orders have required fields
    for blocked in result.blocked_orders:
        assert "symbol" in blocked
        assert "reason" in blocked
        assert "event" in blocked


def test_kill_switch_stops_trading(spy_bars):
    """After kill switch fires, no more trades should be added."""
    engine = _engine()
    result = engine.run({"SPY": spy_bars})
    ks_events = [e for e in result.risk_events if e.event.value == "kill_switch"]
    if ks_events:
        ks_time = ks_events[0].timestamp
        post_ks_trades = [t for t in result.trades if t.entry_time > ks_time]
        assert len(post_ks_trades) == 0


# ------------------------------------------------------------------ #
# Tearsheet output
# ------------------------------------------------------------------ #

def test_tearsheet_builds(spy_bars):
    engine = _engine()
    result = engine.run({"SPY": spy_bars})
    sheet = build_tearsheet(result)
    assert "BACKTEST RESULTS" in sheet
    assert "CAPITAL" in sheet
    assert "TRADES" in sheet
    assert len(sheet) > 200


def test_export_all(spy_bars, tmp_path):
    engine = _engine()
    result = engine.run({"SPY": spy_bars})
    paths = export_all(result, tmp_path / "results")
    assert (tmp_path / "results" / "tearsheet.txt").exists()
    assert (tmp_path / "results" / "trades.csv").exists()
    assert (tmp_path / "results" / "equity_curve.csv").exists()
    assert (tmp_path / "results" / "metrics.csv").exists()


# ------------------------------------------------------------------ #
# Data loader
# ------------------------------------------------------------------ #

def test_synthetic_bars_are_valid(spy_bars):
    from packages.core.models import Bar
    for bar in spy_bars:
        assert bar.high >= bar.low
        assert bar.close >= bar.low
        assert bar.close <= bar.high
        assert bar.volume > 0


def test_save_and_load_csv(spy_bars, tmp_path):
    from packages.backtest.data_loader import load_csv, save_csv
    path = tmp_path / "SPY.csv"
    save_csv(spy_bars[:100], path)
    loaded = load_csv(path, "SPY")
    assert len(loaded) == 100
    assert loaded[0].symbol == "SPY"
    assert loaded[0].close == spy_bars[0].close
