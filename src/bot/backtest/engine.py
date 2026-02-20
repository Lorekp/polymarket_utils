"""Backtest execution engine — uses simulated time and parquet prices."""

from __future__ import annotations

import logging

from src.bot.backtest.clock import SimulatedClock
from src.bot.backtest.parquet_loader import ParquetLoader
from src.bot.execution.base import ExecutionEngine
from src.bot.models import ClosedTrade, ExitReason, Position, TradeDecision

logger = logging.getLogger(__name__)

SLIPPAGE_PCT = 0.005  # 0.5% — same as SimulationEngine


class BacktestExecutionEngine(ExecutionEngine):
    """Execution engine for backtesting.

    Mirrors :class:`SimulationEngine` but uses a :class:`SimulatedClock`
    for timestamps and a :class:`ParquetLoader` for price lookups.
    """

    def __init__(
        self,
        initial_bankroll: float,
        clock: SimulatedClock,
        loader: ParquetLoader,
    ) -> None:
        self.cash = initial_bankroll
        self.initial_bankroll = initial_bankroll
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[ClosedTrade] = []
        self._clock = clock
        self._loader = loader
        self._trade_counter = 0

    def execute_trade(
        self, decision: TradeDecision, question: str, condition_id: str
    ) -> Position | None:
        if decision.size_usd > self.cash:
            logger.warning(
                "Insufficient funds: need $%.2f, have $%.2f",
                decision.size_usd,
                self.cash,
            )
            return None

        # Use parquet price at current simulated time (like SimulationEngine
        # uses live CLOB midpoint).  Fall back to the decision's fill_price.
        sim_time = self._clock.now()
        raw_price = decision.fill_price
        mid = self._loader.get_price_at(decision.signal.token_id, sim_time)
        if mid is not None:
            raw_price = mid

        # Apply simulated slippage (same as SimulationEngine)
        fill_price = raw_price * (1 + SLIPPAGE_PCT)
        fill_price = max(0.01, min(0.99, fill_price))
        shares = decision.size_usd / fill_price

        self._trade_counter += 1
        position_id = f"BT-{self._trade_counter:06d}"

        position = Position(
            position_id=position_id,
            market_id=decision.signal.market_id,
            condition_id=condition_id,
            token_id=decision.signal.token_id,
            question=question,
            side=decision.signal.side,
            entry_price=fill_price,
            size_usd=decision.size_usd,
            size_shares=shares,
            entry_time=sim_time,
            strategy_name=decision.signal.strategy_name,
            current_price=fill_price,
        )

        self.cash -= decision.size_usd
        self.positions[position_id] = position

        logger.info(
            "OPEN %s | %s | %.2f shares @ $%.4f | $%.2f | %s",
            position_id,
            decision.signal.side,
            shares,
            fill_price,
            decision.size_usd,
            question[:60],
        )
        return position

    def close_position(
        self, position: Position, exit_price: float, reason: ExitReason
    ) -> ClosedTrade:
        exit_value = position.size_shares * exit_price
        realized_pnl = exit_value - position.size_usd

        if reason == ExitReason.RESOLVED_WIN:
            exit_value = position.size_shares * 1.0
            realized_pnl = exit_value - position.size_usd
        elif reason == ExitReason.RESOLVED_LOSS:
            exit_value = 0.0
            realized_pnl = -position.size_usd

        closed = ClosedTrade(
            position_id=position.position_id,
            market_id=position.market_id,
            condition_id=position.condition_id,
            token_id=position.token_id,
            question=position.question,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            size_usd=position.size_usd,
            size_shares=position.size_shares,
            entry_time=position.entry_time,
            exit_time=self._clock.now(),
            realized_pnl=realized_pnl,
            strategy_name=position.strategy_name,
            exit_reason=reason,
        )

        self.cash += max(0, exit_value)
        del self.positions[position.position_id]
        self.closed_trades.append(closed)

        logger.info(
            "CLOSE %s | %s | P&L $%.2f | %s | %s",
            position.position_id,
            reason,
            realized_pnl,
            position.question[:60],
            position.strategy_name,
        )
        return closed

    def update_marks(self) -> None:
        """Mark all open positions to market at the current simulated time."""
        sim_time = self._clock.now()
        for pos in self.positions.values():
            price = self._loader.get_price_at(pos.token_id, sim_time)
            if price is not None:
                pos.current_price = price
                pos.unrealized_pnl = (price - pos.entry_price) * pos.size_shares

    def get_open_positions(self) -> list[Position]:
        return list(self.positions.values())

    def get_closed_trades(self) -> list[ClosedTrade]:
        return list(self.closed_trades)

    def get_portfolio_value(self) -> float:
        positions = list(self.positions.values())
        unrealized = sum(p.unrealized_pnl for p in positions)
        position_cost = sum(p.size_usd for p in positions)
        return self.cash + position_cost + unrealized

    def get_cash(self) -> float:
        return self.cash
