"""Strategy registry."""
from __future__ import annotations

from typing import Type

from packages.core.models import StrategyConfig
from packages.strategies.base import BaseStrategy
from packages.strategies.momentum_breakout import MomentumBreakoutStrategy
from packages.strategies.vwap_reversion import VWAPReversionStrategy
from packages.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from packages.strategies.relative_volume_momentum import RelativeVolumeMomentumStrategy

STRATEGY_REGISTRY: dict[str, Type[BaseStrategy]] = {
    "MomentumBreakoutStrategy": MomentumBreakoutStrategy,
    "VWAPReversionStrategy": VWAPReversionStrategy,
    "OpeningRangeBreakoutStrategy": OpeningRangeBreakoutStrategy,
    "RelativeVolumeMomentumStrategy": RelativeVolumeMomentumStrategy,
}


def load_strategy(config: StrategyConfig) -> BaseStrategy:
    """Instantiate a strategy by class name."""
    cls = STRATEGY_REGISTRY.get(config.cls_name)
    if cls is None:
        raise ValueError(
            f"Unknown strategy: {config.cls_name!r}. "
            f"Available: {list(STRATEGY_REGISTRY)}"
        )
    return cls(config)


def list_strategies() -> list[str]:
    return list(STRATEGY_REGISTRY.keys())
