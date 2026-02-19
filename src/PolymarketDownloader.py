import asyncio
import datetime
import json
import logging
import time
from pathlib import Path

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
PAGE_SIZE = 500

# Schema for the price history parquet file (defined once, reused)
HISTORY_SCHEMA = pa.schema([
    ("token_id",     pa.string()),
    ("market_id",    pa.string()),
    ("condition_id", pa.string()),
    ("event_id",     pa.string()),
    ("timestamp",    pa.timestamp("us", tz="UTC")),
    ("price",        pa.float64()),
])


# ── Rate limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    """Simple next-slot rate limiter — no burst, perfectly smooth."""

    def __init__(self, rate: float):
        self.rate = rate
        self._interval = 1.0 / rate
        self._lock = asyncio.Lock()
        self._next_time: float | None = None

    async def acquire(self):
        async with self._lock:
            now = asyncio.get_event_loop().time()
            if self._next_time is None:
                self._next_time = now

            if now < self._next_time:
                await asyncio.sleep(self._next_time - now)

            self._next_time = max(now, self._next_time) + self._interval


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_dt(dt: datetime.datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_list(value) -> list | None:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _extract_tag_labels(tags) -> str | None:
    if not tags or not isinstance(tags, list):
        return None
    labels = [t.get("label") or t.get("slug", "") for t in tags if isinstance(t, dict)]
    return " | ".join(labels) if labels else None


def _extract_tag_ids(tags) -> str | None:
    if not tags or not isinstance(tags, list):
        return None
    ids = [str(t["id"]) for t in tags if isinstance(t, dict) and "id" in t]
    return " | ".join(ids) if ids else None


def _extract_category_labels(categories) -> str | None:
    if not categories or not isinstance(categories, list):
        return None
    labels = [c.get("label", "") for c in categories if isinstance(c, dict)]
    return " | ".join(labels) if labels else None


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, (httpx.ConnectError, OSError)):
        msg = str(exc).lower()
        if "getaddrinfo" in msg or "name or service" in msg or "nodename" in msg:
            return True
    return False


def _ts_to_arrow(unix_ts: int) -> int:
    """Convert unix seconds to microseconds for Arrow timestamp('us')."""
    return int(unix_ts) * 1_000_000


# ── Main class ───────────────────────────────────────────────────────────────

