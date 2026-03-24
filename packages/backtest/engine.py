"""Event-driven backtesting engine."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from packages.backtest.mock_cache import BacktestCache
from packages.brokers.alpaca_adapter import SimulatorAdapter
from packages.core.enums import OrderSide, OrderType, RiskEvent, SignalDirection
from packages.core.models import Bar, Fill, Order, RiskCheckResult, Signal, StrategyConfig
from packages.core.utils import generate_client_order_id
from packages.observability.logging import get_logger
from packages.strategies.registry import load_strategy
from workers.portfolio.tracker import PortfolioTracker
from workers.risk.engine import RiskEngine


log = get_logger(__name__)


# ------------------------------------------------------------------ #
# Result data structures
# ------------------------------------------------------------------ #

@dataclass
class TradeRecord:
    """A completed round-trip trade (entry + exit)."""
    symbol: str
    strategy_id: str
    side: str  # "long" or "short"
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    gross_pnl: Decimal
    commission: Decimal
    exit_reason: str  # "stop", "target", "eod", "signal"
    bars_held: int

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.commission

    @property
    def is_winner(self) -> bool:
        return self.net_pnl > 0

    @property
    def return_pct(self) -> Decimal:
        cost = self.entry_price * self.qty
        if cost == 0:
            return Decimal("0")
        return self.net_pnl / cost * 100


@dataclass
class EquityPoint:
    timestamp: datetime
    equity: Decimal
    cash: Decimal
    open_positions: int


@dataclass
class RiskEventRecord:
    timestamp: datetime
    event: RiskEvent
    symbol: str
    strategy_id: str
    reason: str


@dataclass
class BacktestResult:
    strategy_names: list[str]
    symbols: list[str]
    start_date: date
    end_date: date
    initial_capital: Decimal
    final_capital: Decimal
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    risk_events: list[RiskEventRecord] = field(default_factory=list)
    blocked_orders: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------ #
# Open position tracking (for stop/target exits)
# ------------------------------------------------------------------ #

@dataclass
class OpenTrade:
    symbol: str
    strategy_id: str
    qty: Decimal
    entry_price: Decimal
    entry_time: datetime
    stop_price: Optional[Decimal]
    target_price: Optional[Decimal]
    commission_paid: Decimal
    entry_bar_index: int


# ------------------------------------------------------------------ #
# Engine
# ------------------------------------------------------------------ #

class BacktestEngine:
    """
    Replays historical bars through strategies, risk engine, and simulator.

    Key behaviours:
    - Bars are processed in strict chronological order across all symbols.
    - Each strategy receives bars only for its subscribed symbols.
    - The risk engine uses BacktestCache (time-aware, no Redis).
    - Session-cutoff check is overridden to use bar-time.
    - Positions are exited when stop or target price is touched on the
      bar's high/low, simulating intrabar fills.
    - All open positions are flattened at end-of-session (16:00 ET).
    """

    SESSION_CLOSE_HOUR_UTC = 21  # 4 PM ET (approx.; ignores DST)

    def __init__(
        self,
        strategy_configs: list[StrategyConfig],
        initial_capital: float = 100_000.0,
        commission_per_share: float = 0.005,
        slippage_bps: float = 2.0,
        warmup_bars: int = 60,
    ) -> None:
        self._strategy_configs = strategy_configs
        self._initial_capital = Decimal(str(initial_capital))
        self._commission = commission_per_share
        self._slippage_bps = slippage_bps
        self._warmup_bars = warmup_bars

        self._cache = BacktestCache()
        self._broker = SimulatorAdapter(
            initial_cash=initial_capital,
            commission_per_share=commission_per_share,
            slippage_bps=slippage_bps,
        )
        self._portfolio = PortfolioTracker(initial_cash=self._initial_capital)
        self._risk = RiskEngine(self._cache, account_equity=self._initial_capital)

        # Patch risk engine: session cutoff uses bar-time, stale data always passes
        self._risk._check_session_cutoff = self._bar_time_session_cutoff
        self._risk._check_stale_data = self._always_fresh

        # Wire fills
        self._broker.register_fill_callback(self._on_fill)

        self._open_trades: dict[str, OpenTrade] = {}  # symbol -> active trade
        self._completed_trades: list[TradeRecord] = []
        self._equity_curve: list[EquityPoint] = []
        self._risk_event_log: list[RiskEventRecord] = []
        self._blocked_orders: list[dict] = []
        self._current_bar_time: datetime = datetime.now(timezone.utc)
        self._bar_counter: dict[str, int] = {}  # symbol -> total bars seen

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def run(self, bars_by_symbol: dict[str, list[Bar]]) -> BacktestResult:
        """Run backtest synchronously. Returns a BacktestResult."""
        return asyncio.get_event_loop().run_until_complete(self._run_async(bars_by_symbol))

    async def _run_async(self, bars_by_symbol: dict[str, list[Bar]]) -> BacktestResult:
        await self._broker.connect()

        # Merge and sort all bars chronologically
        all_bars = sorted(
            [bar for bars in bars_by_symbol.values() for bar in bars],
            key=lambda b: b.timestamp,
        )

        if not all_bars:
            raise ValueError("No bars provided for backtest")

        # Split into warmup + test periods per symbol
        warmup_data: dict[str, list[Bar]] = {sym: [] for sym in bars_by_symbol}
        test_bars: list[Bar] = []

        # Track how many bars each symbol has seen for warmup cutoff
        seen: dict[str, int] = {sym: 0 for sym in bars_by_symbol}
        warmed_up: dict[str, bool] = {sym: False for sym in bars_by_symbol}

        for bar in all_bars:
            seen[bar.symbol] += 1
            if not warmed_up[bar.symbol]:
                warmup_data[bar.symbol].append(bar)
                if seen[bar.symbol] >= self._warmup_bars:
                    warmed_up[bar.symbol] = True
            else:
                test_bars.append(bar)

        # Load strategies and prepare with warmup data
        strategies = [load_strategy(cfg) for cfg in self._strategy_configs]
        for strategy in strategies:
            sym_history = {
                sym: warmup_data.get(sym, [])
                for sym in strategy.config.symbols
            }
            await strategy.prepare(sym_history)

        # Initialise equity tracking
        await self._cache.update_daily_high_equity(float(self._initial_capital))
        self._risk.update_account_equity(self._initial_capital)

        current_date: Optional[date] = None

        # Main replay loop
        for bar_idx, bar in enumerate(test_bars):
            self._current_bar_time = bar.timestamp
            self._cache.advance_time(bar.timestamp)
            self._bar_counter[bar.symbol] = self._bar_counter.get(bar.symbol, 0) + 1

            # Daily reset at session open
            bar_date = bar.timestamp.date()
            if bar_date != current_date:
                current_date = bar_date
                await self._cache.reset_daily_state()
                await self._cache.update_daily_high_equity(float(self._portfolio.equity))
                self._risk.update_daily_pnl(Decimal("0"))

            # Update price in broker and portfolio
            self._broker._last_prices[bar.symbol] = bar.close
            self._portfolio.on_price_update(bar)
            equity = self._portfolio.equity
            self._risk.update_account_equity(equity)
            self._risk.update_positions(self._portfolio.positions)
            self._risk.update_daily_pnl(self._portfolio.realized_pnl)
            await self._cache.update_daily_high_equity(float(equity))

            # Check stop/target exits BEFORE generating new signals
            await self._check_exits(bar)

            # End-of-session flatten
            if self._is_near_session_close(bar.timestamp):
                await self._flatten_all_eod(bar)

            # Feed bar through strategies
            for strategy in strategies:
                if bar.symbol not in strategy.config.symbols:
                    continue
                if not strategy.config.enabled:
                    continue
                try:
                    signal = await strategy.on_bar(bar)
                    if signal is not None:
                        await self._handle_signal(signal)
                except Exception as e:
                    log.error("strategy_error", strategy=strategy.config.cls_name, error=str(e))

            # Record equity curve point (every bar for the first symbol, or all)
            self._equity_curve.append(EquityPoint(
                timestamp=bar.timestamp,
                equity=equity,
                cash=self._portfolio.cash,
                open_positions=len(self._portfolio.positions),
            ))

        # End-of-backtest cleanup
        for strategy in strategies:
            await strategy.on_close()

        start = test_bars[0].timestamp.date() if test_bars else date.today()
        end = test_bars[-1].timestamp.date() if test_bars else date.today()

        return BacktestResult(
            strategy_names=[cfg.cls_name for cfg in self._strategy_configs],
            symbols=list(bars_by_symbol.keys()),
            start_date=start,
            end_date=end,
            initial_capital=self._initial_capital,
            final_capital=self._portfolio.equity,
            trades=list(self._completed_trades),
            equity_curve=list(self._equity_curve),
            risk_events=list(self._risk_event_log),
            blocked_orders=list(self._blocked_orders),
        )

    # ------------------------------------------------------------------ #
    # Signal handling
    # ------------------------------------------------------------------ #

    async def _handle_signal(self, signal: Signal) -> None:
        # Skip if already in a position for this symbol
        if signal.symbol in self._open_trades:
            return

        # Compute qty from ATR-based risk if not provided
        qty = signal.suggested_qty
        equity = self._portfolio.equity
        from packages.core.config import get_settings
        cfg = get_settings()
        if not qty or qty <= 0:
            risk_dollars = equity * cfg.risk.risk_per_trade_pct
            if signal.entry_price and signal.stop_price:
                risk_per_share = abs(signal.entry_price - signal.stop_price)
                if risk_per_share > 0:
                    qty = (risk_dollars / risk_per_share).quantize(Decimal("1"))
            if not qty or qty <= 0:
                qty = Decimal("1")

        # Cap at max position size — use floor to guarantee notional stays under limit
        if signal.entry_price and signal.entry_price > 0:
            from decimal import ROUND_DOWN
            max_notional = equity * cfg.risk.max_position_size_pct
            max_qty = (max_notional / signal.entry_price).to_integral_value(rounding=ROUND_DOWN)
            qty = min(qty, max(max_qty, Decimal("1")))

        signal.suggested_qty = qty

        coid = generate_client_order_id(signal.strategy_id, signal.symbol)
        order = Order(
            client_order_id=coid,
            symbol=signal.symbol,
            side=OrderSide.buy if signal.direction == SignalDirection.long else OrderSide.sell,
            order_type=OrderType.market,
            qty=qty,
            broker="simulator",
            strategy_id=signal.strategy_id,
        )

        result = await self._risk.check_pre_trade(signal, order)
        if not result.passed:
            self._risk_event_log.append(RiskEventRecord(
                timestamp=self._current_bar_time,
                event=result.risk_event,
                symbol=signal.symbol,
                strategy_id=signal.strategy_id,
                reason=result.blocked_reason or "",
            ))
            self._blocked_orders.append({
                "timestamp": self._current_bar_time.isoformat(),
                "symbol": signal.symbol,
                "reason": result.blocked_reason,
                "event": result.risk_event.value,
            })
            return

        submitted = await self._broker.submit_order(
            symbol=order.symbol,
            side=order.side,
            qty=float(order.qty),
            order_type=order.order_type,
            client_order_id=coid,
            strategy_id=signal.strategy_id,
        )

        if submitted.avg_fill_price:
            self._open_trades[signal.symbol] = OpenTrade(
                symbol=signal.symbol,
                strategy_id=signal.strategy_id,
                qty=submitted.qty,
                entry_price=submitted.avg_fill_price,
                entry_time=self._current_bar_time,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                commission_paid=submitted.qty * Decimal(str(self._commission)),
                entry_bar_index=self._bar_counter.get(signal.symbol, 0),
            )

    # ------------------------------------------------------------------ #
    # Exit management
    # ------------------------------------------------------------------ #

    async def _check_exits(self, bar: Bar) -> None:
        trade = self._open_trades.get(bar.symbol)
        if not trade:
            return

        exit_price: Optional[Decimal] = None
        exit_reason = ""

        # Target hit (use bar high for long)
        if trade.target_price and bar.high >= trade.target_price:
            exit_price = trade.target_price
            exit_reason = "target"

        # Stop hit (use bar low for long)
        elif trade.stop_price and bar.low <= trade.stop_price:
            exit_price = trade.stop_price
            exit_reason = "stop"

        if exit_price:
            await self._exit_trade(trade, exit_price, exit_reason, bar.timestamp)

    async def _flatten_all_eod(self, bar: Bar) -> None:
        trade = self._open_trades.get(bar.symbol)
        if trade:
            await self._exit_trade(trade, bar.close, "eod", bar.timestamp)

    async def _exit_trade(
        self,
        trade: OpenTrade,
        exit_price: Decimal,
        reason: str,
        ts: datetime,
    ) -> None:
        # Apply slippage to exit
        slippage = Decimal(str(self._slippage_bps)) / Decimal("10000")
        # Selling: slippage reduces proceeds
        actual_exit = exit_price * (1 - slippage)

        # Submit exit order through broker
        exit_order = await self._broker.submit_order(
            symbol=trade.symbol,
            side=OrderSide.sell,
            qty=float(trade.qty),
            order_type=OrderType.market,
            strategy_id=trade.strategy_id,
        )

        exit_commission = trade.qty * Decimal(str(self._commission))
        gross_pnl = trade.qty * (actual_exit - trade.entry_price)
        total_commission = trade.commission_paid + exit_commission
        bars_held = self._bar_counter.get(trade.symbol, 0) - trade.entry_bar_index

        self._completed_trades.append(TradeRecord(
            symbol=trade.symbol,
            strategy_id=trade.strategy_id,
            side="long",
            entry_time=trade.entry_time,
            exit_time=ts,
            entry_price=trade.entry_price,
            exit_price=actual_exit,
            qty=trade.qty,
            gross_pnl=gross_pnl,
            commission=total_commission,
            exit_reason=reason,
            bars_held=bars_held,
        ))

        del self._open_trades[trade.symbol]

        # Update risk state post-exit
        self._risk.update_positions(self._portfolio.positions)
        self._risk.update_account_equity(self._portfolio.equity)
        self._risk.update_daily_pnl(self._portfolio.realized_pnl)

    # ------------------------------------------------------------------ #
    # Fill callback
    # ------------------------------------------------------------------ #

    def _on_fill(self, fill: Fill) -> None:
        self._portfolio.on_fill(fill)

    # ------------------------------------------------------------------ #
    # Risk engine patches
    # ------------------------------------------------------------------ #

    async def _bar_time_session_cutoff(self, signal: Signal, order: Order) -> RiskCheckResult:
        """Session cutoff using bar timestamp instead of wall clock."""
        from datetime import timedelta
        from packages.core.config import get_settings
        cutoff_min = get_settings().risk.session_cutoff_minutes
        close_utc = self._current_bar_time.replace(
            hour=self.SESSION_CLOSE_HOUR_UTC, minute=0, second=0, microsecond=0
        )
        cutoff_dt = close_utc - timedelta(minutes=cutoff_min)
        if self._current_bar_time >= cutoff_dt:
            return RiskCheckResult.block(
                f"Session cutoff ({cutoff_min} min before close)",
                RiskEvent.session_cutoff,
            )
        return RiskCheckResult.ok()

    async def _always_fresh(self, signal: Signal, order: Order) -> RiskCheckResult:
        """Replayed data is always fresh — skip stale-data check."""
        return RiskCheckResult.ok()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _is_near_session_close(self, ts: datetime) -> bool:
        """True for bars at or after 3:58 PM ET (20:58 UTC)."""
        return ts.hour >= self.SESSION_CLOSE_HOUR_UTC and ts.minute >= 58
