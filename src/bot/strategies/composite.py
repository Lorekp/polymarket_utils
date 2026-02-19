"""Composite strategy — aggregates signals from all strategies into trade decisions."""

from __future__ import annotations

import logging

from src.bot.config import BotConfig
from src.bot.models import MarketSnapshot, Side, Signal, TradeDecision

from .base import Strategy

logger = logging.getLogger(__name__)

MIN_COMPOSITE_CONFIDENCE = 0.30


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
            net_score = yes_score - no_score
            net_edge = yes_edge - no_edge
            side = Side.BUY_YES
            token_id = snapshot.token_id_yes
        else:
            net_score = no_score - yes_score
            net_edge = no_edge - yes_edge
            side = Side.BUY_NO
            token_id = snapshot.token_id_no

        total_weight = yes_score + no_score
        if total_weight == 0:
            return None

        composite_confidence = net_score / total_weight
        composite_edge = net_edge / total_weight if total_weight > 0 else 0

        if composite_confidence < MIN_COMPOSITE_CONFIDENCE:
            return None

        # Select the primary signal for reasoning
        primary = max(signals, key=lambda s: s.confidence * self._get_weight(s.strategy_name))

        # Compute position size
        size_usd = self._compute_size(composite_confidence)

        # Determine fill price
        if side == Side.BUY_YES:
            fill_price = snapshot.price_yes or 0.5
        else:
            fill_price = snapshot.price_no or 0.5

        # Build aggregated signal
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

        return TradeDecision(
            signal=agg_signal,
            size_usd=size_usd,
            fill_price=fill_price,
        )

    def _get_weight(self, strategy_name: str) -> float:
        for s in self.strategies:
            if s.name == strategy_name:
                return s.weight
        return 1.0

    def _compute_size(self, confidence: float) -> float:
        base = self.config.position_sizing.fixed_size_usd
        # Scale size by confidence: 30% confidence = 60% of base, 100% = 100%
        scale = 0.6 + 0.4 * confidence
        return round(base * scale, 2)
