"""Category x Price Zone Bias -- exploits statistically significant mispricings.

From v4 analysis of 49,753 closed binary markets (bootstrap CIs, cost-adjusted):

Only 6 segments survive after transaction costs (2% spread + 2% winner fee)
and have 95% bootstrap confidence intervals excluding zero:

  Politics x Unlikely (15-35%):    +12.9% gross -> +10.1% net  [9.44%, 16.29%]  composite=1.00
  Politics x Toss-up (35-65%):     +8.4%  gross -> +5.3%  net  [5.68%, 11.34%]  composite=0.80
  Science/Tech x Toss-up (35-65%): -7.7%  gross -> +4.4%  net  [-11.19%, -4.18%] composite=0.73
  Science/Tech x Unlikely (15-35%): -6.2% gross -> +2.6%  net  [-8.88%, -3.55%] composite=0.72
  Crypto x Toss-up (35-65%):       +4.6%  gross -> +1.5%  net  [2.05%, 7.21%]   composite=0.68
  Crypto x Likely (65-85%):        +4.8%  gross -> +1.2%  net  [1.46%, 7.93%]   composite=0.65

REMOVED (net-negative after costs):
  Economics x Favorite (85-100%): +3.8% gross -> -0.2% net  (costs eat entire edge)
  Politics x Likely (65-85%):     +3.3% gross -> -0.2% net  (costs eat entire edge)

REMOVED (not statistically significant):
  Entertainment x Likely:  CI includes zero [-2.58%, 12.48%]
  Other x Likely:          CI includes zero [-1.04%, 10.5%]
"""

from __future__ import annotations

from src.bot.config import CategoryStrategyConfig
from src.bot.models import ExitReason, MarketSnapshot, Position, Side, Signal

from .base import Strategy

# Net edge AFTER transaction costs (2% spread + 2% winner fee) from v4 Table 8.2.
# These are the honest expected returns per $1 wagered.
NET_EDGE_BY_SEGMENT: dict[tuple[str, str], float] = {
    ("politics", "unlikely"): 0.101,
    ("politics", "tossup"): 0.053,
    ("science_tech", "tossup"): 0.044,
    ("science_tech", "unlikely"): 0.026,
    ("crypto", "tossup"): 0.015,
    ("crypto", "likely"): 0.012,
}

# Composite exploitability score from v4 Section 8.5.
# Combines: edge magnitude, statistical significance, temporal persistence, sample size.
COMPOSITE_SCORE: dict[tuple[str, str], float] = {
    ("politics", "unlikely"): 1.00,
    ("politics", "tossup"): 0.80,
    ("science_tech", "tossup"): 0.73,
    ("science_tech", "unlikely"): 0.72,
    ("crypto", "tossup"): 0.68,
    ("crypto", "likely"): 0.65,
}

# Persistence: fraction of months with bias in the correct direction (v4 Section 7.2)
PERSISTENCE: dict[tuple[str, str], float] = {
    ("politics", "unlikely"): 1.00,   # 14/14 months
    ("politics", "tossup"): 0.86,     # 12/14 months
    ("science_tech", "tossup"): 0.69, # 9/13 months
    ("science_tech", "unlikely"): 0.92, # 11/12 months
    ("crypto", "tossup"): 0.92,       # 11/12 months
    ("crypto", "likely"): 0.83,       # 10/12 months
}


def classify_price_zone(price: float) -> str:
    """Classify a market price into a zone matching the analysis buckets."""
    if price <= 0.15:
        return "longshot"
    if price <= 0.35:
        return "unlikely"
    if price <= 0.65:
        return "tossup"
    if price <= 0.85:
        return "likely"
    return "favorite"


class CategoryBias(Strategy):
    """Apply category x price zone bias adjustments using v4-validated, cost-adjusted edges."""

    def __init__(self, config: CategoryStrategyConfig):
        super().__init__("category", config.weight)
        self.config = config

    def evaluate(self, snapshot: MarketSnapshot) -> Signal | None:
        if snapshot.price_yes is None:
            return None

        zone = classify_price_zone(snapshot.price_yes)
        category_zones = self.config.zone_biases.get(snapshot.category)
        if not category_zones:
            return None

        bias = category_zones.get(zone)
        if bias is None or abs(bias) < self.config.min_abs_bias:
            return None

        # Look up the cost-adjusted net edge for this segment
        segment_key = (snapshot.category, zone)
        net_edge = NET_EDGE_BY_SEGMENT.get(segment_key)
        if net_edge is None or net_edge < self.config.min_net_edge:
            return None

        # Check composite exploitability score (combines edge, significance, persistence, sample size)
        composite_score = COMPOSITE_SCORE.get(segment_key, 0.0)
        if composite_score < self.config.min_composite_score:
            return None

        if bias > 0:
            side = Side.BUY_YES
            token_id = snapshot.token_id_yes
        else:
            side = Side.BUY_NO
            token_id = snapshot.token_id_no

        # Confidence derived from composite score (already incorporates edge, significance,
        # persistence, and sample size). Capped at 0.85.
        confidence = min(0.85, composite_score)

        persistence = PERSISTENCE.get(segment_key, 0.5)

        return Signal(
            strategy_name=self.name,
            market_id=snapshot.market_id,
            token_id=token_id,
            side=side,
            confidence=confidence,
            expected_edge=net_edge,  # Use NET edge (after costs), not gross bias
            reasoning=(
                f"Category '{snapshot.category}' x zone '{zone}': "
                f"gross bias {bias:+.1%}, net edge {net_edge:+.1%}, "
                f"composite score {composite_score:.2f}, "
                f"persistence {persistence:.0%}"
            ),
            raw_data={
                "category": snapshot.category,
                "zone": zone,
                "gross_bias": round(bias, 4),
                "net_edge": round(net_edge, 4),
                "composite_score": composite_score,
                "persistence": persistence,
            },
        )

    def should_close(
        self, position: Position, snapshot: MarketSnapshot
    ) -> tuple[bool, ExitReason | None]:
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
