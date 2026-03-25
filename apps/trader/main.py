"""Trading bot orchestrator."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from decimal import Decimal
from typing import Optional

from packages.alerting.base import Alert, AlertLevel
from packages.alerting.router import AlertRouter
from packages.alerting.slack import SlackChannel
from packages.brokers.alpaca_adapter import AlpacaAdapter, SimulatorAdapter
from packages.brokers.base import BaseBroker
from packages.core.config import Settings, get_settings
from packages.core.enums import BrokerName, Interval, TradingMode
from packages.core.models import Bar, StrategyConfig
from packages.data.database import close_engine, create_tables
from packages.data.redis_client import RedisCache
from packages.observability.logging import configure_logging, get_logger
from packages.observability.metrics import start_metrics_server
from packages.strategies.base import BaseStrategy
from packages.strategies.registry import load_strategy
from workers.execution.engine import ExecutionEngine
from workers.market_data.ingestion import MarketDataWorker
from workers.portfolio.tracker import PortfolioTracker
from workers.risk.engine import RiskEngine
from workers.session.scheduler import SessionScheduler


log = get_logger(__name__)


class TradingBot:
    def __init__(
        self,
        settings: Settings,
        broker: BaseBroker,
        strategy_configs: list[StrategyConfig],
        alert_router: Optional[AlertRouter] = None,
    ) -> None:
        self._settings = settings
        self._broker = broker
        self._cache = RedisCache(settings.redis_url)
        self._alerts = alert_router or AlertRouter()

        self._portfolio = PortfolioTracker()
        self._risk = RiskEngine(self._cache)

        self._execution = ExecutionEngine(
            broker=broker,
            risk_engine=self._risk,
            portfolio=self._portfolio,
            trading_mode=settings.trading_mode,
            database_url=settings.database_url,
        )

        self._strategies: list[BaseStrategy] = [
            load_strategy(cfg) for cfg in strategy_configs
        ]

        symbols = {s for cfg in strategy_configs for s in cfg.symbols}
        self._market_data = MarketDataWorker(
            broker=broker,
            cache=self._cache,
            symbols=list(symbols),
            interval=Interval.min_1,
        )
        self._market_data.register_bar_callback(self._process_bar)

        # Wire simulator fills
        if hasattr(broker, "register_fill_callback"):
            broker.register_fill_callback(self._execution.on_fill_received)

        self._scheduler = SessionScheduler(
            on_session_open=self._on_session_open,
            on_eod_flatten=self._on_eod_flatten,
            on_eod_report=self._on_eod_report,
        )
        self._running = False

    async def start(self) -> None:
        """Full startup sequence."""
        settings = self._settings
        log.info(
            "bot_starting",
            mode=settings.trading_mode.value,
            broker=settings.broker.value,
        )

        await self._cache.connect()
        await self._cache.reset_daily_state()
        await self._broker.connect()

        # Load account state
        account = await self._broker.get_account()
        self._portfolio._cash = account.cash
        self._risk.update_account_equity(account.equity)
        await self._cache.update_daily_high_equity(float(account.equity))

        # Reconcile
        await self._execution.reconcile()

        # Fetch history and prepare strategies
        history = await self._market_data.fetch_history()
        for strategy in self._strategies:
            sym_history = {s: history.get(s, []) for s in strategy.config.symbols}
            await strategy.prepare(sym_history)

        # Start streaming
        await self._market_data.start_streaming()

        self._running = True
        log.info("bot_ready", strategies=[s.config.cls_name for s in self._strategies])

        await self._alerts.info(
            title="Bot Started",
            body=(
                f"Mode: {settings.trading_mode.value} | "
                f"Broker: {settings.broker.value} | "
                f"Strategies: {[s.config.cls_name for s in self._strategies]}"
            ),
        )

        # Main loop — also drives the session scheduler
        try:
            await asyncio.gather(
                self._main_loop(),
                self._scheduler.start(),
            )
        finally:
            await self.stop()

    async def _main_loop(self) -> None:
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        log.info("bot_stopping")
        self._running = False
        await self._scheduler.stop()
        await self._market_data.stop()
        for strategy in self._strategies:
            await strategy.on_close()

        # End-of-day flatten (configurable)
        if self._settings.trading_mode != TradingMode.backtest:
            positions = self._portfolio.positions
            if positions:
                log.warning("open_positions_at_close", count=len(positions))

        report = self._portfolio.generate_daily_report()
        log.info(
            "daily_report",
            total_trades=report.total_trades,
            gross_pnl=float(report.gross_pnl),
            net_pnl=float(report.net_pnl),
        )

        await self._alerts.info(
            title="Bot Stopped",
            body=(
                f"Trades: {report.total_trades} | "
                f"Net PnL: ${float(report.net_pnl):+.2f}"
            ),
        )

        await self._broker.disconnect()
        await self._cache.close()
        await close_engine()

    # ------------------------------------------------------------------ #
    # Scheduler callbacks
    # ------------------------------------------------------------------ #
    async def _on_session_open(self) -> None:
        log.info("session_open_triggered")
        await self._cache.reset_daily_state()
        account = await self._broker.get_account()
        self._risk.update_account_equity(account.equity)
        await self._cache.update_daily_high_equity(float(account.equity))
        await self._alerts.info(
            title="Market Open — Daily Reset",
            body=f"Equity: ${float(account.equity):,.2f} | Cash: ${float(account.cash):,.2f}",
        )

    async def _on_eod_flatten(self) -> None:
        log.warning("eod_flatten_triggered")
        positions = self._portfolio.positions
        if not positions:
            log.info("eod_flatten_no_positions")
            return
        orders = await self._execution.flatten_all()
        await self._alerts.warning(
            title="EOD Flatten",
            body=f"Closed {len(positions)} position(s) via {len(orders)} order(s)",
        )

    async def _on_eod_report(self) -> None:
        report = self._portfolio.generate_daily_report()
        blocked = len(self._execution.blocked_orders)
        win_rate = (
            report.winning_trades / report.total_trades * 100
            if report.total_trades > 0
            else 0.0
        )
        body = (
            f"Trades: {report.total_trades} | "
            f"Win rate: {win_rate:.1f}% | "
            f"Gross PnL: ${float(report.gross_pnl):+,.2f} | "
            f"Net PnL: ${float(report.net_pnl):+,.2f} | "
            f"Blocked: {blocked}"
        )
        level = (
            AlertLevel.warning if float(report.net_pnl) < 0 else AlertLevel.info
        )
        await self._alerts.send(Alert(title="EOD Report", body=body, level=level))

    def _process_bar(self, bar: Bar) -> None:
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda: asyncio.create_task(self._handle_bar_async(bar))
        )

    async def _handle_bar_async(self, bar: Bar) -> None:
        """Process a bar through all relevant strategies."""
        for strategy in self._strategies:
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

    async def _handle_signal(self, signal) -> None:
        from packages.core.config import get_settings
        cfg = get_settings()

        # Basic position sizing if not already set
        if not signal.suggested_qty or signal.suggested_qty <= 0:
            equity = self._portfolio.equity
            risk_per_trade = equity * cfg.risk.risk_per_trade_pct
            if signal.entry_price and signal.stop_price:
                risk_per_share = abs(signal.entry_price - signal.stop_price)
                if risk_per_share > 0:
                    qty = (risk_per_trade / risk_per_share).quantize(Decimal("1"))
                    signal.suggested_qty = max(qty, Decimal("1"))
            if not signal.suggested_qty:
                signal.suggested_qty = Decimal("1")

        await self._execution.submit_signal(signal)


def create_bot_from_env() -> TradingBot:
    """Factory to build a TradingBot from environment settings."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_dir)

    # Live trading confirmation
    if settings.trading_mode == TradingMode.live_guarded:
        token = os.environ.get("LIVE_CONFIRMATION_TOKEN", "")
        entered = input("Enter live trading confirmation token: ").strip()
        if entered != token:
            print("Token mismatch. Aborting.")
            sys.exit(1)

    # Build broker
    broker: BaseBroker
    if settings.broker == BrokerName.alpaca:
        if not settings.alpaca_api_key or not settings.alpaca_api_secret:
            raise ValueError("ALPACA_API_KEY and ALPACA_API_SECRET required")
        paper = settings.trading_mode != TradingMode.live_guarded
        broker = AlpacaAdapter(
            api_key=settings.alpaca_api_key,
            api_secret=settings.alpaca_api_secret,
            base_url=settings.alpaca_base_url,
            paper=paper,
        )
    else:
        broker = SimulatorAdapter()

    # Build alert router (Slack optional)
    router = AlertRouter()
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_webhook:
        router.add_channel(SlackChannel(webhook_url=slack_webhook), min_level=AlertLevel.info)

    return TradingBot(
        settings=settings,
        broker=broker,
        strategy_configs=default_strategy_configs(settings),
        alert_router=router,
    )


def default_strategy_configs(settings: Settings) -> list[StrategyConfig]:
    symbols = settings.allowed_symbols[:5]
    return [
        StrategyConfig(
            cls_name="MomentumBreakoutStrategy",
            symbols=symbols,
            params={
                "min_bars": 30,
                "breakout_period": 20,
                "rvol_threshold": 1.5,
                "atr_stop_mult": 1.5,
                "atr_tp_mult": 3.0,
            },
        )
    ]


if __name__ == "__main__":
    bot = create_bot_from_env()
    asyncio.run(bot.start())
