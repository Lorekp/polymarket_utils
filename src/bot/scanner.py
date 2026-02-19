"""Periodic market scanner — discovers candidate markets for each strategy."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.bot.config import BotConfig
from src.bot.market_data import MarketDataService
from src.bot.models import MarketSnapshot

logger = logging.getLogger(__name__)


class MarketScanner:
    """Discovers and builds snapshots for candidate markets each scan cycle."""

    def __init__(self, data_service: MarketDataService, config: BotConfig):
        self.data = data_service
        self.config = config

    def scan(self) -> list[MarketSnapshot]:
        """Run one scan cycle. Returns deduplicated MarketSnapshots."""
        vf = self.config.volume_filter
        markets = self.data.get_active_binary_markets(
            volume_min=vf.min_volume,
            volume_max=vf.max_volume,
        )
        logger.info("Gamma returned %d active binary markets", len(markets))

        snapshots: dict[str, MarketSnapshot] = {}
        now = datetime.now(timezone.utc)

        for market in markets:
            snap = self.data.build_snapshot(market)
            if snap is None or snap.price_yes is None:
                continue

            # Skip already-resolved markets (Gamma API sometimes lags)
            if snap.outcome_prices:
                if any(p >= 0.99 or p <= 0.01 for p in snap.outcome_prices):
                    continue

            # Skip markets with extreme prices (very low liquidity / effectively resolved)
            if snap.price_yes <= 0.01 or snap.price_yes >= 0.99:
                continue

            # Check if any strategy wants this market
            dominated = False

            # New market strategy
            sc = self.config.strategies.new_market
            if sc.enabled and snap.created_at:
                age_hours = (now - snap.created_at).total_seconds() / 3600
                if age_hours <= sc.max_age_hours:
                    dominated = True

            # Late stage strategy
            sl = self.config.strategies.late_stage
            if sl.enabled and snap.end_date:
                days_until = (snap.end_date - now).total_seconds() / 86400
                if 0 < days_until <= sl.days_before_close:
                    dominated = True

            # Category bias — applies to all markets
            if self.config.strategies.category.enabled:
                dominated = True

            # Breakout — needs price history enrichment
            sb = self.config.strategies.breakout
            if sb.enabled:
                dominated = True

            if dominated:
                snapshots[snap.market_id] = snap

        # Enrich breakout candidates with price history (costs 1 API call each)
        if self.config.strategies.breakout.enabled:
            enriched = 0
            for snap in snapshots.values():
                # Only fetch history for markets with meaningful volume
                if snap.volume >= self.config.volume_filter.min_volume:
                    self.data.enrich_with_price_history(
                        snap,
                        lookback_hours=self.config.strategies.breakout.lookback_hours,
                    )
                    enriched += 1
            logger.info("Enriched %d markets with price history", enriched)

        result = list(snapshots.values())
        logger.info("Scanner produced %d candidate snapshots", len(result))
        return result
