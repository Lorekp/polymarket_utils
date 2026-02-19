"""Live trading execution engine (stub).

This module is architecturally ready for live trading using the
PolymarketClobClient from polymarket_apis, but all trading methods
raise NotImplementedError. Validate strategies in simulation mode first.
"""

from __future__ import annotations

from src.bot.models import ClosedTrade, ExitReason, Position, TradeDecision

from .base import ExecutionEngine


class LiveEngine(ExecutionEngine):
    """Live trading stub — raises NotImplementedError for all operations."""

    def __init__(self, private_key: str | None = None, funder_address: str | None = None):
        self.private_key = private_key
        self.funder_address = funder_address
        # When ready for live trading, initialize here:
        #
        # from polymarket_apis import PolymarketClobClient, ApiCreds
        # import json
        # with open("api_keys.json") as f:
        #     creds_raw = json.load(f)
        # creds = ApiCreds(
        #     api_key=creds_raw["apiKey"],
        #     api_secret=creds_raw["secret"],
        #     api_passphrase=creds_raw["passphrase"],
        # )
        # self.clob = PolymarketClobClient(
        #     private_key=private_key,
        #     address=funder_address,
        #     creds=creds,
        #     chain_id=137,
        # )

    def execute_trade(self, decision: TradeDecision, question: str, condition_id: str) -> Position | None:
        raise NotImplementedError(
            "Live trading is not yet implemented. "
            "Use simulation mode (mode='simulation' in config.json) to validate strategies first."
        )

    def close_position(self, position: Position, exit_price: float, reason: ExitReason) -> ClosedTrade:
        raise NotImplementedError("Live trading is not yet implemented.")

    def get_open_positions(self) -> list[Position]:
        return []

    def get_closed_trades(self) -> list[ClosedTrade]:
        return []

    def get_portfolio_value(self) -> float:
        return 0.0

    def get_cash(self) -> float:
        return 0.0
