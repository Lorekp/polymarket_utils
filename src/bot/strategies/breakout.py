"""Asymmetric Breakout Strategy — fades upward breakouts, follows downward ones.

From 2025 analysis (1.2M breakout events across 33,591 markets):
- Upward breakouts: only 43.5% continue (56.5% revert), avg post-48h move = -5.78pp.
  These are overcorrections — fade them by buying NO.
- Downward breakouts: 77.9% predict correct resolution direction.
  These are informative — follow them by buying NO (price fell, market resolves NO).
- Overall continuation: 45% (below random). Symmetric momentum-following is unprofitable.
"""

from __future__ import annotations

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
    """Asymmetric breakout: fade upward moves (buy NO), follow downward moves (buy NO)."""

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

        if direction == "up":
            # Upward breakout: fade it (56.5% revert).
            # Buy NO — betting the upward move was an overcorrection.
            side = Side.BUY_NO
            token_id = snapshot.token_id_no

            # Edge is modest: ~6.5% over random (56.5% - 50%)
            if abs_move >= 0.40:
                confidence = 0.60
            elif abs_move >= 0.20:
                confidence = 0.50
            else:
                confidence = 0.40

            expected_edge = min(0.15, 0.04 * (abs_move / 0.10))
            reasoning = (
                f"Upward breakout +{abs_move:.0%} in {self.config.lookback_hours}h — "
                f"fading (56.5% of upward breakouts revert)"
            )
        else:
            # Downward breakout: follow it (77.9% informative about resolution).
            # Buy NO — price fell, market likely resolves NO.
            side = Side.BUY_NO
            token_id = snapshot.token_id_no

            # Edge is strong: ~27.9% over random (77.9% - 50%)
            if abs_move >= 0.40:
                confidence = 0.80
            elif abs_move >= 0.20:
                confidence = 0.70
            else:
                confidence = 0.55

            expected_edge = min(0.25, 0.10 * (abs_move / 0.10))
            reasoning = (
                f"Downward breakout -{abs_move:.0%} in {self.config.lookback_hours}h — "
                f"following (77.9% of downward breakouts predict resolution)"
            )

        return Signal(
            strategy_name=self.name,
            market_id=snapshot.market_id,
            token_id=token_id,
            side=side,
            confidence=confidence,
            expected_edge=expected_edge,
            reasoning=reasoning,
            raw_data={
                "move": round(move, 4),
                "direction": direction,
                "action": "fade" if direction == "up" else "follow",
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
        now = snapshot.snapshot_time
        hold_hours = (now - position.entry_time).total_seconds() / 3600
        if hold_hours >= self.config.hold_hours:
            return True, ExitReason.HOLD_EXPIRED

        # Reversal protection: if the held token's price drops by half the threshold
        if position.side == Side.BUY_YES:
            current = snapshot.price_yes
            if current is not None and current < position.entry_price - (self.config.threshold_pp / 2):
                return True, ExitReason.STOP_LOSS
        else:
            current = snapshot.price_no
            if current is not None and current < position.entry_price - (self.config.threshold_pp / 2):
                return True, ExitReason.STOP_LOSS

        return False, None
