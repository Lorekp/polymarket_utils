"""New Market Buyer — exploits the -15.65% initial price bias.

Markets are systematically underpriced at creation. Buying Yes on new markets
and holding until the bias corrects is historically profitable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.bot.config import NewMarketStrategyConfig
from src.bot.models import ExitReason, MarketSnapshot, Position, Side, Signal

from .base import Strategy

# Historical bias by category (from the analysis)
CATEGORY_INITIAL_BIAS: dict[str, float] = {
    "politics": 0.18,
    "crypto": 0.16,
    "science_tech": 0.15,
    "entertainment": 0.15,
    "sports": 0.14,
    "economics": 0.12,
    "world_events": 0.14,
    "other": 0.15,
}


class NewMarketBuyer(Strategy):
    """Buy Yes on newly created markets to exploit initial underpricing."""

    def __init__(self, config: NewMarketStrategyConfig):
        super().__init__("new_market", config.weight)
        self.config = config

    def evaluate(self, snapshot: MarketSnapshot) -> Signal | None:
        if not snapshot.created_at or snapshot.price_yes is None:
            return None

        now = datetime.now(timezone.utc)
        age_hours = (now - snapshot.created_at).total_seconds() / 3600

        if age_hours > self.config.max_age_hours:
            return None

        # Check market duration fits preferred range
        if snapshot.end_date:
            duration_days = (snapshot.end_date - snapshot.created_at).total_seconds() / 86400
            if duration_days < self.config.preferred_duration_days_min:
                return None
            if duration_days > self.config.preferred_duration_days_max:
                return None

        # Estimate edge based on historical initial bias for this category
        base_bias = CATEGORY_INITIAL_BIAS.get(snapshot.category, 0.15)

        # Adjust: newer markets have more bias remaining
        age_factor = max(0.3, 1.0 - (age_hours / self.config.max_age_hours) * 0.5)
        estimated_edge = base_bias * age_factor

        if estimated_edge < self.config.min_edge:
            return None

        # Confidence: higher for lower-priced markets (longshot bias amplifies)
        price = snapshot.price_yes
        if price <= 0.15:
            confidence = min(0.9, estimated_edge / 0.10)
        elif price <= 0.50:
            confidence = min(0.8, estimated_edge / 0.12)
        else:
            confidence = min(0.6, estimated_edge / 0.15)

        return Signal(
            strategy_name=self.name,
            market_id=snapshot.market_id,
            token_id=snapshot.token_id_yes,
            side=Side.BUY_YES,
            confidence=confidence,
            expected_edge=estimated_edge,
            reasoning=(
                f"New market ({age_hours:.0f}h old), category={snapshot.category}, "
                f"price={price:.2f}, estimated initial bias={estimated_edge:.1%}"
            ),
            raw_data={
                "age_hours": round(age_hours, 1),
                "base_bias": base_bias,
                "age_factor": round(age_factor, 3),
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

        # P&L based exits
        current = snapshot.price_yes
        pnl_pct = (current - position.entry_price) / position.entry_price

        if position.side == Side.BUY_YES:
            if pnl_pct >= self.config.take_profit:
                return True, ExitReason.TAKE_PROFIT
            if pnl_pct <= -self.config.stop_loss:
                return True, ExitReason.STOP_LOSS
        else:
            # BUY_NO: profit when yes price drops
            pnl_pct = (position.entry_price - current) / position.entry_price
            if pnl_pct >= self.config.take_profit:
                return True, ExitReason.TAKE_PROFIT
            if pnl_pct <= -self.config.stop_loss:
                return True, ExitReason.STOP_LOSS

        return False, None
