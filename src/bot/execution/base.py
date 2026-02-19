"""Abstract execution engine interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.bot.models import ClosedTrade, ExitReason, Position, TradeDecision


class ExecutionEngine(ABC):
    @abstractmethod
    def execute_trade(self, decision: TradeDecision, question: str, condition_id: str) -> Position | None:
        """Execute a trade. Returns the opened Position, or None on failure."""
        ...

    @abstractmethod
    def close_position(self, position: Position, exit_price: float, reason: ExitReason) -> ClosedTrade:
        """Close an existing position at the given price."""
        ...

    @abstractmethod
    def get_open_positions(self) -> list[Position]:
        """Get all open positions."""
        ...

    @abstractmethod
    def get_closed_trades(self) -> list[ClosedTrade]:
        """Get all closed trades."""
        ...

    @abstractmethod
    def get_portfolio_value(self) -> float:
        """Get total portfolio value (cash + positions at mark)."""
        ...

    @abstractmethod
    def get_cash(self) -> float:
        """Get available cash."""
        ...
