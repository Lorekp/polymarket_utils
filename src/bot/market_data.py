"""Market data fetching layer — wraps Gamma API + CLOB public endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone

import httpx
from polymarket_apis import PolymarketGammaClient
from polymarket_apis.types.gamma_types import GammaMarket

from src.PolymarketDownloader import RateLimiter, _is_transient
from src.bot.models import MarketSnapshot

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"

# Category classification — same regex patterns as the R analysis
CATEGORY_RULES: list[tuple[str, str]] = [
    ("sports", r"nba|nfl|mlb|nhl|soccer|football|tennis|boxing|mma|ufc|cricket|esports|dota|league of legends|sports|epl|la liga|bundesliga|serie a|ligue 1|f1|formula|nascar|golf|olympics|pga"),
    ("crypto", r"crypto|bitcoin|btc|ethereum|eth|solana|sol|defi|blockchain|token|nft|altcoin|memecoin|binance|coinbase"),
    ("politics", r"politic|election|trump|biden|democrat|republican|congress|senate|governor|president|vote|gop|dnc|rnc|parliament|minister|legislation|bill|law|executive order"),
    ("economics", r"econom|fed |federal reserve|interest rate|inflation|gdp|employment|jobs|recession|tariff|trade|stock market|s&p|nasdaq|dow jones|treasury|debt|cpi"),
    ("science_tech", r"science|tech|ai |artificial intelligence|openai|google|apple|microsoft|space|spacex|nasa|climate|weather|earthquake|hurricane|temperature"),
    ("entertainment", r"movie|film|oscar|grammy|emmy|music|album|box office|streaming|netflix|youtube|tiktok|celebrity|award|tv show|series"),
    ("world_events", r"war|conflict|ukraine|russia|china|israel|gaza|nato|united nations|refugee|disaster|pandemic"),
]


def classify_category(tags: list[str], question: str = "") -> str:
    """Classify a market into a category using tag text + question."""
    combined = " ".join(tags).lower() + " " + question.lower()
    for category, pattern in CATEGORY_RULES:
        if re.search(pattern, combined):
            return category
    return "other"


def _extract_tags(market: GammaMarket) -> list[str]:
    """Extract tag labels from a GammaMarket."""
    if market.tags:
        return [t.label for t in market.tags if t.label]
    return []


def _outcome_yes_index(market: GammaMarket) -> int:
    """Return the index of the "Yes" outcome (0 or 1).

    Used by both ``_extract_token_ids`` and ``build_snapshot`` so that
    token IDs *and* outcome prices are reordered consistently.
    """
    outcomes_raw = market.outcomes
    if not outcomes_raw:
        return 0

    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
    except (json.JSONDecodeError, TypeError):
        return 0

    if len(outcomes) != 2:
        return 0

    return next((i for i, o in enumerate(outcomes) if str(o).lower() == "yes"), 0)


def _extract_token_ids(market: GammaMarket) -> tuple[str | None, str | None]:
    """Extract (yes_token_id, no_token_id) from a GammaMarket.

    Binary markets have outcomes like '["Yes","No"]' and token_ids is a
    list of two strings.  The first token corresponds to the first outcome.
    """
    token_ids = market.token_ids
    if not token_ids or len(token_ids) != 2:
        return None, None

    yes_idx = _outcome_yes_index(market)
    no_idx = 1 - yes_idx
    return token_ids[yes_idx], token_ids[no_idx]


class MarketDataService:
    """Fetches market data from Gamma API and CLOB public endpoints."""

    CLOB_RATE = 30  # sync rate (kept for remaining sync calls)
    ASYNC_RATE = 80  # async rate: 1000 req/10s limit, target 80 for margin
    ASYNC_CONCURRENCY = 15  # max parallel connections for batch fetches
    MAX_RETRIES = 3
    RETRY_BASE = 2.0

    def __init__(self):
        self.gamma = PolymarketGammaClient()
        self.http = httpx.Client(http2=True, timeout=30.0)
        self._limiter_next = 0.0
        self._limiter_interval = 1.0 / self.CLOB_RATE

    def get_current_time(self) -> datetime:
        """Return the current time.  Overridden by BacktestDataService."""
        return datetime.now(timezone.utc)

    def _rate_limit(self) -> None:
        """Simple synchronous rate limiter for CLOB calls."""
        now = time.monotonic()
        if now < self._limiter_next:
            time.sleep(self._limiter_next - now)
        self._limiter_next = max(now, self._limiter_next) + self._limiter_interval

    def _clob_get(self, endpoint: str, params: dict | None = None) -> dict | list | None:
        """GET request to CLOB API with retry."""
        url = f"{CLOB_BASE}/{endpoint}"
        for attempt in range(1, self.MAX_RETRIES + 1):
            self._rate_limit()
            try:
                resp = self.http.get(url, params=params or {})
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < self.MAX_RETRIES:
                    wait = self.RETRY_BASE * (2 ** attempt)
                    logger.warning("CLOB 429 rate limited, backing off %.0fs", wait)
                    time.sleep(wait)
                else:
                    logger.warning("CLOB %s %s failed: %s", endpoint, params, e)
                    return None
            except Exception as e:
                if _is_transient(e) and attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_BASE * attempt)
                else:
                    logger.warning("CLOB %s error: %s", endpoint, e)
                    return None
        return None

    # ── Gamma API wrappers ─────────────────────────────────────────────

    def get_active_binary_markets(
        self,
        volume_min: float = 0,
        volume_max: float | None = None,
        limit: int = 500,
    ) -> list[GammaMarket]:
        """Fetch active, non-closed markets from Gamma, filtered to binary."""
        offset = 0
        markets = []
        max_retries = 3

        while True:
            page_success = False

            # Mini-loop to attempt fetching the page multiple times
            for attempt in range(max_retries):
                try:
                    page = self.gamma.get_markets(
                        active=True,
                        closed=False,
                        volume_num_min=volume_min if volume_min > 0 else None,
                        limit=limit,
                        offset=offset
                    )
                    page_success = True
                    break

                except Exception as e:
                    logger.error("Failed fetching offset %s (Attempt %s/%s): %s", offset, attempt + 1, max_retries, e)
                    if attempt < max_retries - 1:
                        time.sleep(1)

            if not page_success:
                logger.error("Max retries reached for offset %s. Returning partial data.", offset)
                break

            # If successful, add to our list and check if we are done
            markets.extend(page)

            if len(page) < limit:
                break

            offset += limit

        binary = []
        for m in markets:
            if not (m.token_ids and len(m.token_ids) == 2 and m.accepting_orders):
                continue
            # Apply volume_max filter client-side (avoids Gamma API 422)
            if volume_max is not None and m.volume_num and m.volume_num > volume_max:
                continue
            binary.append(m)
        return binary

    def get_market_by_id(self, market_id: str) -> GammaMarket | None:
        try:
            return self.gamma.get_market(market_id)
        except Exception as e:
            logger.warning("Gamma get_market(%s) failed: %s", market_id, e)
            return None

    # ── CLOB price endpoints (no auth needed) ──────────────────────────

    def get_midpoint(self, token_id: str) -> float | None:
        data = self._clob_get("midpoint", {"token_id": token_id})
        if data and "mid" in data:
            try:
                return float(data["mid"])
            except (ValueError, TypeError):
                return None
        return None

    def get_midpoints_batch(self, token_ids: list[str]) -> dict[str, float]:
        """Fetch midpoints for multiple tokens via POST /midpoints.

        Uses the JSON-body variant to avoid URL length limits with large
        token ID lists.  Chunks into batches of 150 to stay well under
        any request-size limits.  Bypasses _clob_get / _rate_limit so it
        doesn't pollute the sync rate limiter state used by the scanner.
        """
        if not token_ids:
            return {}
        result: dict[str, float] = {}
        chunk_size = 150
        for i in range(0, len(token_ids), chunk_size):
            chunk = token_ids[i : i + chunk_size]
            try:
                resp = self.http.post(
                    f"{CLOB_BASE}/midpoints",
                    json=[{"token_id": tid} for tid in chunk],
                )
                resp.raise_for_status()
                data = resp.json()
                for tid, v in data.items():
                    try:
                        result[tid] = float(v)
                    except (ValueError, TypeError):
                        pass
            except Exception as e:
                logger.warning("Batch midpoints chunk failed: %s", e)
        return result

    def get_price_history(
        self,
        token_id: str,
        start_ts: int | None = None,
        fidelity: int = 60,
    ) -> list[tuple[int, float]]:
        """Fetch price history. Returns list of (unix_ts, price)."""
        params: dict = {"market": token_id, "fidelity": fidelity}
        if start_ts is not None:
            params["startTs"] = start_ts
        data = self._clob_get("prices-history", params)
        if not data or "history" not in data:
            return []
        return [(int(p["t"]), float(p["p"])) for p in data["history"]]

    # ── Snapshot builder ───────────────────────────────────────────────

    def build_snapshot(self, market: GammaMarket) -> MarketSnapshot | None:
        """Build a MarketSnapshot from a GammaMarket, enriched with CLOB data."""
        yes_token, no_token = _extract_token_ids(market)
        if not yes_token or not no_token:
            return None

        tags = _extract_tags(market)
        category = classify_category(tags, market.question or "")

        # Use Gamma-provided prices (avoid extra API call for most markets).
        # Reorder to match Yes/No convention (same logic as _extract_token_ids).
        prices = market.outcome_prices
        if prices and len(prices) >= 2:
            yes_idx = _outcome_yes_index(market)
            no_idx = 1 - yes_idx
            price_yes = prices[yes_idx]
            price_no = prices[no_idx]
        elif prices and len(prices) == 1:
            price_yes = prices[0]
            price_no = None
        else:
            price_yes = None
            price_no = None

        # If no price available from Gamma, try CLOB midpoint
        if price_yes is None:
            price_yes = self.get_midpoint(yes_token)
            if price_yes is not None:
                price_no = 1.0 - price_yes

        return MarketSnapshot(
            market_id=market.id or "",
            condition_id=market.condition_id or "",
            question=market.question or "",
            token_id_yes=yes_token,
            token_id_no=no_token,
            category=category,
            tags=tags,
            price_yes=price_yes,
            price_no=price_no,
            best_bid_yes=market.best_bid,
            best_ask_yes=market.best_ask,
            spread=market.spread,
            volume=market.volume_num or 0,
            liquidity=market.liquidity_num or 0,
            created_at=market.created_at,
            end_date=market.end_date,
            accepting_orders=bool(market.accepting_orders),
            one_day_price_change=market.one_day_price_change,
            one_week_price_change=market.one_week_price_change,
            outcome_prices=[price_yes, price_no] if price_yes is not None and price_no is not None else None,
        )

    def enrich_with_price_history(
        self, snapshot: MarketSnapshot, lookback_hours: int = 24
    ) -> None:
        """Populate snapshot.price_history_24h from CLOB."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        start_ts = now_ts - (lookback_hours * 3600)
        history = self.get_price_history(
            snapshot.token_id_yes, start_ts=start_ts, fidelity=60
        )
        snapshot.price_history_24h = history

    # ── Async batch price history ─────────────────────────────────────

    async def _fetch_one_price_history(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        limiter: RateLimiter,
        token_id: str,
        start_ts: int | None,
        fidelity: int,
    ) -> tuple[str, list[tuple[int, float]]]:
        """Fetch one token's price history asynchronously."""
        params: dict = {"market": token_id, "fidelity": fidelity}
        if start_ts is not None:
            params["startTs"] = start_ts

        for attempt in range(1, self.MAX_RETRIES + 1):
            async with sem:
                await limiter.acquire()
                try:
                    resp = await client.get(
                        f"{CLOB_BASE}/prices-history", params=params
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    if not data or "history" not in data:
                        return token_id, []
                    return token_id, [
                        (int(p["t"]), float(p["p"])) for p in data["history"]
                    ]
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429 and attempt < self.MAX_RETRIES:
                        wait = self.RETRY_BASE * (2 ** attempt)
                        logger.warning(
                            "CLOB 429 on %s (attempt %d), backing off %.0fs",
                            token_id[:16], attempt, wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.warning("CLOB prices-history failed for %s: %s",
                                       token_id[:16], e)
                        return token_id, []
                except Exception as e:
                    if _is_transient(e) and attempt < self.MAX_RETRIES:
                        await asyncio.sleep(self.RETRY_BASE * attempt)
                    else:
                        logger.warning("CLOB prices-history error for %s: %s",
                                       token_id[:16], e)
                        return token_id, []
        return token_id, []

    async def _fetch_price_histories_async(
        self,
        requests: list[tuple[str, int | None, int]],
    ) -> dict[str, list[tuple[int, float]]]:
        """Fetch multiple price histories concurrently."""
        sem = asyncio.Semaphore(self.ASYNC_CONCURRENCY)
        limiter = RateLimiter(rate=self.ASYNC_RATE)

        async with httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(
                max_connections=self.ASYNC_CONCURRENCY + 5,
                max_keepalive_connections=self.ASYNC_CONCURRENCY,
            ),
        ) as client:
            tasks = [
                self._fetch_one_price_history(
                    client, sem, limiter, token_id, start_ts, fidelity
                )
                for token_id, start_ts, fidelity in requests
            ]
            results = await asyncio.gather(*tasks)

        return {token_id: history for token_id, history in results}

    def fetch_price_histories_batch(
        self,
        requests: list[tuple[str, int | None, int]],
    ) -> dict[str, list[tuple[int, float]]]:
        """Fetch multiple price histories concurrently (sync entry point).

        Args:
            requests: list of (token_id, start_ts, fidelity) tuples
        Returns:
            dict mapping token_id -> [(unix_ts, price), ...]
        """
        if not requests:
            return {}
        return asyncio.run(self._fetch_price_histories_async(requests))

    def close(self) -> None:
        self.http.close()
        self.gamma.client.close()
