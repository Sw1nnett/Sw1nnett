"""Walk-forward validation for strategy robustness assessment."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from packages.backtest.analytics import compute_metrics, sharpe_ratio, total_return_pct, win_rate
from packages.backtest.engine import BacktestEngine, BacktestResult
from packages.core.models import Bar, StrategyConfig
from packages.observability.logging import get_logger

log = get_logger(__name__)


@dataclass
class WFSplit:
    """One train/test split."""
    split_index: int
    train_bars: int
    test_bars: int
    train_result: BacktestResult
    test_result: BacktestResult
    test_metrics: dict


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward analysis output."""
    splits: list[WFSplit] = field(default_factory=list)

    # Aggregate test-period statistics
    avg_sharpe: float = 0.0
    avg_return_pct: float = 0.0
    avg_win_rate: float = 0.0
    consistency_score: float = 0.0  # fraction of splits with positive return
    total_test_trades: int = 0


def run_walk_forward(
    strategy_config: StrategyConfig,
    bars_by_symbol: dict[str, list[Bar]],
    n_splits: int = 5,
    train_pct: float = 0.7,
    initial_capital: float = 100_000.0,
    warmup_bars: int = 60,
    commission_per_share: float = 0.005,
    slippage_bps: float = 2.0,
) -> WalkForwardResult:
    """
    Perform walk-forward validation.

    Each symbol's bar list is divided into ``n_splits`` sequential windows.
    Within each window the first ``train_pct`` fraction is the in-sample period,
    the remainder is the out-of-sample test period.
    The engine is re-initialised for each split to avoid look-ahead bias.
    """
    if not bars_by_symbol:
        raise ValueError("No bars provided")

    # Use the first symbol to determine split boundaries
    ref_symbol = next(iter(bars_by_symbol))
    ref_bars = bars_by_symbol[ref_symbol]
    n = len(ref_bars)

    if n == 0:
        return WalkForwardResult()

    window_size = n // n_splits
    if window_size < warmup_bars * 2:
        raise ValueError(
            f"Window size ({window_size}) too small for warmup ({warmup_bars}). "
            f"Use fewer splits or more data."
        )

    splits: list[WFSplit] = []

    for i in range(n_splits):
        start_idx = i * window_size
        end_idx = start_idx + window_size if i < n_splits - 1 else n
        train_end = start_idx + int((end_idx - start_idx) * train_pct)

        # Slice bars for this window across all symbols
        train_bars = {
            sym: [b for b in bars if start_idx <= _bar_index(bars_by_symbol[sym], b) < train_end]
            for sym, bars in bars_by_symbol.items()
        }
        test_bars = {
            sym: [b for b in bars if train_end <= _bar_index(bars_by_symbol[sym], b) < end_idx]
            for sym, bars in bars_by_symbol.items()
        }

        # Skip if insufficient data in either period
        min_train = min(len(v) for v in train_bars.values())
        min_test = min(len(v) for v in test_bars.values())
        if min_train < warmup_bars or min_test < 10:
            log.warning("wf_split_skipped", split=i, train=min_train, test=min_test)
            continue

        log.info("wf_split_running", split=i, train_bars=min_train, test_bars=min_test)

        # Train run (for reference — not strictly needed, included for analysis)
        train_engine = BacktestEngine(
            strategy_configs=[strategy_config],
            initial_capital=initial_capital,
            commission_per_share=commission_per_share,
            slippage_bps=slippage_bps,
            warmup_bars=warmup_bars,
        )
        train_result = train_engine.run(train_bars)

        # Test (out-of-sample) run
        test_engine = BacktestEngine(
            strategy_configs=[strategy_config],
            initial_capital=initial_capital,
            commission_per_share=commission_per_share,
            slippage_bps=slippage_bps,
            warmup_bars=min(warmup_bars, min_test // 4),
        )
        test_result = test_engine.run(test_bars)
        test_metrics = compute_metrics(test_result)

        splits.append(WFSplit(
            split_index=i,
            train_bars=min_train,
            test_bars=min_test,
            train_result=train_result,
            test_result=test_result,
            test_metrics=test_metrics,
        ))

    if not splits:
        return WalkForwardResult()

    sharpes = [s.test_metrics.get("sharpe_ratio", 0.0) for s in splits]
    returns = [s.test_metrics.get("total_return_pct", 0.0) for s in splits]
    win_rates = [s.test_metrics.get("win_rate_pct", 0.0) for s in splits]
    total_trades = sum(s.test_metrics.get("total_trades", 0) for s in splits)
    positive_splits = sum(1 for r in returns if r > 0)

    return WalkForwardResult(
        splits=splits,
        avg_sharpe=sum(sharpes) / len(sharpes),
        avg_return_pct=sum(returns) / len(returns),
        avg_win_rate=sum(win_rates) / len(win_rates),
        consistency_score=positive_splits / len(splits),
        total_test_trades=total_trades,
    )


def _bar_index(bars: list[Bar], bar: Bar) -> int:
    """Return the index of bar in bars (by object identity)."""
    for idx, b in enumerate(bars):
        if b is bar:
            return idx
    return -1
