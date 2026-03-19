"""Tests for strategy framework and portfolio tracker."""
from __future__ import annotations

from decimal import Decimal

import pytest

from packages.core.enums import OrderSide, SignalDirection, StrategyState
from packages.core.models import StrategyConfig
from packages.strategies.registry import STRATEGY_REGISTRY, list_strategies, load_strategy
from tests.fixtures.factories import make_bar, make_bars, make_config, make_fill


# ------------------------------------------------------------------ #
# Registry
# ------------------------------------------------------------------ #
def test_registry_has_all_strategies():
    names = list_strategies()
    assert "MomentumBreakoutStrategy" in names
    assert "VWAPReversionStrategy" in names
    assert "OpeningRangeBreakoutStrategy" in names
    assert "RelativeVolumeMomentumStrategy" in names


def test_load_unknown_strategy_raises():
    cfg = make_config("UnknownStrategy")
    with pytest.raises(ValueError, match="Unknown strategy"):
        load_strategy(cfg)


def test_load_valid_strategy():
    cfg = make_config("MomentumBreakoutStrategy")
    strategy = load_strategy(cfg)
    assert strategy is not None
    assert strategy.config.cls_name == "MomentumBreakoutStrategy"


# ------------------------------------------------------------------ #
# Base strategy helpers
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_strategy_starts_idle():
    cfg = make_config("MomentumBreakoutStrategy")
    s = load_strategy(cfg)
    assert s.state == StrategyState.idle


@pytest.mark.asyncio
async def test_prepare_activates_strategy():
    cfg = make_config("MomentumBreakoutStrategy")
    cfg.symbols = ["SPY"]
    s = load_strategy(cfg)
    bars = make_bars("SPY", n=50)
    await s.prepare({"SPY": bars})
    assert s.state == StrategyState.active


@pytest.mark.asyncio
async def test_insufficient_bars_returns_none():
    cfg = make_config("MomentumBreakoutStrategy")
    cfg.symbols = ["SPY"]
    s = load_strategy(cfg)
    bar = make_bar("SPY", close=450.0)
    signal = await s.on_bar(bar)
    assert signal is None


@pytest.mark.asyncio
async def test_sma_computed_correctly():
    cfg = make_config("MomentumBreakoutStrategy")
    cfg.symbols = ["SPY"]
    s = load_strategy(cfg)
    bars = [make_bar("SPY", close=float(100 + i)) for i in range(10)]
    for b in bars:
        s._push_bar(b)
    sma = s._compute_sma("SPY", 5)
    assert sma is not None
    # Last 5 closes: 105,106,107,108,109 → avg = 107
    assert sma == Decimal("107")


@pytest.mark.asyncio
async def test_relative_volume_returns_one_with_insufficient_bars():
    cfg = make_config("MomentumBreakoutStrategy")
    cfg.symbols = ["SPY"]
    s = load_strategy(cfg)
    bar = make_bar("SPY")
    s._push_bar(bar)
    rvol = s._relative_volume("SPY")
    assert rvol == Decimal("1")


# ------------------------------------------------------------------ #
# Momentum Breakout
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_momentum_breakout_signal_on_breakout():
    from packages.strategies.momentum_breakout import MomentumBreakoutStrategy

    cfg = make_config("MomentumBreakoutStrategy")
    cfg.symbols = ["SPY"]
    cfg.params = {"min_bars": 30, "breakout_period": 20, "rvol_threshold": 0.1}
    s = MomentumBreakoutStrategy(cfg)

    # Build history: 30 bars at ~450, then a breakout bar
    bars = make_bars("SPY", n=35, start_price=450.0)
    await s.prepare({"SPY": bars})

    # Breakout bar: close well above previous high
    breakout_bar = make_bar("SPY", close=480.0, volume=500_000.0)
    signal = await s.on_bar(breakout_bar)
    assert signal is not None
    assert signal.direction == SignalDirection.long
    assert signal.stop_price < breakout_bar.close


@pytest.mark.asyncio
async def test_momentum_breakout_no_signal_below_high():
    from packages.strategies.momentum_breakout import MomentumBreakoutStrategy

    cfg = make_config("MomentumBreakoutStrategy")
    cfg.symbols = ["SPY"]
    cfg.params = {"min_bars": 30, "breakout_period": 20, "rvol_threshold": 0.1}
    s = MomentumBreakoutStrategy(cfg)

    bars = make_bars("SPY", n=35, start_price=450.0)
    await s.prepare({"SPY": bars})

    # Non-breakout bar: close well below previous high
    no_signal_bar = make_bar("SPY", close=400.0, volume=100_000.0)
    signal = await s.on_bar(no_signal_bar)
    assert signal is None


# ------------------------------------------------------------------ #
# VWAP Reversion
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_vwap_strategy_needs_enough_bars():
    from packages.strategies.vwap_reversion import VWAPReversionStrategy

    cfg = make_config("VWAPReversionStrategy")
    cfg.symbols = ["SPY"]
    s = VWAPReversionStrategy(cfg)

    bar = make_bar("SPY", close=450.0)
    signal = await s.on_bar(bar)
    assert signal is None


# ------------------------------------------------------------------ #
# Portfolio Tracker
# ------------------------------------------------------------------ #
def test_portfolio_tracker_fill_updates_position():
    from workers.portfolio.tracker import PortfolioTracker

    tracker = PortfolioTracker(initial_cash=Decimal("100000"))
    fill = make_fill(symbol="SPY", side=OrderSide.buy, qty=10.0, price=450.0)
    tracker.on_fill(fill)
    pos = tracker.get_position("SPY")
    assert pos is not None
    assert pos.qty == Decimal("10")
    assert pos.avg_entry_price == pytest.approx(float(fill.price), rel=0.01)


def test_portfolio_tracker_realized_pnl_on_close():
    from workers.portfolio.tracker import PortfolioTracker

    tracker = PortfolioTracker(initial_cash=Decimal("100000"))
    buy_fill = make_fill(symbol="SPY", side=OrderSide.buy, qty=10.0, price=450.0)
    tracker.on_fill(buy_fill)

    sell_fill = make_fill(symbol="SPY", side=OrderSide.sell, qty=10.0, price=460.0)
    tracker.on_fill(sell_fill)

    # PnL = 10 * (460 - 450) = 100, minus 2 commissions
    assert tracker.realized_pnl > Decimal("0")


def test_portfolio_tracker_equity_increases_with_unrealized():
    from workers.portfolio.tracker import PortfolioTracker

    tracker = PortfolioTracker(initial_cash=Decimal("100000"))
    fill = make_fill(symbol="SPY", side=OrderSide.buy, qty=10.0, price=450.0)
    tracker.on_fill(fill)

    bar = make_bar("SPY", close=460.0)
    tracker.on_price_update(bar)

    assert tracker.equity > Decimal("100000") - Decimal("1")  # approximate


def test_portfolio_tracker_daily_report():
    from workers.portfolio.tracker import PortfolioTracker

    tracker = PortfolioTracker(initial_cash=Decimal("100000"))
    report = tracker.generate_daily_report(strategy_id="test")
    assert report.total_trades == 0
    assert report.strategy_id == "test"
