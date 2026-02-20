"""Shared data models used across the trading bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class Side(StrEnum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"


class ExitReason(StrEnum):
    RESOLVED_WIN = "resolved_win"
    RESOLVED_LOSS = "resolved_loss"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    HOLD_EXPIRED = "hold_expired"
    MARKET_CLOSED = "market_closed"
    SHUTDOWN = "shutdown"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MarketSnapshot:
    """Point-in-time view of a market for strategy evaluation."""

    market_id: str
    condition_id: str
    question: str
    token_id_yes: str
    token_id_no: str
    category: str
    tags: list[str]

    # Pricing
    price_yes: float | None  # midpoint or last trade
    price_no: float | None
    best_bid_yes: float | None
    best_ask_yes: float | None
    spread: float | None

    # Volume & liquidity
    volume: float
    liquidity: float

    # Timing
    created_at: datetime | None
    end_date: datetime | None
    accepting_orders: bool

    # Gamma convenience fields
    one_day_price_change: float | None
    one_week_price_change: float | None

    # Price history for breakout detection (populated on demand)
    price_history_24h: list[tuple[int, float]] | None = None  # (unix_ts, price)

    # Outcome prices for resolution detection
    outcome_prices: list[float] | None = None

    snapshot_time: datetime = field(default_factory=_utcnow)


@dataclass
class Signal:
    """Output from a strategy indicating a trading opportunity."""

    strategy_name: str
    market_id: str
    token_id: str
    side: Side
    confidence: float  # 0.0 to 1.0
    expected_edge: float  # estimated edge in absolute terms (e.g. 0.15 = 15pp)
    reasoning: str
    raw_data: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "market_id": self.market_id,
            "token_id": self.token_id,
            "side": self.side,
            "confidence": round(self.confidence, 4),
            "expected_edge": round(self.expected_edge, 4),
            "reasoning": self.reasoning,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class TradeDecision:
    """Final trade decision after signal aggregation and risk checks."""

    signal: Signal
    size_usd: float
    fill_price: float
    timestamp: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "market_id": self.signal.market_id,
            "token_id": self.signal.token_id,
            "side": self.signal.side,
            "size_usd": round(self.size_usd, 2),
            "fill_price": round(self.fill_price, 4),
            "confidence": round(self.signal.confidence, 4),
            "expected_edge": round(self.signal.expected_edge, 4),
            "strategy": self.signal.strategy_name,
            "reasoning": self.signal.reasoning,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class Position:
    """An open simulated position."""

    position_id: str
    market_id: str
    condition_id: str
    token_id: str
    question: str
    side: Side
    entry_price: float
    size_usd: float
    size_shares: float
    entry_time: datetime
    strategy_name: str
    current_price: float | None = None
    unrealized_pnl: float = 0.0

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "market_id": self.market_id,
            "question": self.question,
            "side": self.side,
            "entry_price": round(self.entry_price, 4),
            "size_usd": round(self.size_usd, 2),
            "size_shares": round(self.size_shares, 4),
            "current_price": round(self.current_price, 4) if self.current_price is not None else None,
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "strategy": self.strategy_name,
            "entry_time": self.entry_time.isoformat(),
        }


@dataclass
class ClosedTrade:
    """A completed round-trip trade."""

    position_id: str
    market_id: str
    condition_id: str
    token_id: str
    question: str
    side: Side
    entry_price: float
    exit_price: float
    size_usd: float
    size_shares: float
    entry_time: datetime
    exit_time: datetime
    realized_pnl: float
    strategy_name: str
    exit_reason: ExitReason

    _CSV_FIELDS = [
        "position_id", "market_id", "question", "side",
        "entry_price", "exit_price", "size_usd", "size_shares",
        "realized_pnl", "strategy", "exit_reason",
        "entry_time", "exit_time", "hold_hours",
    ]

    @classmethod
    def csv_fieldnames(cls) -> list[str]:
        """Canonical CSV column order (single source of truth)."""
        return list(cls._CSV_FIELDS)

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "market_id": self.market_id,
            "question": self.question,
            "side": self.side,
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "size_usd": round(self.size_usd, 2),
            "size_shares": round(self.size_shares, 4),
            "realized_pnl": round(self.realized_pnl, 2),
            "strategy": self.strategy_name,
            "exit_reason": self.exit_reason,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "hold_hours": round((self.exit_time - self.entry_time).total_seconds() / 3600, 1),
        }
