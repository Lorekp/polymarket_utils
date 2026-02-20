"""Parquet-backed data service for backtesting.

Duck-types :class:`MarketDataService` so the scanner, strategies, and
execution engine can use it without modification.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.bot.backtest.clock import SimulatedClock
from src.bot.backtest.parquet_loader import MarketRow, ParquetLoader
from src.bot.models import MarketSnapshot

logger = logging.getLogger(__name__)

# Category rules — duplicated from market_data.py to avoid importing the live
# module (which pulls in polymarket_apis / httpx).  Kept in sync manually.
CATEGORY_RULES: list[tuple[str, str]] = [
    ("sports", r"nba|nfl|mlb|nhl|soccer|football|tennis|boxing|mma|ufc|cricket|esports|dota|league of legends|sports|epl|la liga|bundesliga|serie a|ligue 1|f1|formula|nascar|golf|olympics|pga"),
    ("crypto", r"crypto|bitcoin|btc|ethereum|eth|solana|sol|defi|blockchain|token|nft|altcoin|memecoin|binance|coinbase"),
    ("politics", r"politic|election|trump|biden|democrat|republican|congress|senate|governor|president|vote|gop|dnc|rnc|parliament|minister|legislation|bill|law|executive order"),
    ("economics", r"econom|fed |federal reserve|interest rate|inflation|gdp|employment|jobs|recession|tariff|trade|stock market|s&p|nasdaq|dow jones|treasury|debt|cpi"),
    ("science_tech", r"science|tech|ai |artificial intelligence|openai|google|apple|microsoft|space|spacex|nasa|climate|weather|earthquake|hurricane|temperature"),
    ("entertainment", r"movie|film|oscar|grammy|emmy|music|album|box office|streaming|netflix|youtube|tiktok|celebrity|award|tv show|series"),
    ("world_events", r"war|conflict|ukraine|russia|china|israel|gaza|nato|united nations|refugee|disaster|pandemic"),
]


def _classify_category(tags_str: str | None, question: str = "") -> str:
    tag_text = (tags_str or "").lower()
    combined = tag_text + " " + question.lower()
    for category, pattern in CATEGORY_RULES:
        if re.search(pattern, combined):
            return category
    return "other"


def _parse_tag_labels(tags_str: str | None) -> list[str]:
    """Convert pipe-separated tag string back to a list."""
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split("|") if t.strip()]


@dataclass
class BacktestMarketProxy:
    """Lightweight proxy mimicking the attributes of ``GammaMarket``
    that :meth:`build_snapshot` and the scanner consume.

    Used by :class:`BacktestDataService` to avoid importing
    ``polymarket_apis``.
    """

    id: str  # noqa: A003 — matches GammaMarket.id
    condition_id: str
    question: str
    token_ids: list[str]
    outcomes: str | None
    outcome_prices: list[float] | None
    tags: list[str] = field(default_factory=list)
    volume_num: float = 0.0
    liquidity_num: float = 0.0
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    created_at: datetime | None = None
    end_date: datetime | None = None
    accepting_orders: bool = True
    one_day_price_change: float | None = None
    one_week_price_change: float | None = None

    # Extra fields consumed by scanner filtering
    category: str = "other"


class BacktestDataService:
    """Parquet-backed data service that duck-types :class:`MarketDataService`.

    Every public method mirrors the signature used by the scanner, engine,
    and strategies so they work without modification.
    """

    def __init__(self, loader: ParquetLoader, clock: SimulatedClock) -> None:
        self._loader = loader
        self._clock = clock

    def get_current_time(self) -> datetime:
        """Return the current simulated time."""
        return self._clock.now()

    # ── Market discovery ────────────────────────────────────────────────

    def get_active_binary_markets(
        self,
        volume_min: float = 0,
        volume_max: float | None = None,
        limit: int = 500,
    ) -> list[BacktestMarketProxy]:
        """Return markets active at the current simulated time."""
        sim_time = self._clock.now()
        active_rows = self._loader.get_active_markets_at(sim_time)

        proxies: list[BacktestMarketProxy] = []
        for m in active_rows:
            if m.volume < volume_min:
                continue
            if volume_max is not None and m.volume > volume_max:
                continue

            proxy = self._market_row_to_proxy(m, sim_time)
            proxies.append(proxy)

        return proxies

    def get_market_by_id(self, market_id: str) -> BacktestMarketProxy | None:
        """Return a single market proxy at the current sim time."""
        m = self._loader.get_market_by_id(market_id)
        if m is None:
            return None
        return self._market_row_to_proxy(m, self._clock.now())

    # ── Snapshot building ───────────────────────────────────────────────

    def build_snapshot(self, market: BacktestMarketProxy) -> MarketSnapshot | None:
        """Build a :class:`MarketSnapshot` from a proxy, using parquet prices."""
        sim_time = self._clock.now()

        yes_token = market.token_ids[0] if len(market.token_ids) >= 1 else None
        no_token = market.token_ids[1] if len(market.token_ids) >= 2 else None
        if not yes_token or not no_token:
            return None

        # Current price from price history
        price_yes = self._loader.get_price_at(yes_token, sim_time)
        if price_yes is None:
            return None
        price_no = 1.0 - price_yes

        # Price changes from history
        day_change, week_change = self._loader.compute_price_changes(yes_token, sim_time)

        # Resolution detection: if market's closed_time <= sim_time, it's resolved
        is_closed, final_outcome, closed_time = self._loader.get_market_outcome(market.id)
        if is_closed and closed_time is not None and closed_time <= sim_time:
            outcome_prices = final_outcome  # e.g. [0.99, 0.01] or [0.01, 0.99]
        else:
            outcome_prices = [price_yes, price_no]

        tags = _parse_tag_labels(
            self._loader.get_market_by_id(market.id).tags
            if self._loader.get_market_by_id(market.id)
            else None
        )
        category = _classify_category(
            self._loader.get_market_by_id(market.id).tags
            if self._loader.get_market_by_id(market.id)
            else None,
            market.question,
        )

        return MarketSnapshot(
            market_id=market.id,
            condition_id=market.condition_id,
            question=market.question,
            token_id_yes=yes_token,
            token_id_no=no_token,
            category=category,
            tags=tags,
            price_yes=price_yes,
            price_no=price_no,
            best_bid_yes=market.best_bid,
            best_ask_yes=market.best_ask,
            spread=market.spread,
            volume=market.volume_num,
            liquidity=market.liquidity_num,
            created_at=market.created_at,
            end_date=market.end_date,
            accepting_orders=market.accepting_orders,
            one_day_price_change=day_change,
            one_week_price_change=week_change,
            outcome_prices=outcome_prices,
            snapshot_time=sim_time,
        )

    # ── Price endpoints ─────────────────────────────────────────────────

    def get_midpoint(self, token_id: str) -> float | None:
        return self._loader.get_price_at(token_id, self._clock.now())

    def get_midpoints_batch(self, token_ids: list[str]) -> dict[str, float]:
        sim_time = self._clock.now()
        result: dict[str, float] = {}
        for tid in token_ids:
            price = self._loader.get_price_at(tid, sim_time)
            if price is not None:
                result[tid] = price
        return result

    def get_price_history(
        self,
        token_id: str,
        start_ts: int | None = None,
        fidelity: int = 60,
    ) -> list[tuple[int, float]]:
        sim_time = self._clock.now()
        if start_ts is not None:
            start = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        else:
            start = sim_time - timedelta(hours=24)
        return self._loader.get_price_history_window(token_id, start, sim_time)

    def fetch_price_histories_batch(
        self,
        requests: list[tuple[str, int | None, int]],
    ) -> dict[str, list[tuple[int, float]]]:
        result: dict[str, list[tuple[int, float]]] = {}
        for token_id, start_ts, fidelity in requests:
            result[token_id] = self.get_price_history(token_id, start_ts, fidelity)
        return result

    def enrich_with_price_history(
        self, snapshot: MarketSnapshot, lookback_hours: int = 24
    ) -> None:
        sim_time = self._clock.now()
        start = sim_time - timedelta(hours=lookback_hours)
        snapshot.price_history_24h = self._loader.get_price_history_window(
            snapshot.token_id_yes, start, sim_time
        )

    def close(self) -> None:
        """No-op — no network resources to release."""
        pass

    # ── Internal helpers ────────────────────────────────────────────────

    def _market_row_to_proxy(
        self, m: MarketRow, sim_time: datetime
    ) -> BacktestMarketProxy:
        """Convert a :class:`MarketRow` to a :class:`BacktestMarketProxy`."""
        # Get current prices for outcome_prices
        price_yes = self._loader.get_price_at(m.token_id_yes, sim_time)
        price_no = (1.0 - price_yes) if price_yes is not None else None

        # Resolution detection
        is_closed, final_outcome, closed_time = self._loader.get_market_outcome(m.market_id)
        if is_closed and closed_time is not None and closed_time <= sim_time:
            outcome_prices = final_outcome
        else:
            outcome_prices = (
                [price_yes, price_no]
                if price_yes is not None and price_no is not None
                else None
            )

        tags = _parse_tag_labels(m.tags)
        category = _classify_category(m.tags, m.question)

        return BacktestMarketProxy(
            id=m.market_id,
            condition_id=m.condition_id,
            question=m.question,
            token_ids=[m.token_id_yes, m.token_id_no],
            outcomes=m.outcomes,
            outcome_prices=outcome_prices,
            tags=tags,
            volume_num=m.volume,
            liquidity_num=m.liquidity,
            best_bid=m.best_bid,
            best_ask=m.best_ask,
            spread=m.spread,
            created_at=m.created_at,
            end_date=m.end_date,
            accepting_orders=True,  # if market is active, it's accepting orders
            one_day_price_change=m.one_day_price_change,
            one_week_price_change=m.one_week_price_change,
            category=category,
        )
