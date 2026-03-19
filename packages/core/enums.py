"""Platform-wide enumerations."""
from enum import Enum


class TradingMode(str, Enum):
    backtest = "backtest"
    paper = "paper"
    live_dry_run = "live_dry_run"
    live_guarded = "live_guarded"


class OrderSide(str, Enum):
    buy = "buy"
    sell = "sell"


class OrderType(str, Enum):
    market = "market"
    limit = "limit"
    stop = "stop"
    stop_limit = "stop_limit"


class OrderStatus(str, Enum):
    pending = "pending"
    acknowledged = "acknowledged"
    partially_filled = "partially_filled"
    filled = "filled"
    canceled = "canceled"
    rejected = "rejected"
    expired = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in {
            OrderStatus.filled,
            OrderStatus.canceled,
            OrderStatus.rejected,
            OrderStatus.expired,
        }


class TimeInForce(str, Enum):
    day = "day"
    gtc = "gtc"
    ioc = "ioc"
    fok = "fok"


class BrokerName(str, Enum):
    alpaca = "alpaca"
    ibkr = "ibkr"
    coinbase = "coinbase"
    simulator = "simulator"


class Interval(str, Enum):
    sec_1 = "1s"
    min_1 = "1m"
    min_5 = "5m"
    min_15 = "15m"
    min_30 = "30m"
    hour_1 = "1h"
    day_1 = "1d"


class SignalDirection(str, Enum):
    long = "long"
    short = "short"
    flat = "flat"


class StrategyState(str, Enum):
    idle = "idle"
    active = "active"
    paused = "paused"
    stopped = "stopped"
    error = "error"


class RiskEvent(str, Enum):
    kill_switch = "kill_switch"
    daily_loss_limit = "daily_loss_limit"
    daily_drawdown = "daily_drawdown"
    stale_data = "stale_data"
    duplicate_order = "duplicate_order"
    wash_cooldown = "wash_cooldown"
    flip_cooldown = "flip_cooldown"
    spread_guard = "spread_guard"
    max_position_size = "max_position_size"
    max_open_positions = "max_open_positions"
    gross_exposure = "gross_exposure"
    net_exposure = "net_exposure"
    session_cutoff = "session_cutoff"
    allowed = "allowed"


class AlertLevel(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"
