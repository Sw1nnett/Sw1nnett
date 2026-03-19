"""Prometheus metrics definitions."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server


orders_submitted_total = Counter(
    "trading_orders_submitted_total",
    "Total orders submitted",
    ["symbol", "side", "broker"],
)

orders_filled_total = Counter(
    "trading_orders_filled_total",
    "Total orders filled",
    ["symbol", "side"],
)

risk_blocks_total = Counter(
    "trading_risk_blocks_total",
    "Total pre-trade risk blocks",
    ["reason"],
)

kill_switch_activations_total = Counter(
    "trading_kill_switch_activations_total",
    "Number of kill switch activations",
)

daily_pnl = Gauge(
    "trading_daily_pnl_dollars",
    "Current day realized PnL in dollars",
    ["strategy_id"],
)

portfolio_value = Gauge(
    "trading_portfolio_value_dollars",
    "Current portfolio market value",
)

open_positions = Gauge(
    "trading_open_positions",
    "Number of open positions",
)

fill_slippage_bps = Histogram(
    "trading_fill_slippage_bps",
    "Fill slippage in basis points",
    ["symbol"],
    buckets=[0, 1, 2, 5, 10, 20, 50, 100, 200],
)

order_latency_seconds = Histogram(
    "trading_order_latency_seconds",
    "Time from order submission to first acknowledgement",
    ["broker"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

data_feed_lag_seconds = Gauge(
    "trading_data_feed_lag_seconds",
    "Age of last received bar in seconds",
    ["symbol"],
)

broker_reconnects_total = Counter(
    "trading_broker_reconnects_total",
    "Number of broker reconnection attempts",
    ["broker"],
)


def start_metrics_server(port: int = 9090) -> None:
    """Start the Prometheus HTTP metrics server."""
    start_http_server(port)
