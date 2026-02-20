"""New Market Buyer — exploits initial underpricing in contested markets only.

From the v4 analysis (49,753 markets):
- Weighted mean absolute initial bias: 15.65% (markets are genuinely mispriced at creation).
- By 1 day before close, bias drops to 1.00% (markets self-correct over time).
- CAVEAT: v4 warns that naive aggregate initial bias is misleading because it conflates
  the base rate of Yes resolutions (~27%) with a default opening price near ~50%.
- The correct measure is conditional initial bias (within-bin), which v4 computes but
  does not isolate a specific edge for the 35-65% price range.
- The original 9.6% edge estimate came from a volatility tier analysis, NOT from v4's
  conditional calibration. We conservatively reduce to 6.0% to avoid overfitting.
- Duration analysis: 1-7d markets have init_bias of -13.1%, 1-4wk: -14.2%, 1-3mo: -23.3%
  (all negative = YES overpriced for short-duration markets on aggregate).

This strategy ONLY buys YES on new markets in the 35-65% price range where the
conditional underpricing effect may exist, with conservative edge estimates.
"""

from __future__ import annotations

from src.bot.config import NewMarketStrategyConfig
from src.bot.models import ExitReason, MarketSnapshot, Position, Side, Signal

from .base import Strategy

# Conservative base edge for contested markets.
# Original estimate was 9.6% from a volatility tier, but v4 conditional analysis does not
# independently confirm this magnitude. Reduced to 6.0% for conservatism.
CONTESTED_MARKET_BASE_EDGE = 0.060


class NewMarketBuyer(Strategy):
    """Buy Yes on newly created contested markets (35-65% price range)."""

    def __init__(self, config: NewMarketStrategyConfig):
        super().__init__("new_market", config.weight)
        self.config = config

    def evaluate(self, snapshot: MarketSnapshot) -> Signal | None:
        if not snapshot.created_at or snapshot.price_yes is None:
            return None

        now = snapshot.snapshot_time
        age_hours = (now - snapshot.created_at).total_seconds() / 3600

        if age_hours > self.config.max_age_hours:
            return None

        price = snapshot.price_yes

        # Only trade contested markets where YES underpricing is real
        if price < self.config.min_price or price > self.config.max_price:
            return None

        # Check market duration fits preferred range
        if snapshot.end_date:
            duration_days = (snapshot.end_date - snapshot.created_at).total_seconds() / 86400
            if duration_days < self.config.preferred_duration_days_min:
                return None
            if duration_days > self.config.preferred_duration_days_max:
                return None

        # Edge decays as market ages (more of the initial mispricing gets corrected)
        age_factor = max(0.3, 1.0 - (age_hours / self.config.max_age_hours) * 0.5)
        estimated_edge = CONTESTED_MARKET_BASE_EDGE * age_factor

        if estimated_edge < self.config.min_edge:
            return None

        # Confidence: higher for prices closer to 50% (most contested)
        # Capped at 0.55 (lower than category strategy, reflecting less validation from v4)
        distance_from_mid = abs(price - 0.50)
        confidence = min(0.55, 0.50 - distance_from_mid)

        return Signal(
            strategy_name=self.name,
            market_id=snapshot.market_id,
            token_id=snapshot.token_id_yes,
            side=Side.BUY_YES,
            confidence=confidence,
            expected_edge=estimated_edge,
            reasoning=(
                f"New contested market ({age_hours:.0f}h old), "
                f"price={price:.2f} (in 35-65% zone), "
                f"estimated edge={estimated_edge:.1%}"
            ),
            raw_data={
                "age_hours": round(age_hours, 1),
                "age_factor": round(age_factor, 3),
                "price": round(price, 4),
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
        if position.side == Side.BUY_YES:
            current = snapshot.price_yes
        else:
            current = snapshot.price_no

        if current is None:
            return False, None

        pnl_pct = (current - position.entry_price) / position.entry_price

        if pnl_pct >= self.config.take_profit:
            return True, ExitReason.TAKE_PROFIT
        if pnl_pct <= -self.config.stop_loss:
            return True, ExitReason.STOP_LOSS

        return False, None
