"""Shared utilities."""
from __future__ import annotations

import re
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


_SECRET_PATTERNS = re.compile(
    r"(api[_-]?key|secret|password|token|bearer|authorization|credential)",
    re.IGNORECASE,
)


def generate_client_order_id(strategy_id: str = "", symbol: str = "") -> str:
    """Generate a unique, deterministic client order ID."""
    parts = [p for p in [strategy_id, symbol] if p]
    prefix = "-".join(parts) + "-" if parts else ""
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* with sensitive values replaced by '***'."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if _SECRET_PATTERNS.search(str(k)):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = redact_secrets(v)
        else:
            out[k] = v
    return out


def to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Safely convert *value* to Decimal."""
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def round_price(price: Decimal, tick: Decimal = Decimal("0.01")) -> Decimal:
    """Round *price* to nearest *tick*."""
    if tick <= 0:
        return price
    return (price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick


def safe_divide(numerator: Decimal, denominator: Decimal, default: Decimal = Decimal("0")) -> Decimal:
    """Return numerator/denominator or *default* if denominator is zero."""
    if denominator == 0:
        return default
    return numerator / denominator


_SYMBOL_RE = re.compile(r"^[A-Z]{1,10}$")


def validate_symbol(symbol: str) -> str:
    """Validate and normalise an equity symbol."""
    upper = symbol.strip().upper()
    if not _SYMBOL_RE.match(upper):
        raise ValueError(f"Invalid symbol: {symbol!r}")
    return upper
