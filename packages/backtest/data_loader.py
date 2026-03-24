"""Historical data loading: CSV files, synthetic generator, Alpaca API."""
from __future__ import annotations

import csv
import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from packages.core.enums import Interval
from packages.core.models import Bar


# ------------------------------------------------------------------ #
# CSV loader
# ------------------------------------------------------------------ #

def load_csv(
    path: str | Path,
    symbol: str,
    interval: Interval = Interval.min_1,
    timestamp_col: str = "timestamp",
    open_col: str = "open",
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    volume_col: str = "volume",
    vwap_col: Optional[str] = "vwap",
) -> list[Bar]:
    """
    Load bars from a CSV file.

    Expected columns (names configurable):
        timestamp, open, high, low, close, volume[, vwap]

    Timestamp formats supported:
        - ISO 8601: "2024-01-02T09:31:00+00:00"
        - Space-separated: "2024-01-02 09:31:00"
        - Unix epoch (float/int seconds)
    """
    bars: list[Bar] = []
    path = Path(path)

    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = _parse_timestamp(row[timestamp_col])
            vwap = None
            if vwap_col and vwap_col in row and row[vwap_col]:
                vwap = Decimal(row[vwap_col])

            bars.append(Bar(
                symbol=symbol,
                timestamp=ts,
                open=Decimal(row[open_col]),
                high=Decimal(row[high_col]),
                low=Decimal(row[low_col]),
                close=Decimal(row[close_col]),
                volume=Decimal(row[volume_col]),
                vwap=vwap,
                interval=interval,
                source="csv",
            ))

    return sorted(bars, key=lambda b: b.timestamp)


def _parse_timestamp(value: str) -> datetime:
    value = value.strip()
    # Try various formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # Try unix epoch
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (ValueError, OSError):
        pass
    raise ValueError(f"Cannot parse timestamp: {value!r}")


