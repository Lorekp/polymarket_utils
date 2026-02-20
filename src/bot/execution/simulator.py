"""Simulation (paper trading) execution engine."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.bot.market_data import MarketDataService
from src.bot.models import ClosedTrade, ExitReason, Position, Side, TradeDecision

from .base import ExecutionEngine

logger = logging.getLogger(__name__)

SLIPPAGE_PCT = 0.005  # 0.5% simulated slippage


class SimulationEngine(ExecutionEngine):
    """Paper trading engine — tracks hypothetical positions and P&L."""

    def __init__(self, initial_bankroll: float = 10_000.0, data_service: MarketDataService | None = None):
        self.cash = initial_bankroll
        self.initial_bankroll = initial_bankroll
        self.positions: dict[str, Position] = {}  # position_id -> Position
        self.closed_trades: list[ClosedTrade] = []
        self._data_service = data_service
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

        # Use live CLOB midpoint when available so entry price and
        # update_marks() use the same price source (no phantom P&L).
        # Fall back to the strategy's Gamma-sourced fill_price if the
        # midpoint call fails.
        raw_price = decision.fill_price
        if self._data_service is not None:
            mid = self._data_service.get_midpoint(decision.signal.token_id)
            if mid is not None:
                raw_price = mid

        # Apply simulated slippage — raw_price is the midpoint of the
        # token we're buying (Yes token for BUY_YES, No token for BUY_NO),
        # so slippage always raises the price we pay.
        fill_price = raw_price * (1 + SLIPPAGE_PCT)

        # Clamp to (0, 1)
        fill_price = max(0.01, min(0.99, fill_price))
        shares = decision.size_usd / fill_price

        self._trade_counter += 1
        position_id = f"SIM-{self._trade_counter:06d}"

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
            entry_time=datetime.now(timezone.utc),
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
        # exit_price is the price of the token we hold (Yes price for
        # BUY_YES, No price for BUY_NO) — same convention as entry_price.
        exit_value = position.size_shares * exit_price
        realized_pnl = exit_value - position.size_usd

        # For resolution: if we won, the token is worth $1; if lost, $0
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
            exit_time=datetime.now(timezone.utc),
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

    def update_marks(self, data_service: MarketDataService) -> None:
        """Mark all open positions to market.

        pos.token_id is already the token we hold (Yes token for BUY_YES,
        No token for BUY_NO), so the midpoint is directly comparable to
        entry_price in both cases — no 1-mid conversion needed.
        """
        for pos in self.positions.values():
            mid = data_service.get_midpoint(pos.token_id)
            if mid is not None:
                pos.current_price = mid
                pos.unrealized_pnl = (mid - pos.entry_price) * pos.size_shares

    def update_marks_batch(self, data_service: MarketDataService) -> None:
        """Mark all open positions using a single batch midpoints call.

        Used by the background refresh thread between cycles so it
        doesn't block the countdown loop or pollute the sync rate limiter.
        Snapshots the positions list to avoid RuntimeError if the main
        thread adds/removes positions concurrently.
        """
        positions_snapshot = list(self.positions.values())
        if not positions_snapshot:
            return
        token_ids = [pos.token_id for pos in positions_snapshot]
        mids = data_service.get_midpoints_batch(token_ids)
        for pos in positions_snapshot:
            mid = mids.get(pos.token_id)
            if mid is not None:
                pos.current_price = mid
                pos.unrealized_pnl = (mid - pos.entry_price) * pos.size_shares

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
