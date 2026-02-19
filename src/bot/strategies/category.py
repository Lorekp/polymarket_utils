"""Category Bias — exploits per-category systematic mispricing.

From the analysis:
- Politics: +2.6% bias (Yes underpriced)
- Crypto: +1.0% bias
- Economics: -1.0% bias (Yes overpriced)
- Sports: ~0% (well-calibrated)
"""

from __future__ import annotations

from src.bot.config import CategoryStrategyConfig
from src.bot.models import ExitReason, MarketSnapshot, Position, Side, Signal

from .base import Strategy


class CategoryBias(Strategy):
    """Apply category-specific bias adjustments."""

    def __init__(self, config: CategoryStrategyConfig):
        super().__init__("category", config.weight)
        self.config = config

    def evaluate(self, snapshot: MarketSnapshot) -> Signal | None:
        if snapshot.price_yes is None:
            return None

        bias = self.config.bias_overrides.get(snapshot.category, 0.0)

        if abs(bias) < 0.005:
            return None  # negligible bias

        if bias > 0:
            # Category is underpriced for Yes
            side = Side.BUY_YES
            token_id = snapshot.token_id_yes
        else:
            # Category is overpriced for Yes
            side = Side.BUY_NO
            token_id = snapshot.token_id_no

        confidence = min(0.6, abs(bias) / 0.03)  # max confidence at 3% bias

        return Signal(
            strategy_name=self.name,
            market_id=snapshot.market_id,
            token_id=token_id,
            side=side,
            confidence=confidence,
            expected_edge=abs(bias),
            reasoning=(
                f"Category '{snapshot.category}' has historical bias of "
                f"{bias:+.1%} — {'Yes underpriced' if bias > 0 else 'Yes overpriced'}"
            ),
            raw_data={"category": snapshot.category, "bias": round(bias, 4)},
        )

    def should_close(
        self, position: Position, snapshot: MarketSnapshot
    ) -> tuple[bool, ExitReason | None]:
        # Category bias is a slow-burn signal — resolution or market close triggers exit
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
