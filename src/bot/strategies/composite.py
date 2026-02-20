"""Composite strategy — aggregates signals from all strategies into trade decisions.

Position sizing uses half-Kelly fractions from v4 analysis (Section 8.4) for category
signals, with edge-proportional fallback for other strategies. Half-Kelly balances
growth-rate optimality with variance reduction.
"""

from __future__ import annotations

import logging

from src.bot.config import BotConfig
from src.bot.models import MarketSnapshot, Side, Signal, TradeDecision

from .base import Strategy

logger = logging.getLogger(__name__)

MIN_COMPOSITE_CONFIDENCE = 0.30

# Conviction override: if a single signal exceeds both thresholds, skip aggregation
CONVICTION_MIN_CONFIDENCE = 0.70
CONVICTION_MIN_EDGE = 0.08


class CompositeStrategy:
    """Aggregates signals from multiple strategies into final trade decisions."""

    def __init__(self, strategies: list[Strategy], config: BotConfig):
        self.strategies = strategies
        self.config = config

    def evaluate(self, snapshot: MarketSnapshot) -> TradeDecision | None:
        """Evaluate all strategies and aggregate into a single decision."""
        signals: list[Signal] = []

        for strategy in self.strategies:
            sig = strategy.evaluate(snapshot)
            if sig is not None:
                signals.append(sig)

        if not signals:
            return None

        # Conviction override: if one signal is very strong, use it directly
        # without dilution from weaker/opposing signals
        conviction_signal = self._check_conviction(signals)
        if conviction_signal is not None:
            return self._build_decision(
                conviction_signal, conviction_signal, [conviction_signal], snapshot,
            )

        # Group signals by direction
        yes_score = 0.0
        no_score = 0.0
        yes_edge = 0.0
        no_edge = 0.0

        for sig in signals:
            weighted = sig.confidence * sig.expected_edge * self._get_weight(sig.strategy_name)
            if sig.side == Side.BUY_YES:
                yes_score += sig.confidence * self._get_weight(sig.strategy_name)
                yes_edge += weighted
            else:
                no_score += sig.confidence * self._get_weight(sig.strategy_name)
                no_edge += weighted

        # Determine dominant direction
        if yes_score >= no_score:
            side = Side.BUY_YES
        else:
            side = Side.BUY_NO

        total_weight = yes_score + no_score
        if total_weight == 0:
            return None

        # Composite confidence: average confidence of the dominant side,
        # discounted by how much the opposing side disagrees.
        dominant_score = max(yes_score, no_score)
        opposing_score = min(yes_score, no_score)
        dominant_n = sum(1 for s in signals if s.side == side)
        dominant_avg = dominant_score / dominant_n if dominant_n > 0 else 0
        agreement = 1.0 - (opposing_score / dominant_score) if dominant_score > 0 else 0
        composite_confidence = dominant_avg * agreement

        net_edge = abs(yes_edge - no_edge)
        composite_edge = max(0, net_edge / dominant_score) if dominant_score > 0 else 0

        if composite_confidence < MIN_COMPOSITE_CONFIDENCE:
            return None

        # Select the primary signal — must agree with composite direction
        same_side = [s for s in signals if s.side == side]
        primary = max(
            same_side if same_side else signals,
            key=lambda s: s.confidence * self._get_weight(s.strategy_name),
        )

        # Build aggregated signal
        token_id = snapshot.token_id_yes if side == Side.BUY_YES else snapshot.token_id_no
        agg_signal = Signal(
            strategy_name=primary.strategy_name,
            market_id=snapshot.market_id,
            token_id=token_id,
            side=side,
            confidence=composite_confidence,
            expected_edge=composite_edge,
            reasoning=f"Composite ({len(signals)} signals): {primary.reasoning}",
            raw_data={
                "signals": [s.to_dict() for s in signals],
                "yes_score": round(yes_score, 3),
                "no_score": round(no_score, 3),
            },
        )

        return self._build_decision(agg_signal, primary, signals, snapshot)

    def _check_conviction(self, signals: list[Signal]) -> Signal | None:
        """Return the strongest signal if it exceeds conviction thresholds."""
        for sig in signals:
            if (sig.confidence >= CONVICTION_MIN_CONFIDENCE
                    and sig.expected_edge >= CONVICTION_MIN_EDGE):
                return sig
        return None

    def _build_decision(
        self, signal: Signal, primary: Signal,
        all_signals: list[Signal], snapshot: MarketSnapshot,
    ) -> TradeDecision:
        """Build a TradeDecision from a signal."""
        if signal.side == Side.BUY_YES:
            fill_price = snapshot.price_yes or 0.5
        else:
            fill_price = snapshot.price_no or 0.5

        size_usd = self._compute_size(signal.confidence, signal.expected_edge, primary)

        return TradeDecision(
            signal=signal,
            size_usd=size_usd,
            fill_price=fill_price,
        )

    def _get_weight(self, strategy_name: str) -> float:
        for s in self.strategies:
            if s.name == strategy_name:
                return s.weight
        return 1.0

    def _compute_size(
        self, confidence: float, expected_edge: float, signal: Signal | None = None,
    ) -> float:
        """Position sizing using half-Kelly for category signals, edge-proportional fallback.

        Half-Kelly fractions (from v4 Section 8.4) provide optimal growth-rate sizing
        with reduced variance vs full Kelly. Each category x zone segment has its own
        fraction derived from its specific win probability and payoff ratio.

        For non-category strategies, falls back to the original edge-proportional sizing
        since we don't have Kelly-calibrated fractions for them.

        All sizes are capped at max_position_pct of bankroll.
        """
        bankroll = self.config.simulation_bankroll
        max_size = bankroll * self.config.position_sizing.max_position_pct

        # Half-Kelly sizing for category signals
        if signal is not None and signal.strategy_name == "category":
            raw_data = signal.raw_data
            category = raw_data.get("category", "")
            zone = raw_data.get("zone", "")
            kelly_key = f"{category}_{zone}"
            kelly_frac = self.config.strategies.category.half_kelly_fractions.get(kelly_key)
            if kelly_frac is not None:
                kelly_size = bankroll * kelly_frac
                return round(min(kelly_size, max_size), 2)

        # Fallback: edge-proportional sizing for other strategies
        base = self.config.position_sizing.fixed_size_usd
        # Scale = confidence * edge / reference_edge, clamped to [0.5, 2.0]
        scale = max(0.5, min(2.0, confidence * expected_edge / 0.03))
        return round(min(base * scale, max_size), 2)
