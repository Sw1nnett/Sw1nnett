"""Tearsheet: text display and CSV export of backtest results."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from packages.backtest.analytics import compute_metrics
from packages.backtest.engine import BacktestResult


_WIDTH = 62


def _line(char: str = "-") -> str:
    return char * _WIDTH


def _header(text: str, char: str = "=") -> str:
    return f"{char * _WIDTH}\n{text.center(_WIDTH)}\n{char * _WIDTH}"


def _row(label: str, value: str, width: int = 32) -> str:
    return f"  {label:<{width}} {value}"


def print_tearsheet(result: BacktestResult, risk_free: float = 0.05) -> None:
    """Print a formatted tearsheet to stdout."""
    print(build_tearsheet(result, risk_free))


def build_tearsheet(result: BacktestResult, risk_free: float = 0.05) -> str:
    m = compute_metrics(result)
    strats = ", ".join(result.strategy_names)
    syms = ", ".join(result.symbols)

    lines = [
        _header(f"BACKTEST RESULTS"),
        f"  Strategy:   {strats}",
        f"  Symbols:    {syms}",
        f"  Period:     {m['start_date']} → {m['end_date']} ({m['trading_days']} days)",
        _line(),
        "  CAPITAL",
        _row("Initial Capital", f"${m['initial_capital']:>12,.2f}"),
        _row("Final Capital",   f"${m['final_capital']:>12,.2f}"),
        _row("Net Profit",      f"${m['net_profit']:>+12,.2f}"),
        _line(),
        "  RETURNS",
        _row("Total Return",          f"{m['total_return_pct']:>+8.2f}%"),
        _row("Annualized Return",     f"{m['annualized_return_pct']:>+8.2f}%"),
        _row("Annualized Volatility", f"{m['annualized_volatility_pct']:>8.2f}%"),
        _line(),
        "  RISK-ADJUSTED",
        _row("Sharpe Ratio",  f"{m['sharpe_ratio']:>8.3f}"),
        _row("Sortino Ratio", f"{m['sortino_ratio']:>8.3f}"),
        _row("Calmar Ratio",  f"{m['calmar_ratio']:>8.3f}"),
        _line(),
        "  DRAWDOWN",
        _row("Max Drawdown",          f"{m['max_drawdown_pct']:>8.2f}%"),
        _row("Max Drawdown Duration", f"{m['max_drawdown_duration_bars']:>8} bars"),
        _line(),
        "  TRADES",
        _row("Total Trades",     f"{m['total_trades']:>8}"),
        _row("Winning Trades",   f"{m['winning_trades']:>8}"),
        _row("Losing Trades",    f"{m['losing_trades']:>8}"),
        _row("Win Rate",         f"{m['win_rate_pct']:>8.1f}%"),
        _row("Profit Factor",    f"{m['profit_factor']:>8.3f}"),
        _row("Avg Win",          f"${m['avg_win']:>+10,.2f}"),
        _row("Avg Loss",         f"${m['avg_loss']:>+10,.2f}"),
        _row("Avg Hold (bars)",  f"{m['avg_bars_held']:>8.1f}"),
        _row("Total Commission", f"${m['total_commission']:>10,.2f}"),
        _row("Blocked Orders",   f"{m['blocked_orders']:>8}"),
    ]

    # Exit reason breakdown
    exits = m.get("exit_reasons", {})
    if exits:
        lines.append(_line())
        lines.append("  EXIT REASONS")
        for reason, count in sorted(exits.items(), key=lambda x: -x[1]):
            lines.append(_row(f"  {reason}", f"{count:>8}"))

    # Risk event breakdown
    risk_evts = m.get("risk_events", {})
    if risk_evts:
        lines.append(_line())
        lines.append("  RISK EVENTS")
        for evt, count in sorted(risk_evts.items(), key=lambda x: -x[1]):
            lines.append(_row(f"  {evt}", f"{count:>8}"))

    lines.append(_line("="))
    return "\n".join(lines)


def export_trades_csv(result: BacktestResult, path: str | Path) -> None:
    """Export trade log to CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "symbol", "strategy_id", "side",
            "entry_time", "exit_time",
            "entry_price", "exit_price", "qty",
            "gross_pnl", "commission", "net_pnl",
            "return_pct", "exit_reason", "bars_held",
        ])
        for t in result.trades:
            writer.writerow([
                t.symbol, t.strategy_id, t.side,
                t.entry_time.isoformat(), t.exit_time.isoformat(),
                float(t.entry_price), float(t.exit_price), float(t.qty),
                float(t.gross_pnl), float(t.commission), float(t.net_pnl),
                float(t.return_pct), t.exit_reason, t.bars_held,
            ])


def export_equity_curve_csv(result: BacktestResult, path: str | Path) -> None:
    """Export equity curve to CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "equity", "cash", "open_positions"])
        for ep in result.equity_curve:
            writer.writerow([
                ep.timestamp.isoformat(),
                float(ep.equity),
                float(ep.cash),
                ep.open_positions,
            ])


def export_metrics_csv(result: BacktestResult, path: str | Path) -> None:
    """Export scalar metrics to a single-row CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m = compute_metrics(result)
    scalar_keys = [k for k, v in m.items() if not isinstance(v, (dict, list))]
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(scalar_keys)
        writer.writerow([m[k] for k in scalar_keys])


def export_all(result: BacktestResult, output_dir: str | Path) -> dict[str, Path]:
    """Export tearsheet, trades, equity curve, and metrics to output_dir."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Text tearsheet
    tearsheet_path = output_dir / "tearsheet.txt"
    tearsheet_path.write_text(build_tearsheet(result))

    trades_path = output_dir / "trades.csv"
    export_trades_csv(result, trades_path)

    equity_path = output_dir / "equity_curve.csv"
    export_equity_curve_csv(result, equity_path)

    metrics_path = output_dir / "metrics.csv"
    export_metrics_csv(result, metrics_path)

    return {
        "tearsheet": tearsheet_path,
        "trades": trades_path,
        "equity_curve": equity_path,
        "metrics": metrics_path,
    }
