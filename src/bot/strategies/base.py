"""Abstract base class for all trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.bot.models import ExitReason, MarketSnapshot, Position, Signal


class Strategy(ABC):
    """Base interface for a trading strategy."""

    def __init__(self, name: str, weight: float = 1.0):
        self.name = name
        self.weight = weight

    @abstractmethod
    def evaluate(self, snapshot: MarketSnapshot) -> Signal | None:
        """Evaluate a market and return a Signal if an opportunity exists."""
        ...

    @abstractmethod
    def should_close(
        self, position: Position, snapshot: MarketSnapshot
    ) -> tuple[bool, ExitReason | None]:
        """Check if an open position should be closed.

        Returns (should_close, reason).
        """
        ...
