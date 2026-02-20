"""Late Stage Momentum — exploits informative price moves in the final week.

From the analysis: price moves >15pp in the 1w→1d window are ~70% correct.
Moves 5-15pp are ~60% correct. This strategy follows these moves.
"""

from __future__ import annotations

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

        now = snapshot.snapshot_time
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

        # Determine direction and entry price
        if move > 0:
            side = Side.BUY_YES
            token_id = snapshot.token_id_yes
            entry_price = snapshot.price_yes
        else:
            side = Side.BUY_NO
            token_id = snapshot.token_id_no
            entry_price = snapshot.price_no or (
                1.0 - snapshot.price_yes if snapshot.price_yes is not None else None
            )

        if entry_price is None or entry_price <= 0 or entry_price >= 1:
            return None

        # Scale win probability by move magnitude
        if abs_move >= self.config.large_move_pp:
            win_prob = 0.70
            move_category = "large"
        else:
            win_prob = 0.60
            move_category = "medium"

        # Price-aware expected edge:
        #   E[return] = win_prob × (1 - price) - (1 - win_prob) × price - cost
        # At price 0.97:  0.70 × 0.03 - 0.30 × 0.97 - 0.04 = -0.33 → skip
        # At price 0.50:  0.70 × 0.50 - 0.30 × 0.50 - 0.04 = +0.16 → trade
        cost = 0.04  # 2% spread + 2% winner fee
        edge = win_prob * (1.0 - entry_price) - (1.0 - win_prob) * entry_price - cost

        if edge <= 0:
            return None  # Negative EV at this price — don't trade

        # Confidence derived from win probability (already validated by edge > 0)
        confidence = win_prob

        return Signal(
            strategy_name=self.name,
            market_id=snapshot.market_id,
            token_id=token_id,
            side=side,
            confidence=confidence,
            expected_edge=round(edge, 4),
            reasoning=(
                f"Late-stage {move_category} move: {move:+.0%} over past week, "
                f"{days_until_close:.1f} days until close, "
                f"entry {entry_price:.3f}, EV edge {edge:.1%}"
            ),
            raw_data={
                "weekly_move": round(move, 4),
                "days_until_close": round(days_until_close, 1),
                "move_category": move_category,
                "entry_price": round(entry_price, 4),
                "win_prob": win_prob,
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
