"""Periodic market scanner — discovers candidate markets for each strategy."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.bot.config import BotConfig
from src.bot.market_data import MarketDataService
from src.bot.models import MarketSnapshot

logger = logging.getLogger(__name__)


class MarketScanner:
    """Discovers and builds snapshots for candidate markets each scan cycle."""

    def __init__(
        self,
        data_service: MarketDataService,
        config: BotConfig,
        cache_path: str | None = "bot_cache/scanner_cache.json",
    ):
        self.data = data_service
        self.config = config
        self._cache_path = Path(cache_path) if cache_path is not None else None

    def _serialize_snapshot(self, snap: MarketSnapshot) -> dict:
        return {
            "market_id":             snap.market_id,
            "condition_id":          snap.condition_id,
            "question":              snap.question,
            "token_id_yes":          snap.token_id_yes,
            "token_id_no":           snap.token_id_no,
            "category":              snap.category,
            "tags":                  snap.tags,
            "price_yes":             snap.price_yes,
            "price_no":              snap.price_no,
            "best_bid_yes":          snap.best_bid_yes,
            "best_ask_yes":          snap.best_ask_yes,
            "spread":                snap.spread,
            "volume":                snap.volume,
            "liquidity":             snap.liquidity,
            "created_at":            snap.created_at.isoformat() if snap.created_at else None,
            "end_date":              snap.end_date.isoformat() if snap.end_date else None,
            "accepting_orders":      snap.accepting_orders,
            "one_day_price_change":  snap.one_day_price_change,
            "one_week_price_change": snap.one_week_price_change,
            "outcome_prices":        snap.outcome_prices,
            "price_history_24h":     snap.price_history_24h,
            "snapshot_time":         snap.snapshot_time.isoformat(),
        }

    def _deserialize_snapshot(self, data: dict) -> MarketSnapshot:
        def _dt(s: str | None) -> datetime | None:
            return datetime.fromisoformat(s) if s else None

        return MarketSnapshot(
            market_id=data["market_id"],
            condition_id=data["condition_id"],
            question=data["question"],
            token_id_yes=data["token_id_yes"],
            token_id_no=data["token_id_no"],
            category=data["category"],
            tags=data["tags"],
            price_yes=data["price_yes"],
            price_no=data["price_no"],
            best_bid_yes=data["best_bid_yes"],
            best_ask_yes=data["best_ask_yes"],
            spread=data["spread"],
            volume=data["volume"],
            liquidity=data["liquidity"],
            created_at=_dt(data.get("created_at")),
            end_date=_dt(data.get("end_date")),
            accepting_orders=data["accepting_orders"],
            one_day_price_change=data.get("one_day_price_change"),
            one_week_price_change=data.get("one_week_price_change"),
            outcome_prices=data.get("outcome_prices"),
            price_history_24h=(
                [(int(ts), float(p)) for ts, p in data["price_history_24h"]]
                if data.get("price_history_24h") is not None
                else None
            ),
            snapshot_time=_dt(data["snapshot_time"]),
        )

    def _load_cache(self) -> tuple[dict[str, MarketSnapshot], bool]:
        """Load cached snapshots. Returns (snapshots, is_stale)."""
        if self._cache_path is None:
            return {}, False
        if not self._cache_path.exists():
            logger.info("No cache file found at %s, starting fresh", self._cache_path)
            return {}, False

        try:
            with open(self._cache_path, encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cache file corrupt or unreadable (%s), ignoring", exc)
            return {}, False

        lookback_hours = self.config.strategies.breakout.lookback_hours
        written_at_str = raw.get("written_at")
        is_stale = True
        if written_at_str:
            try:
                written_at = datetime.fromisoformat(written_at_str)
                age_hours = (self.data.get_current_time() - written_at).total_seconds() / 3600
                is_stale = age_hours > lookback_hours
            except ValueError:
                pass

        if is_stale:
            logger.info(
                "Cache is older than %d hours (lookback_hours), treating all markets as new",
                lookback_hours,
            )
            return {}, True

        cached: dict[str, MarketSnapshot] = {}
        for market_id, entry in raw.get("markets", {}).items():
            try:
                cached[market_id] = self._deserialize_snapshot(entry)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed cache entry %s: %s", market_id, exc)

        logger.info("Loaded %d snapshots from cache", len(cached))
        return cached, False

    def _save_cache(self, snapshots: dict[str, MarketSnapshot]) -> None:
        """Atomically write snapshots to cache file."""
        if self._cache_path is None:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "written_at": self.data.get_current_time().isoformat(),
            "markets": {
                mid: self._serialize_snapshot(snap)
                for mid, snap in snapshots.items()
            },
        }
        tmp_path = self._cache_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            tmp_path.replace(self._cache_path)
        except OSError as exc:
            logger.warning("Failed to save cache: %s", exc)

    def scan(self) -> list[MarketSnapshot]:
        """Run one scan cycle. Returns deduplicated MarketSnapshots."""
        cached_snaps, is_stale = self._load_cache()

        vf = self.config.volume_filter
        markets = self.data.get_active_binary_markets(
            volume_min=vf.min_volume,
            volume_max=vf.max_volume,
        )
        logger.info("Gamma returned %d active binary markets", len(markets))

        snapshots: dict[str, MarketSnapshot] = {}
        now = self.data.get_current_time()

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

        # Enrich breakout candidates with price history (concurrent batch)
        if self.config.strategies.breakout.enabled:
            lookback_hours = self.config.strategies.breakout.lookback_hours
            now_ts = int(now.timestamp())
            cutoff_ts = now_ts - (lookback_hours * 3600)

            # Phase 1: Collect — decide what each snapshot needs
            fetch_requests: list[tuple[str, int | None, int]] = []
            # (market_id, token_id, cached_history as dict or None)
            fetch_plan: list[tuple[str, str, dict | None]] = []
            cache_reuse = 0

            for snap in snapshots.values():
                if snap.volume < vf.min_volume:
                    continue

                cached = cached_snaps.get(snap.market_id)
                use_cache = (
                    cached is not None
                    and not is_stale
                    and cached.price_history_24h
                )

                if use_cache:
                    max_cached_ts = max(ts for ts, _ in cached.price_history_24h)
                    if max_cached_ts >= now_ts - 120:
                        snap.price_history_24h = cached.price_history_24h
                        cache_reuse += 1
                    else:
                        fetch_requests.append((snap.token_id_yes, max_cached_ts + 60, 60))
                        fetch_plan.append((snap.market_id, snap.token_id_yes, dict(cached.price_history_24h)))
                else:
                    start_ts = now_ts - (lookback_hours * 3600)
                    fetch_requests.append((snap.token_id_yes, start_ts, 60))
                    fetch_plan.append((snap.market_id, snap.token_id_yes, None))

            # Phase 2: Dispatch — fetch all at once, concurrently
            results: dict[str, list[tuple[int, float]]] = {}
            if fetch_requests:
                results = self.data.fetch_price_histories_batch(fetch_requests)

            # Phase 3: Apply — merge results back into snapshots
            enriched_new = 0
            enriched_incremental = 0

            for market_id, token_id, merge_base in fetch_plan:
                snap = snapshots.get(market_id)
                if snap is None:
                    continue

                new_points = results.get(token_id, [])

                if merge_base is not None:
                    for ts, price in new_points:
                        merge_base[ts] = price
                    snap.price_history_24h = [
                        (ts, p) for ts, p in sorted(merge_base.items()) if ts >= cutoff_ts
                    ]
                    enriched_incremental += 1
                else:
                    snap.price_history_24h = new_points or None
                    enriched_new += 1

            logger.info(
                "Price history: %d full fetches, %d incremental updates, %d cache reuse",
                enriched_new, enriched_incremental, cache_reuse,
            )

        self._save_cache(snapshots)
        result = list(snapshots.values())
        logger.info("Scanner produced %d candidate snapshots", len(result))
        return result
