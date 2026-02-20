"""Configuration loader with pydantic validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VolumeFilterConfig(BaseModel):
    min_volume: float = 10_000
    max_volume: float = 500_000
    max_spread: float = 0.05


class PositionSizingConfig(BaseModel):
    fixed_size_usd: float = Field(10.0, gt=0)
    max_position_pct: float = Field(0.05, gt=0, le=1.0)
    max_total_exposure_pct: float = Field(0.30, gt=0, le=1.0)
    daily_loss_limit_pct: float = Field(0.05, gt=0, le=1.0)


class NewMarketStrategyConfig(BaseModel):
    enabled: bool = True
    weight: float = 1.0
    max_age_hours: int = 48
    min_edge: float = 0.05
    min_price: float = 0.35
    max_price: float = 0.65
    preferred_duration_days_min: int = 7
    preferred_duration_days_max: int = 120
    take_profit: float = 0.20
    stop_loss: float = 0.30


class BreakoutStrategyConfig(BaseModel):
    enabled: bool = False
    weight: float = 1.0
    threshold_pp: float = 0.10
    lookback_hours: int = 24
    hold_hours: int = 48


class CategoryStrategyConfig(BaseModel):
    enabled: bool = True
    weight: float = 2.0
    min_abs_bias: float = 0.03
    min_net_edge: float = 0.01
    min_composite_score: float = 0.5
    zone_biases: dict[str, dict[str, float]] = Field(default_factory=lambda: {
        "politics": {"unlikely": 0.129, "tossup": 0.084},
        "science_tech": {"tossup": -0.077, "unlikely": -0.062},
        "crypto": {"likely": 0.048, "tossup": 0.046},
    })
    half_kelly_fractions: dict[str, float] = Field(default_factory=lambda: {
        "politics_unlikely": 0.082,
        "politics_tossup": 0.070,
        "science_tech_tossup": 0.066,
        "science_tech_unlikely": 0.076,
        "crypto_tossup": 0.030,
        "crypto_likely": 0.045,
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


class ScoringConfig(BaseModel):
    """Time-priority scoring parameters (capital-efficiency curve)."""

    min_days: float = 0.5
    too_close_factor: float = 0.05
    long_horizon_days: float = 90.0
    long_horizon_floor: float = 0.05
    no_end_date_factor: float = 0.1


class BotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["simulation", "live"] = "simulation"
    scan_interval_seconds: int = 300
    simulation_bankroll: float = Field(10_000.0, gt=0)
    volume_filter: VolumeFilterConfig = VolumeFilterConfig()
    position_sizing: PositionSizingConfig = PositionSizingConfig()
    strategies: StrategiesConfig = StrategiesConfig()
    scoring: ScoringConfig = ScoringConfig()
    private_key: str | None = None
    funder_address: str | None = None


def load_config(config_path: str = "config.json") -> BotConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path.resolve()}")
    with open(path) as f:
        raw = json.load(f)
    return BotConfig(**raw)
