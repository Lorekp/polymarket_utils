"""Simulated clock for backtesting — single source of truth for 'now'."""

from __future__ import annotations

from datetime import date, datetime


class SimulatedClock:
    """Central time authority used during a backtest.

    Every component that needs the current time (execution engine, portfolio,
    data service) reads from this clock instead of ``datetime.now(UTC)``.
    The :class:`BacktestRunner` advances it at each time step.
    """

    def __init__(self, start_time: datetime) -> None:
        self._current_time: datetime = start_time

    def now(self) -> datetime:
        """Return the current simulated time."""
        return self._current_time

    def advance_to(self, new_time: datetime) -> None:
        """Move the clock forward.  Must be monotonically non-decreasing."""
        if new_time < self._current_time:
            raise ValueError(
                f"Cannot move clock backward: {new_time} < {self._current_time}"
            )
        self._current_time = new_time

    def today(self) -> date:
        """Simulated date (used by Portfolio for daily-limit tracking)."""
        return self._current_time.date()
