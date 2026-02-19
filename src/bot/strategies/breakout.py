"""Breakout Follower — exploits post-breakout momentum.

When a market moves >=10pp in 24h, the move tends to continue
in the same direction over the next 48h (>55% continuation rate).
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.bot.config import BreakoutStrategyConfig
from src.bot.models import ExitReason, MarketSnapshot, Position, Side, Signal

from .base import Strategy


def detect_breakout(
    history: list[tuple[int, float]],
    threshold: float,
) -> tuple[float | None, str | None]:
    """Detect the largest breakout in a price history series.

    Returns (move_size, direction) where direction is "up" or "down",
    or (None, None) if no breakout detected.
    """
    if not history or len(history) < 2:
        return None, None

    # Find max and min prices and compute the range
    prices = [p for _, p in history]
    earliest_price = prices[0]
    latest_price = prices[-1]
    move = latest_price - earliest_price

    if abs(move) >= threshold:
        direction = "up" if move > 0 else "down"
        return move, direction

    # Also check rolling max move within the window
    max_price = max(prices)
    min_price = min(prices)

    rise = max_price - earliest_price
    fall = earliest_price - min_price

    if rise >= threshold and rise >= fall:
        return rise, "up"
    if fall >= threshold:
        return -fall, "down"

    return None, None


class BreakoutFollower(Strategy):
    """Follow momentum after large price moves."""

    def __init__(self, config: BreakoutStrategyConfig):
        super().__init__("breakout", config.weight)
        self.config = config

    def evaluate(self, snapshot: MarketSnapshot) -> Signal | None:
        if not snapshot.price_history_24h or snapshot.price_yes is None:
            return None

        move, direction = detect_breakout(
            snapshot.price_history_24h,
            threshold=self.config.threshold_pp,
        )

        if move is None or direction is None:
            return None

        abs_move = abs(move)

        # Scale confidence by breakout magnitude
        if abs_move >= 0.40:
            confidence = 0.85
        elif abs_move >= 0.20:
            confidence = 0.70
        else:
            confidence = 0.50

        # Expected edge: from research, continuation rate is ~55-65%
        # so edge ≈ (continuation_rate - 0.5) * avg_continuation_magnitude
        expected_edge = 0.08 * (abs_move / 0.10)  # scales with move size

        if direction == "up":
            side = Side.BUY_YES
            token_id = snapshot.token_id_yes
        else:
            side = Side.BUY_NO
            token_id = snapshot.token_id_no

        return Signal(
            strategy_name=self.name,
            market_id=snapshot.market_id,
            token_id=token_id,
            side=side,
            confidence=confidence,
            expected_edge=expected_edge,
            reasoning=(
                f"Breakout detected: {direction} {abs_move:.0%} in {self.config.lookback_hours}h, "
                f"expecting momentum continuation"
            ),
            raw_data={
                "move": round(move, 4),
                "direction": direction,
                "history_points": len(snapshot.price_history_24h),
            },
        )

    def should_close(
        self, position: Position, snapshot: MarketSnapshot
    ) -> tuple[bool, ExitReason | None]:
        if snapshot.price_yes is None:
            return False, None

        # Check resolution
        if snapshot.outcome_prices:
            if snapshot.outcome_prices[0] >= 0.99 or snapshot.outcome_prices[0] <= 0.01:
                won = (
                    position.side == Side.BUY_YES and snapshot.outcome_prices[0] >= 0.99
                ) or (
                    position.side == Side.BUY_NO and snapshot.outcome_prices[0] <= 0.01
                )
                return True, ExitReason.RESOLVED_WIN if won else ExitReason.RESOLVED_LOSS

        # Time-based exit: close after hold_hours
        now = datetime.now(timezone.utc)
        hold_hours = (now - position.entry_time).total_seconds() / 3600
        if hold_hours >= self.config.hold_hours:
            return True, ExitReason.HOLD_EXPIRED

        # Reversal protection: if price reverses by half the threshold
        current = snapshot.price_yes
        if position.side == Side.BUY_YES:
            if current < position.entry_price - (self.config.threshold_pp / 2):
                return True, ExitReason.STOP_LOSS
        else:
            if current > position.entry_price + (self.config.threshold_pp / 2):
                return True, ExitReason.STOP_LOSS

        return False, None
