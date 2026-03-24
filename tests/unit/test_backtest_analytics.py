"""Tests for backtest analytics."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from packages.backtest.analytics import (
    annualized_return_pct,
    avg_loss,
    avg_win,
    compute_metrics,
    exit_reason_breakdown,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    total_return_pct,
    win_rate,
)
from packages.backtest.engine import BacktestResult, EquityPoint, TradeRecord


def _make_result(
    initial: float = 100_000.0,
    final: float = 110_000.0,
    trades: list[TradeRecord] | None = None,
    equity_curve: list[EquityPoint] | None = None,
    start_date: date = date(2024, 1, 2),
    end_date: date = date(2024, 3, 29),
) -> BacktestResult:
    return BacktestResult(
        strategy_names=["TestStrategy"],
        symbols=["SPY"],
        start_date=start_date,
        end_date=end_date,
        initial_capital=Decimal(str(initial)),
        final_capital=Decimal(str(final)),
        trades=trades or [],
        equity_curve=equity_curve or [],
    )


def _make_trade(net_pnl: float, exit_reason: str = "target", bars_held: int = 10) -> TradeRecord:
    ts = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
    entry_price = Decimal("450")
    qty = Decimal("10")
    pnl = Decimal(str(net_pnl))
    gross = pnl + Decimal("0.10")  # add back tiny commission
    exit_price = entry_price + gross / qty
    return TradeRecord(
        symbol="SPY",
        strategy_id="test",
        side="long",
        entry_time=ts,
        exit_time=ts,
        entry_price=entry_price,
        exit_price=exit_price,
        qty=qty,
        gross_pnl=gross,
        commission=Decimal("0.10"),
        exit_reason=exit_reason,
        bars_held=bars_held,
    )


def _make_equity_curve(daily_returns: list[float], start: float = 100_000.0) -> list[EquityPoint]:
    curve = []
    eq = start
    ts = datetime(2024, 1, 2, 20, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    for r in daily_returns:
        eq *= (1 + r)
        curve.append(EquityPoint(timestamp=ts, equity=Decimal(str(eq)), cash=Decimal(str(eq)), open_positions=0))
        ts += timedelta(days=1)
    return curve


# ------------------------------------------------------------------ #
# Return metrics
# ------------------------------------------------------------------ #

def test_total_return_pct_positive():
    result = _make_result(initial=100_000, final=110_000)
    assert total_return_pct(result) == pytest.approx(10.0)


def test_total_return_pct_negative():
    result = _make_result(initial=100_000, final=95_000)
    assert total_return_pct(result) == pytest.approx(-5.0)


def test_total_return_pct_zero_capital():
    result = _make_result(initial=0, final=0)
    assert total_return_pct(result) == 0.0


def test_annualized_return_no_days():
    result = _make_result(start_date=date(2024, 1, 2), end_date=date(2024, 1, 2))
    assert annualized_return_pct(result) == 0.0


def test_annualized_return_positive():
    result = _make_result(initial=100_000, final=110_000,
                          start_date=date(2024, 1, 2), end_date=date(2024, 12, 31))
    ann = annualized_return_pct(result)
    assert 8.0 < ann < 12.0  # ~10% for a full year


# ------------------------------------------------------------------ #
# Sharpe / Sortino
# ------------------------------------------------------------------ #

def test_sharpe_positive_returns():
    # Steady upward drift → positive Sharpe
    returns = [0.002] * 252
    curve = _make_equity_curve(returns)
    result = _make_result(equity_curve=curve)
    sr = sharpe_ratio(result)
    assert sr > 0


def test_sharpe_below_risk_free():
    # Returns below risk-free rate → negative Sharpe
    returns = [-0.001] * 100
    curve = _make_equity_curve(returns)
    result = _make_result(equity_curve=curve)
    sr = sharpe_ratio(result)
    assert sr < 0


def test_sortino_no_downside():
    returns = [0.003] * 100
    curve = _make_equity_curve(returns)
    result = _make_result(equity_curve=curve)
    sr = sortino_ratio(result)
    assert sr == float("inf") or sr > 0


def test_sortino_with_losses():
    returns = [0.003, -0.002, 0.004, -0.001] * 50
    curve = _make_equity_curve(returns)
    result = _make_result(equity_curve=curve)
    sr = sortino_ratio(result)
    assert sr > 0  # Net positive return


# ------------------------------------------------------------------ #
# Drawdown
# ------------------------------------------------------------------ #

def test_max_drawdown_flat():
    curve = _make_equity_curve([0.0] * 50)
    result = _make_result(equity_curve=curve)
    dd, dur = max_drawdown(result)
    assert dd == 0.0


def test_max_drawdown_known_drop():
    # Drop 10% then recover
    returns = [0.0] * 10 + [-0.01] * 10 + [0.01] * 10
    curve = _make_equity_curve(returns)
    result = _make_result(equity_curve=curve)
    dd, dur = max_drawdown(result)
    assert 0 < dd < 15  # ~9-10% drawdown
    assert dur > 0


def test_max_drawdown_empty():
    result = _make_result(equity_curve=[])
    dd, dur = max_drawdown(result)
    assert dd == 0.0
    assert dur == 0


# ------------------------------------------------------------------ #
# Trade metrics
# ------------------------------------------------------------------ #

def test_win_rate_all_winners():
    trades = [_make_trade(100) for _ in range(5)]
    result = _make_result(trades=trades)
    assert win_rate(result) == 100.0


def test_win_rate_all_losers():
    trades = [_make_trade(-50) for _ in range(4)]
    result = _make_result(trades=trades)
    assert win_rate(result) == 0.0


def test_win_rate_mixed():
    trades = [_make_trade(100), _make_trade(-50), _make_trade(200), _make_trade(-30)]
    result = _make_result(trades=trades)
    assert win_rate(result) == pytest.approx(50.0)


def test_profit_factor():
    trades = [_make_trade(100), _make_trade(-50), _make_trade(200), _make_trade(-100)]
    result = _make_result(trades=trades)
    pf = profit_factor(result)
    assert pf == pytest.approx(300 / 150, rel=0.01)


def test_profit_factor_no_losses():
    trades = [_make_trade(100), _make_trade(50)]
    result = _make_result(trades=trades)
    assert profit_factor(result) == float("inf")


def test_avg_win():
    trades = [_make_trade(100), _make_trade(200), _make_trade(-50)]
    result = _make_result(trades=trades)
    assert avg_win(result) == pytest.approx(150.0, rel=0.01)


def test_avg_loss():
    trades = [_make_trade(100), _make_trade(-50), _make_trade(-150)]
    result = _make_result(trades=trades)
    assert avg_loss(result) == pytest.approx(-100.0, rel=0.01)


def test_exit_reason_breakdown():
    trades = [
        _make_trade(100, "target"),
        _make_trade(-50, "stop"),
        _make_trade(80, "target"),
        _make_trade(20, "eod"),
    ]
    result = _make_result(trades=trades)
    breakdown = exit_reason_breakdown(result)
    assert breakdown["target"] == 2
    assert breakdown["stop"] == 1
    assert breakdown["eod"] == 1


def test_compute_metrics_keys():
    result = _make_result(
        trades=[_make_trade(100), _make_trade(-50)],
        equity_curve=_make_equity_curve([0.001] * 60),
    )
    m = compute_metrics(result)
    required_keys = [
        "total_return_pct", "annualized_return_pct", "sharpe_ratio",
        "sortino_ratio", "max_drawdown_pct", "win_rate_pct", "profit_factor",
        "total_trades", "exit_reasons",
    ]
    for key in required_keys:
        assert key in m, f"Missing key: {key}"
