"""Unit tests for the strategy optimizer."""
from __future__ import annotations

import pytest

from packages.backtest.data_loader import generate_synthetic_bars
from packages.backtest.optimizer import OptimizationResult, OptimizationRun, grid_search


@pytest.fixture(scope="module")
def spy_bars():
    return generate_synthetic_bars("SPY", n_bars=400, start_price=450.0, seed=7)


def test_grid_search_returns_correct_run_count(spy_bars):
    result = grid_search(
        strategy_cls="MomentumBreakoutStrategy",
        symbols=["SPY"],
        bars_by_symbol={"SPY": spy_bars},
        param_grid={
            "breakout_period": [10, 20],
            "rvol_threshold": [0.5, 1.0],
        },
        warmup_bars=30,
    )
    # 2 x 2 = 4 combinations
    assert len(result.runs) == 4


def test_grid_search_best_has_highest_sharpe(spy_bars):
    result = grid_search(
        strategy_cls="MomentumBreakoutStrategy",
        symbols=["SPY"],
        bars_by_symbol={"SPY": spy_bars},
        param_grid={
            "breakout_period": [10, 20, 30],
            "rvol_threshold": [0.5],
        },
        warmup_bars=30,
    )
    best = result.best
    assert best is not None
    for run in result.runs:
        assert best.sharpe >= run.sharpe


def test_grid_search_ranked_is_descending(spy_bars):
    result = grid_search(
        strategy_cls="MomentumBreakoutStrategy",
        symbols=["SPY"],
        bars_by_symbol={"SPY": spy_bars},
        param_grid={
            "breakout_period": [10, 20],
            "rvol_threshold": [0.5, 1.5],
        },
        warmup_bars=30,
    )
    ranked = result.ranked
    for i in range(len(ranked) - 1):
        assert ranked[i].sharpe >= ranked[i + 1].sharpe


def test_grid_search_all_runs_have_metrics(spy_bars):
    result = grid_search(
        strategy_cls="MomentumBreakoutStrategy",
        symbols=["SPY"],
        bars_by_symbol={"SPY": spy_bars},
        param_grid={"breakout_period": [15]},
        warmup_bars=30,
    )
    for run in result.runs:
        assert "total_return_pct" in run.metrics
        assert "sharpe_ratio" in run.metrics
        assert run.total_trades >= 0


def test_grid_search_single_param(spy_bars):
    result = grid_search(
        strategy_cls="MomentumBreakoutStrategy",
        symbols=["SPY"],
        bars_by_symbol={"SPY": spy_bars},
        param_grid={"breakout_period": [20]},
        warmup_bars=30,
    )
    assert len(result.runs) == 1
    assert result.best == result.runs[0]


def test_optimization_result_empty_has_no_best():
    result = OptimizationResult(
        strategy_cls="TestStrategy",
        symbols=["SPY"],
        param_grid={},
    )
    assert result.best is None
    assert result.ranked == []
