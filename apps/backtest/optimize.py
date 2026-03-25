"""CLI runner for strategy grid-search optimization."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from packages.backtest.data_loader import generate_synthetic_bars, load_multi_symbol_csv
from packages.backtest.optimizer import grid_search
from packages.observability.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="optimize",
        description="Grid-search strategy parameters ranked by Sharpe ratio",
    )
    p.add_argument("--strategy", default="MomentumBreakoutStrategy")
    p.add_argument("--symbols", default="SPY")
    p.add_argument("--param-grid", metavar="JSON", required=True,
                   help='JSON object of param -> list of values, e.g. \'{"breakout_period":[10,20]}\'')
    p.add_argument("--data-dir", metavar="DIR",
                   help="Directory with {SYMBOL}.csv files; uses synthetic data otherwise")
    p.add_argument("--n-bars", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--capital", type=float, default=100_000.0)
    p.add_argument("--warmup", type=int, default=60)
    p.add_argument("--top", type=int, default=10, help="Number of top results to display")
    return p


def main() -> int:
    args = build_parser().parse_args()
    configure_logging("WARNING", "/tmp/optimize_logs")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    try:
        param_grid = json.loads(args.param_grid)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid --param-grid JSON: {e}", file=sys.stderr)
        return 1

    if args.data_dir:
        bars_by_symbol = load_multi_symbol_csv(args.data_dir, symbols)
    else:
        print(f"Using synthetic data ({args.n_bars} bars/symbol, seed={args.seed})")
        bars_by_symbol = {
            sym: generate_synthetic_bars(sym, n_bars=args.n_bars, seed=args.seed)
            for sym in symbols
        }

    bars_by_symbol = {k: v for k, v in bars_by_symbol.items() if v}
    if not bars_by_symbol:
        print("ERROR: No bar data available.", file=sys.stderr)
        return 1

    total_combos = 1
    for v in param_grid.values():
        total_combos *= len(v)
    print(f"\nOptimizing {args.strategy} on {', '.join(symbols)}")
    print(f"Grid: {total_combos} combinations × {args.n_bars} bars each\n")

    result = grid_search(
        strategy_cls=args.strategy,
        symbols=symbols,
        bars_by_symbol=bars_by_symbol,
        param_grid=param_grid,
        initial_capital=args.capital,
        warmup_bars=args.warmup,
    )

    if not result.runs:
        print("No successful runs.", file=sys.stderr)
        return 1

    print(f"{'Rank':<5} {'Sharpe':>8} {'Return%':>9} {'WinRate%':>10} {'Trades':>7}  Params")
    print("─" * 70)
    for rank, run in enumerate(result.ranked[: args.top], 1):
        params_str = ", ".join(f"{k}={v}" for k, v in run.params.items())
        print(
            f"{rank:<5} {run.sharpe:>8.3f} {run.total_return_pct:>9.2f} "
            f"{run.win_rate_pct:>10.1f} {run.total_trades:>7}  {params_str}"
        )

    if result.best:
        print(f"\nBest params: {json.dumps(result.best.params)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
