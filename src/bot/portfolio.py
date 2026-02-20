"""Portfolio manager — position tracking, risk limits, and daily loss protection."""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.bot.config import PositionSizingConfig
from src.bot.execution.base import ExecutionEngine


class Portfolio:
    """Central risk management layer.

    Risk controls (per v4 analysis recommendations):
    - Max single position: max_position_pct of portfolio (default 5%)
    - Max total exposure: max_total_exposure_pct of portfolio (default 30%)
    - Daily loss limit: pause new trades after daily_loss_limit_pct drawdown (default 5%)
    """

    def __init__(self, engine: ExecutionEngine, config: PositionSizingConfig, clock=None):
        self.engine = engine
        self.config = config
        self._clock = clock  # optional SimulatedClock for backtesting

        # Daily loss tracking
        self._day_start_value: float | None = None
        self._current_day: date | None = None
        self._daily_limit_hit: bool = False

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

    def check_daily_limit(self) -> bool:
        """Check if the daily loss limit has been breached.

        Returns True if trading should be paused (drawdown >= daily_loss_limit_pct).
        Resets automatically at the start of each new UTC day.

        From v4 recommendation: pause trading after 5% bankroll drawdown in a day.
        """
        today = self._clock.today() if self._clock is not None else datetime.now(timezone.utc).date()

        # New day: reset tracking
        if self._current_day != today:
            self._current_day = today
            self._day_start_value = self.engine.get_portfolio_value()
            self._daily_limit_hit = False
            return False

        # Already hit today — stay paused for the rest of the day
        if self._daily_limit_hit:
            return True

        if self._day_start_value is None or self._day_start_value <= 0:
            return False

        current_value = self.engine.get_portfolio_value()
        daily_drawdown = (self._day_start_value - current_value) / self._day_start_value

        if daily_drawdown >= self.config.daily_loss_limit_pct:
            self._daily_limit_hit = True
            return True

        return False

    def has_position_in_market(self, market_id: str) -> bool:
        """Check if we already have an open position in this market."""
        return any(p.market_id == market_id for p in self.engine.get_open_positions())

    def get_summary(self) -> dict:
        """Portfolio summary for logging."""
        positions = self.engine.get_open_positions()
        closed = self.engine.get_closed_trades()
        total_realized = sum(t.realized_pnl for t in closed)
        total_unrealized = sum(p.unrealized_pnl for p in positions)

        summary = {
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
            "daily_limit_hit": self._daily_limit_hit,
        }

        # Add daily drawdown info if tracking
        if self._day_start_value is not None and self._day_start_value > 0:
            current_value = self.engine.get_portfolio_value()
            daily_dd = (self._day_start_value - current_value) / self._day_start_value
            summary["daily_drawdown_pct"] = round(daily_dd * 100, 2)

        return summary
