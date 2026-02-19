"""Late Stage Momentum — exploits informative price moves in the final week.

From the analysis: price moves >15pp in the 1w→1d window are ~70% correct.
Moves 5-15pp are ~60% correct. This strategy follows these moves.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.bot.config import LateStageStrategyConfig
from src.bot.models import ExitReason, MarketSnapshot, Position, Side, Signal

from .base import Strategy


class LateStageMomentum(Strategy):
    """Follow large price moves in the final week before close."""

    def __init__(self, config: LateStageStrategyConfig):
        super().__init__("late_stage", config.weight)
        self.config = config

    def evaluate(self, snapshot: MarketSnapshot) -> Signal | None:
        if snapshot.price_yes is None or snapshot.end_date is None:
            return None

        now = datetime.now(timezone.utc)
        days_until_close = (snapshot.end_date - now).total_seconds() / 86400

        if days_until_close <= 0 or days_until_close > self.config.days_before_close:
            return None

        # Use one_week_price_change from Gamma (no extra API call)
        move = snapshot.one_week_price_change
        if move is None:
            return None

        abs_move = abs(move)

        if abs_move < self.config.min_move_pp:
            return None

        # Scale confidence by move size
        if abs_move >= self.config.large_move_pp:
            confidence = 0.75
            edge = 0.12
        else:
            confidence = 0.50
            edge = 0.06

        if move > 0:
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
            expected_edge=edge,
            reasoning=(
                f"Late-stage move: {move:+.0%} over past week, "
                f"{days_until_close:.1f} days until close — following momentum"
            ),
            raw_data={
                "weekly_move": round(move, 4),
                "days_until_close": round(days_until_close, 1),
                "move_category": "large" if abs_move >= self.config.large_move_pp else "medium",
            },
        )

    def should_close(
        self, position: Position, snapshot: MarketSnapshot
    ) -> tuple[bool, ExitReason | None]:
        # Hold to resolution
        if snapshot.outcome_prices:
            if snapshot.outcome_prices[0] >= 0.99 or snapshot.outcome_prices[0] <= 0.01:
                won = (
                    position.side == Side.BUY_YES and snapshot.outcome_prices[0] >= 0.99
                ) or (
                    position.side == Side.BUY_NO and snapshot.outcome_prices[0] <= 0.01
                )
                return True, ExitReason.RESOLVED_WIN if won else ExitReason.RESOLVED_LOSS

        if not snapshot.accepting_orders:
            return True, ExitReason.MARKET_CLOSED

        return False, None
