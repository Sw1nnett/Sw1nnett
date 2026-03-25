"""CLI runner for walk-forward validation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from packages.backtest.data_loader import generate_synthetic_bars, load_multi_symbol_csv
from packages.backtest.walk_forward import run_walk_forward
from packages.core.models import StrategyConfig
from packages.observability.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="walk_forward",
        description="Walk-forward validation — assess strategy out-of-sample robustness",
    )
    p.add_argument("--strategy", default="MomentumBreakoutStrategy")
    p.add_argument("--symbols", default="SPY")
    p.add_argument("--params", metavar="JSON", default="{}",
                   help='Strategy params as JSON, e.g. \'{"breakout_period":20}\'')
    p.add_argument("--data-dir", metavar="DIR",
                   help="Directory with {SYMBOL}.csv files; uses synthetic data otherwise")
    p.add_argument("--n-bars", type=int, default=3000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--splits", type=int, default=5)
    p.add_argument("--train-pct", type=float, default=0.7,
                   help="Fraction of each split used for training (default 0.7)")
    p.add_argument("--capital", type=float, default=100_000.0)
    p.add_argument("--warmup", type=int, default=60)
    p.add_argument("--commission", type=float, default=0.005, metavar="DOLLARS")
    p.add_argument("--slippage", type=float, default=2.0, metavar="BPS")
    return p


def main() -> int:
    args = build_parser().parse_args()
    configure_logging("WARNING", "/tmp/wf_logs")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid --params JSON: {e}", file=sys.stderr)
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

    cfg = StrategyConfig(cls_name=args.strategy, symbols=symbols, params=params)
    print(
        f"\nWalk-forward: {args.strategy} on {', '.join(symbols)} | "
        f"{args.splits} splits, train={int(args.train_pct * 100)}%, "
        f"{args.n_bars} bars total\n"
    )

    try:
        result = run_walk_forward(
            strategy_config=cfg,
            bars_by_symbol=bars_by_symbol,
            n_splits=args.splits,
            train_pct=args.train_pct,
            initial_capital=args.capital,
            warmup_bars=args.warmup,
            commission_per_share=args.commission,
            slippage_bps=args.slippage,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if not result.splits:
        print("No valid splits completed.", file=sys.stderr)
        return 1

    # Per-split table
    print(f"{'Split':<7} {'Train':>7} {'Test':>7} {'Sharpe':>8} {'Return%':>9} {'Trades':>7}")
    print("─" * 52)
    for s in result.splits:
        sharpe = s.test_metrics.get("sharpe_ratio", 0.0)
        ret = s.test_metrics.get("total_return_pct", 0.0)
        trades = s.test_metrics.get("total_trades", 0)
        print(
            f"{s.split_index:<7} {s.train_bars:>7} {s.test_bars:>7} "
            f"{sharpe:>8.3f} {ret:>9.2f} {trades:>7}"
        )

    # Summary
    print("\n── Summary ──")
    print(f"  Splits completed    : {len(result.splits)}")
    print(f"  Avg Sharpe (OOS)    : {result.avg_sharpe:.3f}")
    print(f"  Avg Return % (OOS)  : {result.avg_return_pct:.2f}%")
    print(f"  Avg Win Rate (OOS)  : {result.avg_win_rate:.1f}%")
    print(f"  Consistency score   : {result.consistency_score:.0%} splits profitable")
    print(f"  Total OOS trades    : {result.total_test_trades}")

    if result.consistency_score >= 0.6 and result.avg_sharpe > 0.5:
        print("\n  ✔  Strategy passes walk-forward criteria")
    else:
        print("\n  ✘  Strategy fails walk-forward criteria")

    return 0


if __name__ == "__main__":
    sys.exit(main())
