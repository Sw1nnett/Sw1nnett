"""Grid-search strategy optimizer ranked by Sharpe ratio."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Optional

from packages.backtest.analytics import compute_metrics, sharpe_ratio
from packages.backtest.engine import BacktestEngine, BacktestResult
from packages.core.models import Bar, StrategyConfig
from packages.observability.logging import get_logger

log = get_logger(__name__)


@dataclass
class OptimizationRun:
    params: dict[str, Any]
    result: BacktestResult
    metrics: dict
    sharpe: float
    total_return_pct: float
    win_rate_pct: float
    total_trades: int


@dataclass
class OptimizationResult:
    strategy_cls: str
    symbols: list[str]
    param_grid: dict[str, list[Any]]
    runs: list[OptimizationRun] = field(default_factory=list)

    @property
    def best(self) -> Optional[OptimizationRun]:
        if not self.runs:
            return None
        return max(self.runs, key=lambda r: r.sharpe)

    @property
    def ranked(self) -> list[OptimizationRun]:
        return sorted(self.runs, key=lambda r: r.sharpe, reverse=True)


def grid_search(
    strategy_cls: str,
    symbols: list[str],
    bars_by_symbol: dict[str, list[Bar]],
    param_grid: dict[str, list[Any]],
    initial_capital: float = 100_000.0,
    warmup_bars: int = 60,
    commission_per_share: float = 0.005,
    slippage_bps: float = 2.0,
    min_trades: int = 5,
) -> OptimizationResult:
    """
    Exhaustive grid search over ``param_grid`` for ``strategy_cls``.

    Each param combination is run as a full backtest. Results are collected
    and ranked by Sharpe ratio. Combinations with fewer than ``min_trades``
    completed trades are excluded from ranking but still recorded.

    Example::

        result = grid_search(
            strategy_cls="MomentumBreakoutStrategy",
            symbols=["SPY"],
            bars_by_symbol={"SPY": bars},
            param_grid={
                "breakout_period": [10, 20, 30],
                "rvol_threshold": [0.5, 1.0, 1.5],
            },
        )
        print(result.best.params, result.best.sharpe)
    """
    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))
    total = len(combos)
    log.info("optimizer_start", strategy=strategy_cls, combos=total)

    opt = OptimizationResult(
        strategy_cls=strategy_cls,
        symbols=symbols,
        param_grid=param_grid,
    )

    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        log.info("optimizer_run", combo=idx + 1, total=total, params=params)

        cfg = StrategyConfig(
            cls_name=strategy_cls,
            symbols=symbols,
            params=params,
        )
        engine = BacktestEngine(
            strategy_configs=[cfg],
            initial_capital=initial_capital,
            commission_per_share=commission_per_share,
            slippage_bps=slippage_bps,
            warmup_bars=warmup_bars,
        )

        try:
            result = engine.run(bars_by_symbol)
        except Exception as e:
            log.error("optimizer_run_failed", params=params, error=str(e))
            continue

        metrics = compute_metrics(result)
        sr = metrics.get("sharpe_ratio", 0.0)
        if isinstance(sr, float) and sr != sr:  # NaN check
            sr = 0.0

        opt.runs.append(OptimizationRun(
            params=params,
            result=result,
            metrics=metrics,
            sharpe=sr,
            total_return_pct=metrics.get("total_return_pct", 0.0),
            win_rate_pct=metrics.get("win_rate_pct", 0.0),
            total_trades=metrics.get("total_trades", 0),
        ))

    log.info(
        "optimizer_done",
        runs=len(opt.runs),
        best_sharpe=opt.best.sharpe if opt.best else None,
        best_params=opt.best.params if opt.best else None,
    )
    return opt
