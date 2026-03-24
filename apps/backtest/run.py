"""CLI runner for backtests."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as: python -m apps.backtest.run
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from packages.backtest.data_loader import generate_synthetic_bars, load_csv, load_multi_symbol_csv
from packages.backtest.engine import BacktestEngine
from packages.backtest.tearsheet import export_all, print_tearsheet
from packages.core.config import get_settings
from packages.core.models import StrategyConfig
from packages.observability.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backtest",
        description="Run a trading strategy backtest",
    )
    p.add_argument(
        "--strategy",
        default="MomentumBreakoutStrategy",
        help="Strategy class name (default: MomentumBreakoutStrategy)",
    )
    p.add_argument(
        "--symbols",
        default="SPY,QQQ,AAPL",
        help="Comma-separated symbols (default: SPY,QQQ,AAPL)",
    )
    p.add_argument(
        "--data-dir",
        metavar="DIR",
        help="Directory with {SYMBOL}.csv files. Uses synthetic data if not set.",
    )
    p.add_argument(
        "--capital",
        type=float,
        default=100_000.0,
        help="Initial capital in USD (default: 100000)",
    )
    p.add_argument(
        "--warmup",
        type=int,
        default=60,
        help="Warmup bars before signals start (default: 60)",
    )
    p.add_argument(
        "--n-bars",
        type=int,
        default=2000,
        help="Synthetic data: number of bars per symbol (default: 2000)",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Synthetic data: random seed (default: 42)",
    )
    p.add_argument(
        "--params",
        default="{}",
        metavar="JSON",
        help='Strategy params as JSON (default: {})',
    )
    p.add_argument(
        "--output-dir",
        default="backtest_results",
        metavar="DIR",
        help="Directory to write CSV outputs (default: backtest_results)",
    )
    p.add_argument(
        "--no-export",
        action="store_true",
        help="Skip writing output files",
    )
    p.add_argument(
        "--commission",
        type=float,
        default=0.005,
        help="Commission per share in USD (default: 0.005)",
    )
    p.add_argument(
        "--slippage-bps",
        type=float,
        default=2.0,
        help="Slippage in basis points (default: 2.0)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    configure_logging("WARNING", "/tmp/backtest_logs")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid --params JSON: {e}", file=sys.stderr)
        return 1

    # Load or generate bars
    bars_by_symbol: dict = {}
    if args.data_dir:
        bars_by_symbol = load_multi_symbol_csv(args.data_dir, symbols)
        missing = [s for s, bars in bars_by_symbol.items() if not bars]
        if missing:
            print(f"WARNING: No data found for: {', '.join(missing)}", file=sys.stderr)
    else:
        print(f"No --data-dir provided. Using synthetic data ({args.n_bars} bars/symbol, seed={args.seed}).")
        for sym in symbols:
            bars_by_symbol[sym] = generate_synthetic_bars(
                symbol=sym,
                n_bars=args.n_bars,
                seed=args.seed,
            )

    # Filter to symbols that have data
    bars_by_symbol = {k: v for k, v in bars_by_symbol.items() if v}
    if not bars_by_symbol:
        print("ERROR: No bar data available.", file=sys.stderr)
        return 1

    cfg = StrategyConfig(
        cls_name=args.strategy,
        symbols=list(bars_by_symbol.keys()),
        params=params,
    )

    engine = BacktestEngine(
        strategy_configs=[cfg],
        initial_capital=args.capital,
        commission_per_share=args.commission,
        slippage_bps=args.slippage_bps,
        warmup_bars=args.warmup,
    )

    print(f"\nRunning backtest: {args.strategy} on {', '.join(bars_by_symbol.keys())} ...")
    result = engine.run(bars_by_symbol)

    print()
    print_tearsheet(result)

    if not args.no_export:
        paths = export_all(result, args.output_dir)
        print(f"\nOutputs written to: {args.output_dir}/")
        for name, path in paths.items():
            print(f"  {name}: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
