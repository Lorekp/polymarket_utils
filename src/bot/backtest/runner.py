"""Backtest runner — replays historical data through the trading bot pipeline."""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from src.bot.backtest.clock import SimulatedClock
from src.bot.backtest.data_service import BacktestDataService
from src.bot.backtest.engine import BacktestExecutionEngine
from src.bot.backtest.parquet_loader import ParquetLoader
from src.bot.config import BotConfig, load_config
from src.bot.logger import BotLogger
from src.bot.models import ExitReason, Side
from src.bot.portfolio import Portfolio
from src.bot.scanner import MarketScanner
from src.bot.scoring import ScoringConfig as ScoringDataclass, score_decision
from src.bot.strategies.base import Strategy
from src.bot.strategies.breakout import BreakoutFollower
from src.bot.strategies.category import CategoryBias
from src.bot.strategies.composite import CompositeStrategy
from src.bot.strategies.late_stage import LateStageMomentum
from src.bot.strategies.new_market import NewMarketBuyer

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Configuration for a single backtest run."""

    markets_parquet: str
    price_history_parquet: str
    start_date: datetime
    end_date: datetime
    scan_interval_minutes: int = 60
    initial_bankroll: float = 10_000.0
    bot_config_path: str = "config.json"


# Type alias for the progress callback
ProgressCallback = Callable[[int, int, dict], None]


class BacktestRunner:
    """Runs the trading bot against historical parquet data.

    Reuses the same strategies, composite aggregator, scoring, portfolio,
    and logger as :class:`TradingBot`, but replaces live data with parquet
    lookups and uses a simulated clock.
    """

    def __init__(self, bt_config: BacktestConfig) -> None:
        self.bt_config = bt_config
        self.bot_config: BotConfig = load_config(bt_config.bot_config_path)

        # Backtest-specific components
        self.clock = SimulatedClock(bt_config.start_date)
        self.loader = ParquetLoader(
            bt_config.markets_parquet,
            bt_config.price_history_parquet,
        )
        self.data_service = BacktestDataService(self.loader, self.clock)
        self.exec_engine = BacktestExecutionEngine(
            bt_config.initial_bankroll, self.clock, self.loader,
        )

        # Reuse standard bot components
        self.strategies = self._build_strategies()
        self.composite = CompositeStrategy(self.strategies, self.bot_config)
        self.portfolio = Portfolio(
            self.exec_engine, self.bot_config.position_sizing, clock=self.clock,
        )
        self.bot_logger = BotLogger(log_dir="logs")
        self.scanner = MarketScanner(
            self.data_service, self.bot_config, cache_path=None,
        )

        # Map Pydantic ScoringConfig → dataclass for scoring module
        sc = self.bot_config.scoring
        self.scoring_config = ScoringDataclass(
            min_days=sc.min_days,
            too_close_factor=sc.too_close_factor,
            long_horizon_days=sc.long_horizon_days,
            long_horizon_floor=sc.long_horizon_floor,
            no_end_date_factor=sc.no_end_date_factor,
        )

        self._cycle_count = 0

    def _build_strategies(self) -> list[Strategy]:
        strategies: list[Strategy] = []
        sc = self.bot_config.strategies

        if sc.new_market.enabled:
            strategies.append(NewMarketBuyer(sc.new_market))
        if sc.breakout.enabled:
            strategies.append(BreakoutFollower(sc.breakout))
        if sc.category.enabled:
            strategies.append(CategoryBias(sc.category))
        if sc.late_stage.enabled:
            strategies.append(LateStageMomentum(sc.late_stage))

        return strategies

    def run(
        self,
        progress_callback: ProgressCallback | None = None,
    ) -> str:
        """Execute the backtest.  Returns the session ID.

        *progress_callback(step, total, summary_dict)* is called after
        each time step so the caller can render a progress bar / GUI update.
        """
        logger.info(
            "Starting backtest: %s -> %s (interval %d min)",
            self.bt_config.start_date.date(),
            self.bt_config.end_date.date(),
            self.bt_config.scan_interval_minutes,
        )

        # Load parquet data
        t0 = time.monotonic()
        self.loader.load()
        load_sec = time.monotonic() - t0
        logger.info("Parquet data loaded in %.1fs", load_sec)

        # Generate time steps
        steps = self._generate_time_steps()
        total_steps = len(steps)
        logger.info("Generated %d time steps", total_steps)

        # Log session start
        self.bot_logger.log(
            "session_start",
            config=self.bot_config.model_dump(exclude={"private_key"}),
            bankroll=self.bt_config.initial_bankroll,
            mode="backtest",
            backtest_start=self.bt_config.start_date.isoformat(),
            backtest_end=self.bt_config.end_date.isoformat(),
            scan_interval_minutes=self.bt_config.scan_interval_minutes,
            strategies=[s.name for s in self.strategies],
        )

        t_start = time.monotonic()

        for i, step_time in enumerate(steps):
            self.clock.advance_to(step_time)
            self._cycle_count = i + 1
            self._run_cycle()

            if progress_callback is not None:
                summary = self.portfolio.get_summary()
                summary["sim_time"] = step_time.isoformat()
                progress_callback(i + 1, total_steps, summary)

        elapsed = time.monotonic() - t_start
        logger.info(
            "Backtest completed in %.1fs (%d cycles, %.1f cycles/sec)",
            elapsed, total_steps, total_steps / elapsed if elapsed > 0 else 0,
        )

        self._finalize()
        return self.bot_logger.session_id

    def _generate_time_steps(self) -> list[datetime]:
        steps: list[datetime] = []
        current = self.bt_config.start_date
        delta = timedelta(minutes=self.bt_config.scan_interval_minutes)
        while current <= self.bt_config.end_date:
            steps.append(current)
            current += delta
        return steps

    # ── Cycle logic (mirrors TradingBot._run_cycle) ─────────────────────

    def _run_cycle(self) -> None:
        # 0. Check daily loss limit
        if self.portfolio.check_daily_limit():
            self.bot_logger.log("daily_limit_hit", cycle=self._cycle_count)
            self._check_exits()
            return

        # 1. Scan for candidate markets
        try:
            candidates = self.scanner.scan()
        except Exception as e:
            logger.error("Scanner error at cycle %d: %s", self._cycle_count, e)
            self.bot_logger.log("error", source="scanner", message=str(e))
            return

        self.bot_logger.log(
            "scan_result",
            cycle=self._cycle_count,
            candidates=len(candidates),
        )

        # 2. Evaluate candidates → collect scored decisions
        signals_generated = 0
        trades_executed = 0
        max_spread = self.bot_config.volume_filter.max_spread

        scored: list[tuple] = []

        for snapshot in candidates:
            if self.portfolio.has_position_in_market(snapshot.market_id):
                continue
            if snapshot.spread is not None and snapshot.spread > max_spread:
                continue

            decision = self.composite.evaluate(snapshot)
            if decision is None:
                continue

            signals_generated += 1
            self.bot_logger.log("signal", **decision.signal.to_dict())

            result = score_decision(decision, snapshot, self.scoring_config)
            scored.append((result, decision, snapshot))

        # Sort by capital efficiency
        scored.sort(
            key=lambda x: x[0].score / max(x[1].size_usd, 1.0),
            reverse=True,
        )

        # Greedy fill
        for result, decision, snapshot in scored:
            if not self.portfolio.can_open_position(decision.size_usd):
                self.bot_logger.log(
                    "trade_rejected",
                    reason="risk_limit",
                    market_id=snapshot.market_id,
                    size_usd=decision.size_usd,
                    priority_score=round(result.score, 6),
                    time_factor=round(result.time_factor, 4),
                    quality_factor=round(result.quality_factor, 4),
                    days_until_close=(
                        round(result.days_until_close, 2)
                        if result.days_until_close is not None
                        else None
                    ),
                )
                continue

            position = self.exec_engine.execute_trade(
                decision,
                question=snapshot.question,
                condition_id=snapshot.condition_id,
            )
            if position:
                trades_executed += 1
                self.bot_logger.log(
                    "trade_executed",
                    **position.to_dict(),
                    priority_score=round(result.score, 6),
                    time_factor=round(result.time_factor, 4),
                    quality_factor=round(result.quality_factor, 4),
                    days_until_close=(
                        round(result.days_until_close, 2)
                        if result.days_until_close is not None
                        else None
                    ),
                )

        # 3. Check exits
        self._check_exits()

        # 4. Update marks
        if self.exec_engine.positions:
            self.exec_engine.update_marks()

        # 5. Log portfolio snapshot
        summary = self.portfolio.get_summary()
        self.bot_logger.log(
            "portfolio_snapshot",
            cycle=self._cycle_count,
            scored_candidates=len(scored),
            top_score=round(scored[0][0].score, 6) if scored else 0.0,
            **summary,
        )

    # ── Exit logic (mirrors TradingBot._check_exits) ────────────────────

    def _check_exits(self) -> int:
        exit_count = 0

        for position in list(self.exec_engine.get_open_positions()):
            market = self.data_service.get_market_by_id(position.market_id)
            if market is None:
                continue

            snapshot = self.data_service.build_snapshot(market)
            if snapshot is None:
                continue

            # Check resolution
            if snapshot.outcome_prices:
                yes_price = snapshot.outcome_prices[0]
                if yes_price >= 0.99 or yes_price <= 0.01:
                    won = (
                        position.side == Side.BUY_YES and yes_price >= 0.99
                    ) or (
                        position.side == Side.BUY_NO and yes_price <= 0.01
                    )
                    reason = ExitReason.RESOLVED_WIN if won else ExitReason.RESOLVED_LOSS
                    if position.side == Side.BUY_YES:
                        exit_price = yes_price
                    else:
                        exit_price = 1.0 - yes_price
                    closed = self.exec_engine.close_position(position, exit_price, reason)
                    self.bot_logger.log("position_closed", **closed.to_dict())
                    exit_count += 1
                    continue

            # Strategy-specific exits
            for strategy in self.strategies:
                if strategy.name == position.strategy_name:
                    should_close, reason = strategy.should_close(position, snapshot)
                    if should_close and reason is not None:
                        if position.side == Side.BUY_YES:
                            exit_price = snapshot.price_yes or position.entry_price
                        else:
                            no_price = snapshot.price_no
                            if no_price is None and snapshot.price_yes is not None:
                                no_price = 1.0 - snapshot.price_yes
                            exit_price = no_price or position.entry_price
                        closed = self.exec_engine.close_position(
                            position, exit_price, reason
                        )
                        self.bot_logger.log("position_closed", **closed.to_dict())
                        exit_count += 1
                    break

        return exit_count

    # ── Finalize ────────────────────────────────────────────────────────

    def _finalize(self) -> None:
        """Close remaining positions, export logs."""
        # Close remaining at current mark
        for position in list(self.exec_engine.get_open_positions()):
            if position.current_price is not None:
                self.exec_engine.close_position(
                    position, position.current_price, ExitReason.SHUTDOWN
                )

        # Log session end
        summary = self.portfolio.get_summary()
        self.bot_logger.log(
            "session_end",
            final_portfolio=self.exec_engine.get_portfolio_value(),
            **summary,
        )

        # Export files
        config_dict = self.bot_config.model_dump(exclude={"private_key"})
        config_dict["mode"] = "backtest"
        config_dict["backtest_start"] = self.bt_config.start_date.isoformat()
        config_dict["backtest_end"] = self.bt_config.end_date.isoformat()
        config_dict["scan_interval_minutes"] = self.bt_config.scan_interval_minutes

        csv_path = self.bot_logger.export_trades_csv(self.exec_engine)
        summary_path = self.bot_logger.export_summary(self.exec_engine, config_dict)

        # Print final stats
        final_value = self.exec_engine.get_portfolio_value()
        initial = self.bt_config.initial_bankroll
        total_return = (final_value / initial - 1) * 100 if initial > 0 else 0
        closed = self.exec_engine.get_closed_trades()
        wins = sum(1 for t in closed if t.realized_pnl > 0)

        print(f"\n{'=' * 60}")
        print(f"  BACKTEST COMPLETE")
        print(f"  Period: {self.bt_config.start_date.date()} -> {self.bt_config.end_date.date()}")
        print(f"  Interval: {self.bt_config.scan_interval_minutes} min")
        print(f"  Cycles: {self._cycle_count}")
        print(f"  Trades: {len(closed)}")
        if closed:
            print(f"  Win rate: {wins}/{len(closed)} ({wins / len(closed) * 100:.0f}%)")
        else:
            print(f"  Win rate: N/A")
        print(f"  Realized P&L: ${summary['total_realized_pnl']:+,.2f}")
        print(f"  Final portfolio: ${final_value:,.2f}")
        print(f"  Return: {total_return:+.2f}%")
        print(f"\n  Session: {self.bot_logger.session_id}")
        print(f"  Log:     {self.bot_logger.log_path}")
        print(f"  CSV:     {csv_path}")
        print(f"  Summary: {summary_path}")
        print(f"{'=' * 60}\n")
