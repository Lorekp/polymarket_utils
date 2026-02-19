"""Configuration loader with pydantic validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class VolumeFilterConfig(BaseModel):
    min_volume: float = 10_000
    max_volume: float = 500_000


class PositionSizingConfig(BaseModel):
    fixed_size_usd: float = 10.0
    max_position_pct: float = 0.05
    max_total_exposure_pct: float = 0.50


class NewMarketStrategyConfig(BaseModel):
    enabled: bool = True
    weight: float = 2.0
    max_age_hours: int = 48
    min_edge: float = 0.05
    preferred_duration_days_min: int = 7
    preferred_duration_days_max: int = 120
    take_profit: float = 0.20
    stop_loss: float = 0.30


class BreakoutStrategyConfig(BaseModel):
    enabled: bool = True
    weight: float = 1.5
    threshold_pp: float = 0.10
    lookback_hours: int = 24
    hold_hours: int = 48


class CategoryStrategyConfig(BaseModel):
    enabled: bool = True
    weight: float = 1.0
    bias_overrides: dict[str, float] = Field(default_factory=lambda: {
        "politics": 0.026,
        "crypto": 0.010,
        "sports": 0.000,
        "economics": -0.010,
        "science_tech": 0.005,
        "entertainment": 0.005,
        "world_events": 0.000,
    })


class LateStageStrategyConfig(BaseModel):
    enabled: bool = True
    weight: float = 1.5
    days_before_close: int = 7
    min_move_pp: float = 0.05
    large_move_pp: float = 0.15


class StrategiesConfig(BaseModel):
    new_market: NewMarketStrategyConfig = NewMarketStrategyConfig()
    breakout: BreakoutStrategyConfig = BreakoutStrategyConfig()
    category: CategoryStrategyConfig = CategoryStrategyConfig()
    late_stage: LateStageStrategyConfig = LateStageStrategyConfig()


class BotConfig(BaseModel):
    mode: Literal["simulation", "live"] = "simulation"
    scan_interval_seconds: int = 300
    simulation_bankroll: float = 10_000.0
    volume_filter: VolumeFilterConfig = VolumeFilterConfig()
    position_sizing: PositionSizingConfig = PositionSizingConfig()
    strategies: StrategiesConfig = StrategiesConfig()
    private_key: str | None = None
    funder_address: str | None = None


def load_config(config_path: str = "config.json") -> BotConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path.resolve()}")
    with open(path) as f:
        raw = json.load(f)
    return BotConfig(**raw)