def save_csv(bars: list[Bar], path: str | Path) -> None:
    """Save a list of bars to CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "vwap"])
        for bar in bars:
            writer.writerow([
                bar.timestamp.isoformat(),
                bar.open, bar.high, bar.low, bar.close, bar.volume,
                bar.vwap or "",
            ])


# ------------------------------------------------------------------ #
# Synthetic data generator
# ------------------------------------------------------------------ #

def generate_synthetic_bars(
    symbol: str,
    n_bars: int = 1000,
    start_price: float = 450.0,
    start_time: Optional[datetime] = None,
    interval_minutes: int = 1,
    drift: float = 0.0,       # daily drift (e.g. 0.001 = slight upward bias)
    volatility: float = 0.002, # per-bar volatility (std dev of returns)
    mean_volume: float = 100_000.0,
    volume_std_pct: float = 0.3,
    seed: Optional[int] = None,
    include_intraday_pattern: bool = True,
) -> list[Bar]:
    """
    Generate realistic synthetic OHLCV bars using a geometric random walk
    with optional intraday volume pattern (U-shaped: high at open/close).

    Args:
        symbol: Ticker symbol
        n_bars: Number of bars to generate
        start_price: Starting close price
        start_time: First bar timestamp (defaults to 2024-01-02 09:31 UTC)
        interval_minutes: Bar interval in minutes
        drift: Per-bar log drift
        volatility: Per-bar log return std dev
        mean_volume: Average bar volume
        volume_std_pct: Volume variability (as fraction of mean)
        seed: Random seed for reproducibility
        include_intraday_pattern: Add U-shaped intraday volume pattern
    """
    if seed is not None:
        random.seed(seed)

    ts = start_time or datetime(2024, 1, 2, 14, 31, tzinfo=timezone.utc)
    price = start_price
    bars: list[Bar] = []

    # Generate session-aware timestamps (skip nights/weekends in a simple way)
    timestamps = _generate_timestamps(ts, n_bars, interval_minutes)

    for i, bar_ts in enumerate(timestamps):
        # Geometric Brownian Motion
        log_return = drift * (interval_minutes / 390) + volatility * random.gauss(0, 1)
        price *= math.exp(log_return)

        # OHLC from close + intrabar noise
        bar_range = price * volatility * random.uniform(0.8, 2.5)
        high = price + bar_range * random.uniform(0.3, 0.7)
        low = price - bar_range * random.uniform(0.3, 0.7)
        open_price = low + (high - low) * random.random()
        close = max(low, min(high, price))

        # Volume with optional intraday U-shape
        vol_scale = 1.0
        if include_intraday_pattern:
            # 390 bars per session; U-shape peaks at open and close
            session_bar = i % 390
            # Gaussian peaks at bar 0 and bar 389
            open_peak = math.exp(-0.5 * (session_bar / 30) ** 2)
            close_peak = math.exp(-0.5 * ((session_bar - 389) / 30) ** 2)
            vol_scale = 1.0 + 2.0 * max(open_peak, close_peak)

        volume = max(1.0, random.gauss(
            mean_volume * vol_scale,
            mean_volume * volume_std_pct * vol_scale,
        ))

        # VWAP approximation
        vwap = (high + low + close) / 3

        bars.append(Bar(
            symbol=symbol,
            timestamp=bar_ts,
            open=Decimal(str(round(open_price, 4))),
            high=Decimal(str(round(high, 4))),
            low=Decimal(str(round(low, 4))),
            close=Decimal(str(round(close, 4))),
            volume=Decimal(str(round(volume, 0))),
            vwap=Decimal(str(round(vwap, 4))),
            interval=Interval.min_1,
            source="synthetic",
        ))

    return bars


def _generate_timestamps(
    start: datetime, n: int, interval_minutes: int
) -> list[datetime]:
    """Generate n bar timestamps, skipping outside regular market hours."""
    timestamps = []
    ts = start
    while len(timestamps) < n:
        # Skip weekends (0=Mon, 6=Sun)
        if ts.weekday() < 5:
            # Market hours: 9:30–16:00 ET = 13:30–20:00 UTC (ignoring DST)
            if (ts.hour, ts.minute) >= (13, 30) and (ts.hour, ts.minute) < (20, 0):
                timestamps.append(ts)
        ts += timedelta(minutes=interval_minutes)
        # If we've gone past market close, jump to next day's open
        if ts.hour >= 20:
            ts = ts.replace(hour=13, minute=31, second=0, microsecond=0)
            ts += timedelta(days=1)
    return timestamps


# ------------------------------------------------------------------ #
# Alpaca historical loader
# ------------------------------------------------------------------ #

def load_from_alpaca(
    symbol: str,
    start: datetime,
    end: datetime,
    api_key: str,
    api_secret: str,
    interval: Interval = Interval.min_1,
    paper: bool = True,
) -> list[Bar]:
    """
    Fetch historical bars from Alpaca Markets API.
    Requires: pip install alpaca-py
    """
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        raise RuntimeError("alpaca-py not installed: pip install alpaca-py")

    _tf_map = {
        Interval.min_1: TimeFrame.Minute,
        Interval.min_5: TimeFrame.Minute,
        Interval.min_15: TimeFrame.Minute,
        Interval.min_30: TimeFrame.Minute,
        Interval.hour_1: TimeFrame.Hour,
        Interval.day_1: TimeFrame.Day,
    }

    client = StockHistoricalDataClient(api_key, api_secret)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=_tf_map.get(interval, TimeFrame.Minute),
        start=start,
        end=end,
        limit=10000,
    )
    data = client.get_stock_bars(req)
    bars = []
    for raw in (data.get(symbol) or []):
        bars.append(Bar(
            symbol=symbol,
            timestamp=raw.timestamp if raw.timestamp.tzinfo else raw.timestamp.replace(tzinfo=timezone.utc),
            open=Decimal(str(raw.open)),
            high=Decimal(str(raw.high)),
            low=Decimal(str(raw.low)),
            close=Decimal(str(raw.close)),
            volume=Decimal(str(raw.volume)),
            vwap=Decimal(str(raw.vwap)) if raw.vwap else None,
            interval=interval,
            source="alpaca_historical",
        ))
    return sorted(bars, key=lambda b: b.timestamp)


# ------------------------------------------------------------------ #
# Multi-symbol loader
# ------------------------------------------------------------------ #

def load_multi_symbol_csv(
    directory: str | Path,
    symbols: list[str],
    suffix: str = ".csv",
    **kwargs,
) -> dict[str, list[Bar]]:
    """
    Load bars for multiple symbols from a directory.
    Expects files named: {SYMBOL}.csv (e.g., SPY.csv, QQQ.csv)
    """
    directory = Path(directory)
    result: dict[str, list[Bar]] = {}
    for symbol in symbols:
        path = directory / f"{symbol}{suffix}"
        if path.exists():
            result[symbol] = load_csv(path, symbol, **kwargs)
        else:
            result[symbol] = []
    return result
