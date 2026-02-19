"""Portfolio manager — position tracking and risk limits."""

from __future__ import annotations

from src.bot.config import PositionSizingConfig
from src.bot.execution.base import ExecutionEngine


class Portfolio:
    """Central risk management layer."""

    def __init__(self, engine: ExecutionEngine, config: PositionSizingConfig):
        self.engine = engine
        self.config = config

    def can_open_position(self, size_usd: float) -> bool:
        """Check risk limits before opening a new position."""
        portfolio_value = self.engine.get_portfolio_value()
        if portfolio_value <= 0:
            return False

        # Check max single position size
        if size_usd / portfolio_value > self.config.max_position_pct:
            return False

        # Check max total exposure
        total_exposure = sum(p.size_usd for p in self.engine.get_open_positions())
        if (total_exposure + size_usd) / portfolio_value > self.config.max_total_exposure_pct:
            return False

        # Check sufficient cash
        if size_usd > self.engine.get_cash():
            return False

        return True

    def has_position_in_market(self, market_id: str) -> bool:
        """Check if we already have an open position in this market."""
        return any(p.market_id == market_id for p in self.engine.get_open_positions())

    def get_summary(self) -> dict:
        """Portfolio summary for logging."""
        positions = self.engine.get_open_positions()
        closed = self.engine.get_closed_trades()
        total_realized = sum(t.realized_pnl for t in closed)
        total_unrealized = sum(p.unrealized_pnl for p in positions)

        return {
            "portfolio_value": round(self.engine.get_portfolio_value(), 2),
            "cash": round(self.engine.get_cash(), 2),
            "open_positions": len(positions),
            "total_exposure": round(sum(p.size_usd for p in positions), 2),
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_realized_pnl": round(total_realized, 2),
            "total_trades": len(closed),
            "win_rate": round(
                sum(1 for t in closed if t.realized_pnl > 0) / len(closed), 3
            ) if closed else 0.0,
        }
