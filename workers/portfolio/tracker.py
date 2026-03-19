"""Portfolio tracker — real-time position and PnL accounting."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from packages.core.enums import OrderSide
from packages.core.models import Bar, DailyReport, Fill, Position
from packages.observability.logging import get_logger
from packages.observability.metrics import daily_pnl, open_positions, portfolio_value


log = get_logger(__name__)


class PortfolioTracker:
    def __init__(self, initial_cash: Decimal = Decimal("100000")) -> None:
        self._cash = initial_cash
        self._positions: dict[str, Position] = {}
        self._realized_pnl: Decimal = Decimal("0")
        self._total_commission: Decimal = Decimal("0")
        self._fills: list[Fill] = []
        self._trades_today: int = 0
        self._winning_trades: int = 0
        self._losing_trades: int = 0

    # ------------------------------------------------------------------ #
    # Fill processing
    # ------------------------------------------------------------------ #
    def on_fill(self, fill: Fill) -> None:
        self._fills.append(fill)
        self._total_commission += fill.commission

        if fill.side == OrderSide.buy:
            cost = fill.qty * fill.price + fill.commission
            self._cash -= cost
        else:
            proceeds = fill.qty * fill.price - fill.commission
            self._cash += proceeds

        self._apply_fill_to_position(fill)
        self._update_metrics(fill.strategy_id)
        log.info(
            "fill_applied",
            symbol=fill.symbol,
            side=fill.side.value,
            qty=float(fill.qty),
            price=float(fill.price),
            commission=float(fill.commission),
        )

    def _apply_fill_to_position(self, fill: Fill) -> None:
        pos = self._positions.get(fill.symbol, Position(symbol=fill.symbol))

        if fill.side == OrderSide.buy:
            if pos.qty < 0:
                # Closing or flipping a short
                close_qty = min(fill.qty, abs(pos.qty))
                pnl = close_qty * (pos.avg_entry_price - fill.price)
                self._record_trade_result(pnl)
                self._realized_pnl += pnl
                pos.qty += fill.qty
                if pos.qty > 0:
                    pos.avg_entry_price = fill.price
                elif pos.qty == 0:
                    pos.avg_entry_price = Decimal("0")
            else:
                if pos.qty == 0:
                    pos.avg_entry_price = fill.price
                    pos.qty = fill.qty
                    pos.opened_at = fill.timestamp
                else:
                    total_cost = pos.qty * pos.avg_entry_price + fill.qty * fill.price
                    pos.qty += fill.qty
                    pos.avg_entry_price = total_cost / pos.qty
        else:  # sell
            if pos.qty > 0:
                close_qty = min(fill.qty, pos.qty)
                pnl = close_qty * (fill.price - pos.avg_entry_price)
                self._record_trade_result(pnl)
                self._realized_pnl += pnl
                pos.qty -= fill.qty
                if pos.qty <= 0:
                    pos.qty = Decimal("0") if pos.qty == 0 else pos.qty
                    if pos.qty == 0:
                        pos.avg_entry_price = Decimal("0")
            else:
                if pos.qty == 0:
                    pos.avg_entry_price = fill.price
                    pos.qty = -fill.qty
                    pos.opened_at = fill.timestamp
                else:
                    total_cost = abs(pos.qty) * pos.avg_entry_price + fill.qty * fill.price
                    pos.qty -= fill.qty
                    pos.avg_entry_price = total_cost / abs(pos.qty)

        self._positions[fill.symbol] = pos

    def _record_trade_result(self, pnl: Decimal) -> None:
        self._trades_today += 1
        if pnl > 0:
            self._winning_trades += 1
        elif pnl < 0:
            self._losing_trades += 1

    # ------------------------------------------------------------------ #
    # Price updates
    # ------------------------------------------------------------------ #
    def on_price_update(self, bar: Bar) -> None:
        if bar.symbol in self._positions:
            self._positions[bar.symbol].update_price(bar.close)
        self._update_metrics()

    # ------------------------------------------------------------------ #
    # Accessors
    # ------------------------------------------------------------------ #
    @property
    def positions(self) -> dict[str, Position]:
        return {k: v for k, v in self._positions.items() if v.qty != 0}

    @property
    def equity(self) -> Decimal:
        # cash + market value of all open positions
        market_value = sum(abs(p.qty) * p.current_price for p in self._positions.values())
        return self._cash + market_value

    @property
    def realized_pnl(self) -> Decimal:
        return self._realized_pnl

    @property
    def cash(self) -> Decimal:
        return self._cash

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    # ------------------------------------------------------------------ #
    # Reports
    # ------------------------------------------------------------------ #
    def generate_daily_report(self, strategy_id: Optional[str] = None) -> DailyReport:
        return DailyReport(
            date=date.today(),
            strategy_id=strategy_id,
            total_trades=self._trades_today,
            winning_trades=self._winning_trades,
            losing_trades=self._losing_trades,
            gross_pnl=self._realized_pnl,
            net_pnl=self._realized_pnl - self._total_commission,
            total_commission=self._total_commission,
        )

    def reset_daily_state(self) -> None:
        self._trades_today = 0
        self._winning_trades = 0
        self._losing_trades = 0
        self._realized_pnl = Decimal("0")
        self._total_commission = Decimal("0")

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #
    def _update_metrics(self, strategy_id: str = "default") -> None:
        eq = float(self.equity)
        portfolio_value.set(eq)
        open_pos = sum(1 for p in self._positions.values() if p.qty != 0)
        open_positions.set(open_pos)
        daily_pnl.labels(strategy_id=strategy_id).set(float(self._realized_pnl))
