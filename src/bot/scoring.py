"""Capital-efficiency scoring for trade prioritization.

Ranks trade decisions by expected return per unit of capital lockup time.
Markets closing sooner get higher priority because the same edge compounds
faster when capital turns over quickly.

Formula:  priority_score = net_edge × time_factor × quality_factor

See plan for full derivation and worked examples.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.bot.models import MarketSnapshot, TradeDecision


@dataclass
class ScoringConfig:
    """Tunable parameters for the time-priority scoring curve."""

    min_days: float = 0.5  # Below this, market is "too close" (12 hours)
    too_close_factor: float = 0.05  # Factor for markets closing within min_days
    long_horizon_days: float = 90.0  # Beyond this, apply floor penalty
    long_horizon_floor: float = 0.05  # Floor factor for very long-dated markets
    no_end_date_factor: float = 0.1  # Factor when end_date is unknown


@dataclass
class ScoreResult:
    """Full scoring breakdown for logging and debugging."""

    score: float
    net_edge: float
    time_factor: float
    quality_factor: float
    days_until_close: float | None


def compute_time_factor(days_until_close: float | None, config: ScoringConfig) -> float:
    """Map days-to-close into a capital-efficiency multiplier.

    - No end date → 0.1 (deprioritized, not excluded)
    - < 12 hours  → 0.05 (bias mostly priced in, thin liquidity)
    - 0.5–90 days → 1/sqrt(days) (smooth decay)
    - > 90 days   → 0.05 (too far out, model uncertainty)
    """
    if days_until_close is None:
        return config.no_end_date_factor

    if days_until_close < config.min_days:
        return config.too_close_factor

    if days_until_close <= config.long_horizon_days:
        return 1.0 / math.sqrt(days_until_close)

    return config.long_horizon_floor


def compute_quality_factor(decision: TradeDecision) -> float:
    """Extract a signal-quality multiplier from the decision.

    Category signals: use v4 composite score (0.65–1.00), which encodes
    edge magnitude, statistical significance, temporal persistence, and
    sample size.

    Other strategies: fall back to confidence (best available proxy).
    """
    raw = decision.signal.raw_data
    composite_score = raw.get("composite_score")
    if composite_score is not None:
        return float(composite_score)
    return decision.signal.confidence


def score_decision(
    decision: TradeDecision,
    snapshot: MarketSnapshot,
    config: ScoringConfig,
) -> ScoreResult:
    """Compute the priority score for a trade decision.

    Returns a ScoreResult with the final score and all components for logging.
    Higher scores → should be executed first when exposure budget is limited.
    """
    now = decision.timestamp

    if snapshot.end_date is not None:
        delta_seconds = (snapshot.end_date - now).total_seconds()
        days_until_close: float | None = max(0.0, delta_seconds / 86_400)
    else:
        days_until_close = None

    net_edge = decision.signal.expected_edge
    time_factor = compute_time_factor(days_until_close, config)
    quality_factor = compute_quality_factor(decision)

    return ScoreResult(
        score=net_edge * time_factor * quality_factor,
        net_edge=net_edge,
        time_factor=time_factor,
        quality_factor=quality_factor,
        days_until_close=days_until_close,
    )
