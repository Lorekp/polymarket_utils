"""Load and index parquet files for efficient backtesting lookups."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MarketRow:
    """Pre-parsed market metadata from the markets parquet."""

    market_id: str
    condition_id: str
    event_id: str
    question: str
    token_id_yes: str
    token_id_no: str
    category: str | None
    tags: str | None  # pipe-separated labels
    outcomes: str | None
    outcome_prices: str | None  # JSON string like "[\"0.99\",\"0.01\"]"
    volume: float
    liquidity: float
    spread: float | None
    best_bid: float | None
    best_ask: float | None
    created_at: datetime | None
    end_date: datetime | None
    closed_time: datetime | None
    accepting_orders: bool
    one_day_price_change: float | None
    one_week_price_change: float | None


class ParquetLoader:
    """Loads parquet data and provides fast temporal lookups.

    Price lookups use pre-built numpy arrays with ``np.searchsorted``
    for O(log n) performance.
    """

    def __init__(self, markets_path: str, price_history_path: str) -> None:
        self._markets_path = markets_path
        self._price_history_path = price_history_path

        # Populated by load()
        self._markets: list[MarketRow] = []
        self._market_by_id: dict[str, MarketRow] = {}
        self._markets_sorted_created: list[MarketRow] = []

        # Per-token price arrays: token_id -> (timestamps_ns array, prices array)
        self._price_ts: dict[str, np.ndarray] = {}
        self._price_vals: dict[str, np.ndarray] = {}

    def load(self) -> None:
        """Load both parquet files and build indices."""
        self._load_markets()
        self._load_price_history()

    # ── Markets ─────────────────────────────────────────────────────────

    def _load_markets(self) -> None:
        logger.info("Loading markets from %s", self._markets_path)
        df = pd.read_parquet(self._markets_path)
        logger.info("Markets parquet: %d rows, %d columns", len(df), len(df.columns))

        markets: list[MarketRow] = []
        for _, row in df.iterrows():
            # Parse token_ids JSON → list of strings
            token_ids_raw = row.get("token_ids")
            token_ids = self._parse_json_list(token_ids_raw)
            if not token_ids or len(token_ids) < 2:
                continue  # skip non-binary

            # Order: first token = Yes, second = No (Polymarket convention)
            token_yes = token_ids[0]
            token_no = token_ids[1]

            m = MarketRow(
                market_id=str(row.get("id", "")),
                condition_id=str(row.get("condition_id", "")),
                event_id=str(row.get("event_id", "")),
                question=str(row.get("question", "")),
                token_id_yes=token_yes,
                token_id_no=token_no,
                category=self._safe_str(row.get("event_category")),
                tags=self._safe_str(row.get("event_tags")),
                outcomes=self._safe_str(row.get("outcomes")),
                outcome_prices=self._safe_str(row.get("outcome_prices")),
                volume=float(row.get("volume_num") or 0),
                liquidity=float(row.get("liquidity_num") or 0),
                spread=self._safe_float(row.get("spread")),
                best_bid=self._safe_float(row.get("best_bid")),
                best_ask=self._safe_float(row.get("best_ask")),
                created_at=self._parse_datetime(row.get("created_at")),
                end_date=self._parse_datetime(row.get("end_date")),
                closed_time=self._parse_datetime(row.get("closed_time")),
                accepting_orders=bool(row.get("accepting_orders", False)),
                one_day_price_change=self._safe_float(row.get("one_day_price_change")),
                one_week_price_change=self._safe_float(row.get("one_week_price_change")),
            )
            markets.append(m)

        self._markets = markets
        self._market_by_id = {m.market_id: m for m in markets}
        self._markets_sorted_created = sorted(
            markets, key=lambda m: m.created_at or datetime.min.replace(tzinfo=timezone.utc)
        )
        logger.info("Indexed %d binary markets", len(markets))

    # ── Price history ───────────────────────────────────────────────────

    def _load_price_history(self) -> None:
        """Load price history using PyArrow for memory efficiency.

        With 64M+ rows the pandas groupby path is too expensive.  We filter
        to only tokens belonging to indexed markets (drops ~90% of rows),
        then sort the much smaller table and split into per-token numpy arrays.
        """
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        logger.info("Loading price history from %s", self._price_history_path)
        table = pq.read_table(
            self._price_history_path,
            columns=["token_id", "timestamp", "price"],
        )
        n_rows = table.num_rows
        logger.info("Price history parquet: %d rows", n_rows)

        # Only keep tokens that belong to indexed markets (huge memory savings)
        relevant_tokens: set[str] = set()
        for m in self._markets:
            relevant_tokens.add(m.token_id_yes)
            relevant_tokens.add(m.token_id_no)

        logger.info("Filtering to %d relevant tokens...", len(relevant_tokens))
        token_set = pa.array(list(relevant_tokens), type=pa.large_string())

        # Cast token_id to large_string to avoid offset overflow on large datasets
        token_col = table.column("token_id")
        if token_col.type != pa.large_string():
            token_col = token_col.cast(pa.large_string())
            table = table.set_column(
                table.schema.get_field_index("token_id"), "token_id", token_col,
            )

        mask = pc.is_in(table.column("token_id"), value_set=token_set)
        table = table.filter(mask)
        logger.info("After filtering: %d rows", table.num_rows)

        if table.num_rows == 0:
            logger.info("No price history data for relevant tokens.")
            return

        # Sort the (now much smaller) table
        table = table.sort_by([("token_id", "ascending"), ("timestamp", "ascending")])

        # Extract columns as numpy-compatible arrays
        token_ids = table.column("token_id").to_pylist()
        ts_np = (
            table.column("timestamp")
            .to_pandas()
            .values.astype("datetime64[ns]")
            .astype(np.int64)
        )
        price_np = table.column("price").to_numpy().astype(np.float64)

        # Free the Arrow table
        del table

        # Split into per-token arrays using the sorted token_id column
        n = len(token_ids)
        start = 0
        current_token = token_ids[0]

        for i in range(1, n + 1):
            if i == n or token_ids[i] != current_token:
                self._price_ts[current_token] = ts_np[start:i].copy()
                self._price_vals[current_token] = price_np[start:i].copy()
                if i < n:
                    current_token = token_ids[i]
                    start = i

        logger.info("Built price indices for %d tokens", len(self._price_ts))

    # ── Public query methods ────────────────────────────────────────────

    def get_market_by_id(self, market_id: str) -> MarketRow | None:
        return self._market_by_id.get(market_id)

    def get_active_markets_at(self, sim_time: datetime) -> list[MarketRow]:
        """Return markets that are active (created and not yet closed) at *sim_time*."""
        result: list[MarketRow] = []
        for m in self._markets:
            # Must have been created before sim_time
            if m.created_at is None or m.created_at > sim_time:
                continue
            # Must not yet be closed at sim_time
            if m.closed_time is not None and m.closed_time <= sim_time:
                continue
            result.append(m)
        return result

    def get_price_at(self, token_id: str, sim_time: datetime) -> float | None:
        """Get the most recent price for *token_id* at or before *sim_time*.

        Uses ``np.searchsorted`` for O(log n) binary search.
        """
        ts_arr = self._price_ts.get(token_id)
        if ts_arr is None or len(ts_arr) == 0:
            return None

        target_ns = self._dt_to_ns(sim_time)
        idx = np.searchsorted(ts_arr, target_ns, side="right") - 1
        if idx < 0:
            return None
        return float(self._price_vals[token_id][idx])

    def get_price_history_window(
        self,
        token_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[tuple[int, float]]:
        """Return price points in ``[start_time, end_time]`` as ``(unix_ts, price)``."""
        ts_arr = self._price_ts.get(token_id)
        if ts_arr is None or len(ts_arr) == 0:
            return []

        start_ns = self._dt_to_ns(start_time)
        end_ns = self._dt_to_ns(end_time)

        left = np.searchsorted(ts_arr, start_ns, side="left")
        right = np.searchsorted(ts_arr, end_ns, side="right")

        if left >= right:
            return []

        prices = self._price_vals[token_id]
        return [
            (int(ts_arr[i] // 1_000_000_000), float(prices[i]))
            for i in range(left, right)
        ]

    def compute_price_changes(
        self, token_id: str, sim_time: datetime
    ) -> tuple[float | None, float | None]:
        """Compute 1-day and 1-week absolute price changes at *sim_time*."""
        current = self.get_price_at(token_id, sim_time)
        if current is None:
            return None, None

        price_1d_ago = self.get_price_at(token_id, sim_time - timedelta(days=1))
        price_1w_ago = self.get_price_at(token_id, sim_time - timedelta(weeks=1))

        day_change = (current - price_1d_ago) if price_1d_ago is not None else None
        week_change = (current - price_1w_ago) if price_1w_ago is not None else None
        return day_change, week_change

    def get_market_outcome(
        self, market_id: str
    ) -> tuple[bool, list[float] | None, datetime | None]:
        """Return (is_closed, final_outcome_prices, closed_time) from the parquet.

        ``final_outcome_prices`` is ``[yes_price, no_price]`` parsed from
        the ``outcome_prices`` column (only meaningful when *is_closed*).
        """
        m = self._market_by_id.get(market_id)
        if m is None:
            return False, None, None

        is_closed = m.closed_time is not None
        outcome = self._parse_outcome_prices(m.outcome_prices)
        return is_closed, outcome, m.closed_time

    def get_all_token_ids(self) -> set[str]:
        """Return the set of all token IDs that have price data."""
        return set(self._price_ts.keys())

    # ── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _dt_to_ns(dt: datetime) -> int:
        """Convert a timezone-aware datetime to nanoseconds since epoch."""
        return int(dt.timestamp() * 1_000_000_000)

    @staticmethod
    def _parse_json_list(value) -> list[str] | None:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    @staticmethod
    def _parse_outcome_prices(value) -> list[float] | None:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [float(v) for v in parsed]
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _parse_datetime(value) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, pd.Timestamp):
            if pd.isna(value):
                return None
            dt = value.to_pydatetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                return None
        return None

    @staticmethod
    def _safe_float(value) -> float | None:
        if value is None:
            return None
        try:
            f = float(value)
            return None if np.isnan(f) else f
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_str(value) -> str | None:
        if value is None:
            return None
        if isinstance(value, float) and np.isnan(value):
            return None
        return str(value)
