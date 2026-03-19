"""Tests for core domain models."""
from __future__ import annotations

from decimal import Decimal

import pytest

from packages.core.enums import BrokerName, OrderSide, OrderStatus, OrderType
from packages.core.models import Bar, Fill, Order, Position, Quote
from packages.core.utils import (
    generate_client_order_id,
    redact_secrets,
    round_price,
    safe_divide,
    to_decimal,
    validate_symbol,
)
from tests.fixtures.factories import make_bar, make_fill, make_order, make_position, make_quote


def test_bar_mid():
    bar = make_bar(close=450.0)
    assert bar.mid == (bar.high + bar.low) / 2


def test_quote_spread():
    q = make_quote(bid=449.95, ask=450.05)
    assert q.spread == Decimal("0.10")
    assert q.mid == Decimal("450.00")


def test_quote_spread_bps():
    q = make_quote(bid=449.95, ask=450.05)
    assert float(q.spread_bps) == pytest.approx(2.22, abs=0.1)


def test_order_is_terminal_filled():
    o = make_order(status=OrderStatus.filled)
    assert o.is_terminal is True


def test_order_is_terminal_pending():
    o = make_order(status=OrderStatus.pending)
    assert o.is_terminal is False


def test_order_remaining_qty():
    from packages.core.models import Order
    from packages.core.enums import OrderType, TimeInForce
    from uuid import uuid4
    order = Order(
        client_order_id="test",
        symbol="SPY",
        side=OrderSide.buy,
        order_type=OrderType.market,
        qty=Decimal("10"),
        filled_qty=Decimal("6"),
        status=OrderStatus.partially_filled,
        broker=BrokerName.simulator,
    )
    assert order.remaining_qty == Decimal("4")


def test_position_unrealized_pnl():
    pos = make_position(qty=10.0, avg_entry_price=450.0, current_price=460.0)
    assert pos.unrealized_pnl == Decimal("100")


def test_position_side_long():
    pos = make_position(qty=10.0)
    assert pos.side == "long"


def test_position_side_flat():
    pos = make_position(qty=0.0)
    assert pos.side == "flat"


def test_position_update_price():
    pos = make_position(qty=5.0, avg_entry_price=100.0, current_price=100.0)
    pos.update_price(Decimal("110"))
    assert pos.current_price == Decimal("110")
    assert pos.unrealized_pnl == Decimal("50")


# ------------------------------------------------------------------ #
# Utils
# ------------------------------------------------------------------ #
def test_generate_client_order_id_unique():
    ids = {generate_client_order_id("strat", "SPY") for _ in range(100)}
    assert len(ids) == 100


def test_redact_secrets():
    data = {"api_key": "secret123", "symbol": "SPY", "token": "tok"}
    redacted = redact_secrets(data)
    assert redacted["api_key"] == "***"
    assert redacted["token"] == "***"
    assert redacted["symbol"] == "SPY"


def test_to_decimal_valid():
    assert to_decimal("123.45") == Decimal("123.45")


def test_to_decimal_none():
    assert to_decimal(None) == Decimal("0")


def test_to_decimal_invalid():
    assert to_decimal("not_a_number") == Decimal("0")


def test_round_price():
    assert round_price(Decimal("450.123"), Decimal("0.01")) == Decimal("450.12")


def test_safe_divide_zero():
    assert safe_divide(Decimal("10"), Decimal("0")) == Decimal("0")


def test_safe_divide_normal():
    assert safe_divide(Decimal("10"), Decimal("4")) == Decimal("2.5")


def test_validate_symbol_valid():
    assert validate_symbol("spy") == "SPY"


def test_validate_symbol_invalid():
    with pytest.raises(ValueError):
        validate_symbol("SP Y!")
