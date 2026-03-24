"""Performance analytics for backtest results."""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Optional

from packages.backtest.engine import BacktestResult, EquityPoint, TradeRecord


def _to_float(d: Decimal) -> float:
    return float(d)


# ------------------------------------------------------------------ #
# Core metrics
# ------------------------------------------------------------------ #

def total_return_pct(result: BacktestResult) -> float:
    if result.initial_capital == 0:
        return 0.0
    return _to_float(
        (result.final_capital - result.initial_capital) / result.initial_capital * 100
    )


def annualized_return_pct(result: BacktestResult) -> float:
    days = (result.end_date - result.start_date).days
    if days <= 0:
        return 0.0
    total_r = _to_float(result.final_capital / result.initial_capital)
    if total_r <= 0:
        return -100.0
    years = days / 365.25
    return (total_r ** (1 / years) - 1) * 100


def _daily_returns(equity_curve: list[EquityPoint]) -> list[float]:
    """Compute daily return series from equity curve (1 per trading day)."""
    if len(equity_curve) < 2:
        return []

    # Group equity by date, take last equity of each day
    daily: dict = {}
    for ep in equity_curve:
        d = ep.timestamp.date()
        daily[d] = float(ep.equity)

    dates = sorted(daily.keys())
    equities = [daily[d] for d in dates]
    returns = []
    for i in range(1, len(equities)):
        prev = equities[i - 1]
        if prev == 0:
            returns.append(0.0)
        else:
            returns.append((equities[i] - prev) / prev)
    return returns


def sharpe_ratio(result: BacktestResult, risk_free_annual: float = 0.05) -> float:
    returns = _daily_returns(result.equity_curve)
    if len(returns) < 2:
        return 0.0
    rf_daily = risk_free_annual / 252
    excess = [r - rf_daily for r in returns]
    mean = sum(excess) / len(excess)
    variance = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(252)


def sortino_ratio(result: BacktestResult, risk_free_annual: float = 0.05) -> float:
    returns = _daily_returns(result.equity_curve)
    if len(returns) < 2:
        return 0.0
    rf_daily = risk_free_annual / 252
    excess = [r - rf_daily for r in returns]
    mean = sum(excess) / len(excess)
    downside = [r for r in excess if r < 0]
    if not downside:
        return float("inf")
    downside_var = sum(r ** 2 for r in downside) / len(downside)
    downside_std = math.sqrt(downside_var)
    if downside_std == 0:
        return 0.0
    return (mean / downside_std) * math.sqrt(252)


def max_drawdown(result: BacktestResult) -> tuple[float, int]:
    """Return (max_drawdown_pct, max_drawdown_duration_bars)."""
    if not result.equity_curve:
        return 0.0, 0

    equities = [float(ep.equity) for ep in result.equity_curve]
    peak = equities[0]
    max_dd = 0.0
    dd_start = 0
    max_dd_duration = 0
    current_dd_start = 0

    for i, eq in enumerate(equities):
        if eq > peak:
            peak = eq
            current_dd_start = i
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
                max_dd_duration = i - current_dd_start

    return max_dd * 100, max_dd_duration


def calmar_ratio(result: BacktestResult) -> float:
    ann_return = annualized_return_pct(result)
    max_dd, _ = max_drawdown(result)
    if max_dd == 0:
        return float("inf")
    return ann_return / max_dd


def annualized_volatility(result: BacktestResult) -> float:
    returns = _daily_returns(result.equity_curve)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance * 252) * 100


# ------------------------------------------------------------------ #
# Trade metrics
# ------------------------------------------------------------------ #

def win_rate(result: BacktestResult) -> float:
    if not result.trades:
        return 0.0
    winners = sum(1 for t in result.trades if t.is_winner)
    return winners / len(result.trades) * 100


def profit_factor(result: BacktestResult) -> float:
    gross_profit = sum(float(t.net_pnl) for t in result.trades if t.net_pnl > 0)
    gross_loss = abs(sum(float(t.net_pnl) for t in result.trades if t.net_pnl < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def avg_win(result: BacktestResult) -> float:
    winners = [float(t.net_pnl) for t in result.trades if t.is_winner]
    return sum(winners) / len(winners) if winners else 0.0


def avg_loss(result: BacktestResult) -> float:
    losers = [float(t.net_pnl) for t in result.trades if not t.is_winner]
    return sum(losers) / len(losers) if losers else 0.0


def avg_bars_held(result: BacktestResult) -> float:
    if not result.trades:
        return 0.0
    return sum(t.bars_held for t in result.trades) / len(result.trades)


def total_commission(result: BacktestResult) -> float:
    return sum(float(t.commission) for t in result.trades)


def total_net_pnl(result: BacktestResult) -> float:
    return sum(float(t.net_pnl) for t in result.trades)


def exit_reason_breakdown(result: BacktestResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in result.trades:
        counts[t.exit_reason] = counts.get(t.exit_reason, 0) + 1
    return counts


def risk_event_breakdown(result: BacktestResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for re in result.risk_events:
        k = re.event.value
        counts[k] = counts.get(k, 0) + 1
    return counts


# ------------------------------------------------------------------ #
# Full summary dict
# ------------------------------------------------------------------ #

def compute_metrics(result: BacktestResult) -> dict:
    max_dd_pct, max_dd_dur = max_drawdown(result)
    return {
        # Returns
        "total_return_pct": round(total_return_pct(result), 4),
        "annualized_return_pct": round(annualized_return_pct(result), 4),
        "sharpe_ratio": round(sharpe_ratio(result), 4),
        "sortino_ratio": round(sortino_ratio(result), 4),
        "calmar_ratio": round(calmar_ratio(result), 4),
        "annualized_volatility_pct": round(annualized_volatility(result), 4),
        # Drawdown
        "max_drawdown_pct": round(max_dd_pct, 4),
        "max_drawdown_duration_bars": max_dd_dur,
        # Capital
        "initial_capital": float(result.initial_capital),
        "final_capital": round(float(result.final_capital), 2),
        "net_profit": round(float(result.final_capital - result.initial_capital), 2),
        # Trades
        "total_trades": len(result.trades),
        "winning_trades": sum(1 for t in result.trades if t.is_winner),
        "losing_trades": sum(1 for t in result.trades if not t.is_winner),
        "win_rate_pct": round(win_rate(result), 2),
        "profit_factor": round(profit_factor(result), 4),
        "avg_win": round(avg_win(result), 2),
        "avg_loss": round(avg_loss(result), 2),
        "avg_bars_held": round(avg_bars_held(result), 1),
        "total_commission": round(total_commission(result), 2),
        "blocked_orders": len(result.blocked_orders),
        # Breakdowns
        "exit_reasons": exit_reason_breakdown(result),
        "risk_events": risk_event_breakdown(result),
        # Period
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "trading_days": (result.end_date - result.start_date).days,
        "strategies": result.strategy_names,
        "symbols": result.symbols,
    }
