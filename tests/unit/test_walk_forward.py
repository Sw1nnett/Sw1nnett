"""Unit tests for the walk-forward validator."""
from __future__ import annotations

import pytest

from packages.backtest.data_loader import generate_synthetic_bars
from packages.backtest.walk_forward import WalkForwardResult, run_walk_forward
from packages.core.models import StrategyConfig


@pytest.fixture(scope="module")
def spy_bars():
    # Need enough bars for multiple splits with warmup
    return generate_synthetic_bars("SPY", n_bars=1200, start_price=450.0, seed=13)


def _cfg(**params) -> StrategyConfig:
    return StrategyConfig(
        cls_name="MomentumBreakoutStrategy",
        symbols=["SPY"],
        params=params or {"breakout_period": 20, "rvol_threshold": 0.5},
    )


def test_walk_forward_returns_splits(spy_bars):
    result = run_walk_forward(
        strategy_config=_cfg(),
        bars_by_symbol={"SPY": spy_bars},
        n_splits=3,
        warmup_bars=40,
    )
    assert isinstance(result, WalkForwardResult)
    assert len(result.splits) == 3


def test_walk_forward_splits_have_metrics(spy_bars):
    result = run_walk_forward(
        strategy_config=_cfg(),
        bars_by_symbol={"SPY": spy_bars},
        n_splits=3,
        warmup_bars=40,
    )
    for split in result.splits:
        assert "total_return_pct" in split.test_metrics
        assert "sharpe_ratio" in split.test_metrics
        assert split.train_bars > 0
        assert split.test_bars > 0


def test_walk_forward_consistency_score_range(spy_bars):
    result = run_walk_forward(
        strategy_config=_cfg(),
        bars_by_symbol={"SPY": spy_bars},
        n_splits=4,
        warmup_bars=40,
    )
    assert 0.0 <= result.consistency_score <= 1.0


def test_walk_forward_avg_sharpe_is_mean_of_splits(spy_bars):
    result = run_walk_forward(
        strategy_config=_cfg(),
        bars_by_symbol={"SPY": spy_bars},
        n_splits=3,
        warmup_bars=40,
    )
    if not result.splits:
        pytest.skip("No splits produced")
    expected_avg = sum(s.test_metrics["sharpe_ratio"] for s in result.splits) / len(result.splits)
    assert abs(result.avg_sharpe - expected_avg) < 1e-9


def test_walk_forward_empty_returns_empty_result():
    result = run_walk_forward(
        strategy_config=_cfg(),
        bars_by_symbol={"SPY": []},
        n_splits=3,
        warmup_bars=5,
    )
    # No bars → no splits
    assert len(result.splits) == 0


def test_walk_forward_too_few_bars_raises(spy_bars):
    tiny_bars = spy_bars[:20]
    with pytest.raises(ValueError, match="Window size"):
        run_walk_forward(
            strategy_config=_cfg(),
            bars_by_symbol={"SPY": tiny_bars},
            n_splits=3,
            warmup_bars=40,
        )