class PolymarketDownloader:
    """
    Downloads Polymarket event + market data and CLOB price history
    to parquet files.  Price history is streamed to disk in chunks
    to keep memory usage bounded.
    """

    # CLOB Price History: 1000 req / 10s → 100/s.  Target 80/s for margin.
    CLOB_RATE = 80
    CLOB_CONCURRENCY = 15
    CLOB_TIMEOUT = 30.0
    CLOB_MAX_RETRIES = 5
    CLOB_RETRY_BASE = 3.0

    # Flush price history to disk every CHUNK_SIZE tokens
    CHUNK_SIZE = 5000

    def __init__(self, settings=None):
        self.settings = settings
        self.http = httpx.Client(http2=True, timeout=60.0)

    # ── Sync HTTP (events pagination) ────────────────────────────────────────

    def _get_with_retry(self, url: str, params: dict, label: str = "") -> dict | list:
        for attempt in range(1, 4):
            try:
                response = self.http.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as e:
                if attempt < 3:
                    wait = attempt * 5
                    logger.warning(f"Attempt {attempt} failed ({label}): {e} — retrying in {wait}s")
                    time.sleep(wait)
                else:
                    raise

    def _fetch_events_page(self, *, offset: int, limit: int, **filters) -> list[dict]:
        params: dict = {"limit": limit, "offset": offset}
        for key in ("order", "ascending", "closed", "tag_id"):
            if filters.get(key) is not None:
                params[key] = filters[key]
        for key in ("start_date_min", "start_date_max", "end_date_min", "end_date_max"):
            if filters.get(key) is not None:
                params[key] = _fmt_dt(filters[key])
        return self._get_with_retry(GAMMA_EVENTS_URL, params, label=f"events offset={offset}")

    # ── Async price history with chunked disk writes ─────────────────────────

    async def _fetch_one_history(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        limiter: RateLimiter,
        token_id: str,
        market_id: str,
        condition_id: str,
        event_id: str,
        fidelity: int,
        buffer: list,
        buffer_lock: asyncio.Lock,
        writer_state: dict,
        pbar: tqdm,
    ) -> None:
        """Fetch one token's history, append to shared buffer, flush if full."""
        rows = []

        for attempt in range(1, self.CLOB_MAX_RETRIES + 1):
            async with sem:
                await limiter.acquire()
                try:
                    resp = await client.get(
                        "https://clob.polymarket.com/prices-history",
                        params={
                            "market": token_id,
                            "startTs": int(datetime.datetime(2020, 1, 1).timestamp()),
                            "fidelity": fidelity,
                        },
                    )
                    resp.raise_for_status()
                    history = resp.json().get("history", [])
                    for point in history:
                        rows.append((
                            token_id,
                            market_id,
                            condition_id,
                            event_id,
                            _ts_to_arrow(point["t"]),
                            float(point["p"]),
                        ))
                    break

                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        wait = self.CLOB_RETRY_BASE * (2 ** attempt)
                        if attempt < self.CLOB_MAX_RETRIES:
                            logger.warning(f"429 rate limited (attempt {attempt}), backing off {wait:.0f}s…")
                            await asyncio.sleep(wait)
                        else:
                            logger.warning(f"429 — gave up after {self.CLOB_MAX_RETRIES} attempts: {token_id[:16]}…")
                    else:
                        logger.warning(f"HTTP {e.response.status_code} for {token_id[:16]}…")
                        break

                except Exception as e:
                    if _is_transient(e) and attempt < self.CLOB_MAX_RETRIES:
                        wait = self.CLOB_RETRY_BASE * attempt
                        logger.debug(f"Transient error (attempt {attempt}), retry in {wait:.0f}s: {e}")
                        await asyncio.sleep(wait)
                    else:
                        if attempt == self.CLOB_MAX_RETRIES:
                            logger.warning(f"Failed after {self.CLOB_MAX_RETRIES} retries: {token_id[:16]}… — {e}")
                        else:
                            logger.warning(f"Non-retryable error for {token_id[:16]}…: {e}")
                        break

        # Add rows to shared buffer, flush to disk if large enough
        if rows:
            async with buffer_lock:
                buffer.extend(rows)
                writer_state["total_rows"] += len(rows)
                if len(buffer) >= 500_000:  # ~500k rows ≈ 50-100 MB
                    self._flush_buffer(buffer, writer_state)

        pbar.update(1)

    def _flush_buffer(self, buffer: list, writer_state: dict) -> None:
        """Write buffer contents to the parquet writer and clear buffer."""
        if not buffer:
            return

        # Build columnar arrays from tuples
        token_ids, market_ids, condition_ids, event_ids, timestamps, prices = zip(*buffer)

        table = pa.table({
            "token_id":     pa.array(token_ids, type=pa.string()),
            "market_id":    pa.array(market_ids, type=pa.string()),
            "condition_id": pa.array(condition_ids, type=pa.string()),
            "event_id":     pa.array(event_ids, type=pa.string()),
            "timestamp":    pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
            "price":        pa.array(prices, type=pa.float64()),
        })

        writer = writer_state["writer"]
        writer.write_table(table)
        writer_state["chunks_written"] += 1

        rows_flushed = len(buffer)
        buffer.clear()
        logger.info(f"Flushed {rows_flushed:,} rows to disk (chunk {writer_state['chunks_written']}, "
                     f"total: {writer_state['total_rows']:,})")

    async def _fetch_all_histories(
        self,
        tasks_info: list[dict],
        fidelity: int,
        history_path: Path,
    ) -> int:
        """
        Fetch all price histories concurrently, streaming to a parquet file.
        Returns total number of rows written.
        """
        sem = asyncio.Semaphore(self.CLOB_CONCURRENCY)
        limiter = RateLimiter(rate=self.CLOB_RATE)

        # Shared mutable state for chunked writing
        buffer: list[tuple] = []
        buffer_lock = asyncio.Lock()

        writer = pq.ParquetWriter(str(history_path), HISTORY_SCHEMA, compression="snappy")
        writer_state = {
            "writer": writer,
            "chunks_written": 0,
            "total_rows": 0,
        }

        pbar = tqdm(
            total=len(tasks_info),
            unit=" tok",
            dynamic_ncols=True,
            bar_format="  ↳ history {l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        )

        try:
            async with httpx.AsyncClient(
                http2=True,
                timeout=httpx.Timeout(self.CLOB_TIMEOUT),
                limits=httpx.Limits(
                    max_connections=self.CLOB_CONCURRENCY + 5,
                    max_keepalive_connections=self.CLOB_CONCURRENCY,
                ),
            ) as client:
                coros = [
                    self._fetch_one_history(
                        client, sem, limiter,
                        t["token_id"], t["market_id"], t["condition_id"], t["event_id"],
                        fidelity, buffer, buffer_lock, writer_state, pbar,
                    )
                    for t in tasks_info
                ]
                await asyncio.gather(*coros)

            # Final flush of remaining rows
            if buffer:
                self._flush_buffer(buffer, writer_state)

        finally:
            writer.close()
            pbar.close()

        return writer_state["total_rows"]

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _is_binary(self, outcomes_str) -> bool:
        if not outcomes_str:
            return False
        try:
            return len(json.loads(outcomes_str)) == 2
        except (json.JSONDecodeError, TypeError):
            return False

    @staticmethod
    def _flatten_event(event: dict) -> list[dict]:
        evt = {
            "event_id":            event.get("id"),
            "event_title":         event.get("title"),
            "event_slug":          event.get("slug"),
            "event_description":   event.get("description"),
            "event_category":      event.get("category"),
            "event_subcategory":   event.get("subcategory"),
            "event_tags":          _extract_tag_labels(event.get("tags")),
            "event_tag_ids":       _extract_tag_ids(event.get("tags")),
            "event_categories":    _extract_category_labels(event.get("categories")),
            "event_neg_risk":      event.get("negRisk"),
            "event_neg_risk_fee":  event.get("negRiskFeeBips"),
            "event_series_slug":   event.get("seriesSlug"),
            "event_volume":        event.get("volume"),
            "event_liquidity":     event.get("liquidity"),
            "event_open_interest": event.get("openInterest"),
            "event_start_date":    event.get("startDate"),
            "event_end_date":      event.get("endDate"),
            "event_closed":        event.get("closed"),
            "event_active":        event.get("active"),
            "event_competitive":   event.get("competitive"),
            "event_comment_count": event.get("commentCount"),
        }
        rows = []
        for m in event.get("markets") or []:
            token_ids = _ensure_list(m.get("clobTokenIds"))
            row = {
                **evt,
                "id":                       m.get("id"),
                "question":                 m.get("question"),
                "condition_id":             m.get("conditionId"),
                "slug":                     m.get("slug"),
                "market_type":              m.get("marketType"),
                "format_type":              m.get("formatType"),
                "sports_market_type":       m.get("sportsMarketType"),
                "outcomes":                 m.get("outcomes"),
                "outcome_prices":           m.get("outcomePrices"),
                "short_outcomes":           m.get("shortOutcomes"),
                "token_ids":                json.dumps(token_ids) if token_ids else None,
                "last_trade_price":         m.get("lastTradePrice"),
                "best_bid":                 m.get("bestBid"),
                "best_ask":                 m.get("bestAsk"),
                "spread":                   m.get("spread"),
                "one_hour_price_change":    m.get("oneHourPriceChange"),
                "one_day_price_change":     m.get("oneDayPriceChange"),
                "one_week_price_change":    m.get("oneWeekPriceChange"),
                "one_month_price_change":   m.get("oneMonthPriceChange"),
                "one_year_price_change":    m.get("oneYearPriceChange"),
                "volume_num":               m.get("volumeNum"),
                "volume_24hr":              m.get("volume24hr"),
                "volume_1wk":               m.get("volume1wk"),
                "volume_1mo":               m.get("volume1mo"),
                "volume_1yr":               m.get("volume1yr"),
                "volume_amm":               m.get("volumeAmm"),
                "volume_clob":              m.get("volumeClob"),
                "liquidity_num":            m.get("liquidityNum"),
                "liquidity_amm":            m.get("liquidityAmm"),
                "liquidity_clob":           m.get("liquidityClob"),
                "fee":                      m.get("fee"),
                "maker_base_fee":           m.get("makerBaseFee"),
                "taker_base_fee":           m.get("takerBaseFee"),
                "created_at":               m.get("createdAt"),
                "start_date":               m.get("startDateIso"),
                "end_date":                 m.get("endDateIso"),
                "accepting_orders_timestamp": m.get("acceptingOrdersTimestamp"),
                "game_start_time":          m.get("gameStartTime"),
                "event_start_time":         m.get("eventStartTime"),
                "closed_time":              m.get("closedTime"),
                "active":                   m.get("active"),
                "closed":                   m.get("closed"),
                "restricted":               m.get("restricted"),
                "accepting_orders":         m.get("acceptingOrders"),
                "fpmm_live":                m.get("fpmmLive"),
                "automatically_resolved":   m.get("automaticallyResolved"),
                "competitive":              m.get("competitive"),
                "rewards_min_size":         m.get("rewardsMinSize"),
                "rewards_max_spread":       m.get("rewardsMaxSpread"),
                "order_price_min_tick_size": m.get("orderPriceMinTickSize"),
                "order_min_size":           m.get("orderMinSize"),
                "lower_bound":              m.get("lowerBound"),
                "upper_bound":              m.get("upperBound"),
                "lower_bound_date":         m.get("lowerBoundDate"),
                "upper_bound_date":         m.get("upperBoundDate"),
                "line":                     m.get("line"),
                "team_a_id":                m.get("teamAID"),
                "team_b_id":                m.get("teamBID"),
                "game_id":                  m.get("gameId"),
                "resolution_source":        m.get("resolutionSource"),
                "uma_resolution_status":    m.get("umaResolutionStatus"),
                "resolved_by":              m.get("resolvedBy"),
                "market_group":             m.get("marketGroup"),
                "group_item_title":         m.get("groupItemTitle"),
                "group_item_threshold":     m.get("groupItemThreshold"),
                "group_item_range":         m.get("groupItemRange"),
            }
            rows.append(row)
        return rows

    # ── Main download ────────────────────────────────────────────────────────

    def downloadData(
            self,
            output_file: str = "markets.parquet",
            closed: bool | None = None,
            tag_id: int | None = None,
            start_date_min: datetime.datetime | None = None,
            start_date_max: datetime.datetime | None = None,
            end_date_min: datetime.datetime | None = None,
            end_date_max: datetime.datetime | None = None,
            min_volume: float | None = None,
            max_volume: float | None = None,
            min_liquidity: float | None = None,
            max_liquidity: float | None = None,
            category: str | None = None,
            binary_only: bool = False,
            min_spread: float | None = None,
            max_spread: float | None = None,
            resolution_source: str | None = None,
            end_date_min_local: datetime.datetime | None = None,
            end_date_max_local: datetime.datetime | None = None,
            fetch_price_history: bool = True,
            price_history_fidelity: int = 60,
            order: str = "id",
            ascending: bool = False,
    ) -> tuple[pd.DataFrame, int]:
        """
        Fetches events → flattens markets → optionally streams price history
        to parquet on disk.

        Returns:
            markets_df          — one row per market with event context
            history_row_count   — number of price history rows written (0 if skipped)
        """

        # ── 1. Fetch events ──────────────────────────────────────────────────
        all_events: list[dict] = []
        offset = 0
        api_filters = dict(
            order=order, ascending=ascending, closed=closed, tag_id=tag_id,
            start_date_min=start_date_min, start_date_max=start_date_max,
            end_date_min=end_date_min, end_date_max=end_date_max,
        )

        print("\n⏳ Fetching events from Gamma API...")
        with tqdm(unit=" events", dynamic_ncols=True, bar_format="{desc}: {n_fmt} fetched") as pbar:
            while True:
                page = self._fetch_events_page(offset=offset, limit=PAGE_SIZE, **api_filters)
                all_events.extend(page)
                pbar.update(len(page))
                pbar.set_description(f"Page {offset // PAGE_SIZE + 1}")
                if len(page) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE
        print(f"✅ Fetched {len(all_events)} events")

        # ── 2. Flatten ────────────────────────────────────────────────────────
        print("📦 Extracting markets from events...")
        market_rows: list[dict] = []
        for event in all_events:
            market_rows.extend(self._flatten_event(event))
        print(f"📊 Extracted {len(market_rows)} markets from {len(all_events)} events")

        # ── 3. Post-filters ───────────────────────────────────────────────────
        before = len(market_rows)
        if min_volume is not None:
            market_rows = [r for r in market_rows if (r["volume_num"] or 0) >= min_volume]
        if max_volume is not None:
            market_rows = [r for r in market_rows if (r["volume_num"] or 0) <= max_volume]
        if min_liquidity is not None:
            market_rows = [r for r in market_rows if (r["liquidity_num"] or 0) >= min_liquidity]
        if max_liquidity is not None:
            market_rows = [r for r in market_rows if (r["liquidity_num"] or 0) <= max_liquidity]
        if category:
            cat_lower = category.lower()
            market_rows = [r for r in market_rows
                           if (r["event_category"] and cat_lower in r["event_category"].lower())
                           or (r["event_tags"] and cat_lower in r["event_tags"].lower())]
        if binary_only:
            market_rows = [r for r in market_rows
                           if (r["market_type"] and "binary" in r["market_type"].lower())
                           or self._is_binary(r["outcomes"])]
        if min_spread is not None:
            market_rows = [r for r in market_rows if (r["spread"] or 0) >= min_spread]
        if max_spread is not None:
            market_rows = [r for r in market_rows if (r["spread"] or 0) <= max_spread]
        if resolution_source:
            market_rows = [r for r in market_rows
                           if r["resolution_source"] and resolution_source.lower() in r["resolution_source"].lower()]
        if end_date_min_local or end_date_max_local:
            def _parse_end(r):
                raw = r.get("end_date")
                if not raw:
                    return None
                try:
                    return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
                except (ValueError, TypeError):
                    return None
            if end_date_min_local:
                market_rows = [r for r in market_rows if (d := _parse_end(r)) and d >= end_date_min_local]
            if end_date_max_local:
                market_rows = [r for r in market_rows if (d := _parse_end(r)) and d <= end_date_max_local]
        if len(market_rows) != before:
            print(f"🔍 Filtered: {before} → {len(market_rows)} markets")

        # ── 4. Write markets parquet ──────────────────────────────────────────
        output_path = Path(output_file)
        markets_df = pd.DataFrame(market_rows)
        markets_df.to_parquet(output_path, index=False)
        print(f"\n💾 Saved {len(markets_df)} markets → {output_path}")

        # ── 5. Price history (async, streamed to disk) ────────────────────────
        history_row_count = 0

        if fetch_price_history:
            tasks_info = []
            for row in market_rows:
                token_ids = _ensure_list(row["token_ids"])
                if token_ids:
                    for tid in token_ids:
                        tasks_info.append({
                            "token_id": tid,
                            "market_id": row["id"],
                            "condition_id": row["condition_id"],
                            "event_id": row["event_id"],
                        })

            history_path = output_path.with_name(output_path.stem + "_price_history.parquet")
            est_minutes = len(tasks_info) / self.CLOB_RATE / 60

            print(f"\n📡 Fetching price history — {len(tasks_info)} requests")
            print(f"   Rate: {self.CLOB_RATE}/s, concurrency: {self.CLOB_CONCURRENCY}, est: ~{est_minutes:.0f} min")
            print(f"   Streaming to disk in chunks (RAM stays bounded)")

            history_row_count = asyncio.run(
                self._fetch_all_histories(tasks_info, fidelity=price_history_fidelity, history_path=history_path)
            )

            if history_row_count > 0:
                print(f"💾 Saved {history_row_count:,} price history rows → {history_path}")
            else:
                # Clean up empty file
                history_path.unlink(missing_ok=True)
                print("⚠️  No price history data returned")

        print("✅ Done!\n")
        return markets_df, history_row_count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.http.close()