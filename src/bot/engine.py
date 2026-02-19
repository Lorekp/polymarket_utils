"""Main trading bot orchestrator - scan loop, trade, and graceful shutdown."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

from src.bot.config import BotConfig, load_config
from src.bot.execution.live import LiveEngine
from src.bot.execution.simulator import SimulationEngine
from src.bot.logger import BotLogger
from src.bot.market_data import MarketDataService
from src.bot.models import ExitReason, Position, Side
from src.bot.portfolio import Portfolio
from src.bot.scanner import MarketScanner
from src.bot.strategies.base import Strategy
from src.bot.strategies.breakout import BreakoutFollower
from src.bot.strategies.category import CategoryBias
from src.bot.strategies.composite import CompositeStrategy
from src.bot.strategies.late_stage import LateStageMomentum
from src.bot.strategies.new_market import NewMarketBuyer

logger = logging.getLogger(__name__)


# ── ANSI helpers ──────────────────────────────────────────────────────────────
_IS_TTY = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
# Check for Windows Terminal / ConPTY support (WT_SESSION is set by Windows Terminal)
_ANSI_OK = _IS_TTY or os.environ.get("WT_SESSION") or os.environ.get("TERM")

# Detect if stdout can handle Unicode (UTF-8) or is stuck on a legacy codepage
try:
    _UNICODE_OK = sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") in (
        "utf8", "utf16", "utf32", "utf_8", "utf_16", "utf_32",
    )
except Exception:
    _UNICODE_OK = False


def _c(code: str, text: str) -> str:
    """Wrap *text* in an ANSI color code if stdout supports it."""
    return f"\033[{code}m{text}\033[0m" if _ANSI_OK else text

def _dim(t: str) -> str: return _c("2", t)
def _bold(t: str) -> str: return _c("1", t)
def _green(t: str) -> str: return _c("32", t)
def _red(t: str) -> str: return _c("31", t)
def _yellow(t: str) -> str: return _c("33", t)
def _cyan(t: str) -> str: return _c("36", t)
def _white(t: str) -> str: return _c("97", t)
def _pnl_color(v: float) -> str:
    s = f"${v:+,.2f}"
    if v > 0:   return _green(s)
    elif v < 0: return _red(s)
    return _dim(s)
def _pct_color(v: float) -> str:
    s = f"{v:+.2f}%"
    if v > 0:   return _green(s)
    elif v < 0: return _red(s)
    return _dim(s)


# Use ASCII-safe characters when Unicode isn't available
_CHR_BAR_FULL = "#" if not _UNICODE_OK else "\u2588"   # █
_CHR_BAR_EMPTY = "." if not _UNICODE_OK else "\u2591"   # ░
_CHR_SEP = "|" if not _UNICODE_OK else "|"
_CHR_UP = "^" if not _UNICODE_OK else "\u25b2"          # ▲
_CHR_DOWN = "v" if not _UNICODE_OK else "\u25bc"         # ▼
_CHR_DASH = "-" if not _UNICODE_OK else "\u2014"         # —


def _safe(text: str) -> str:
    """Strip characters that can't be encoded by stdout."""
    if _UNICODE_OK:
        return text
    try:
        text.encode(sys.stdout.encoding or "ascii")
        return text
    except (UnicodeEncodeError, LookupError):
        return text.encode(sys.stdout.encoding or "ascii", errors="replace").decode(
            sys.stdout.encoding or "ascii"
        )


def _bar(value: float, width: int = 20) -> str:
    """Tiny horizontal bar for exposure, clamped 0-1."""
    filled = int(max(0, min(1, value)) * width)
    return _cyan(_CHR_BAR_FULL * filled) + _dim(_CHR_BAR_EMPTY * (width - filled))


def _terminal_width() -> int:
    try:
        return shutil.get_terminal_size((80, 24)).columns
    except Exception:
        return 80


class _LiveDisplay:
    """Renders a compact, in-place dashboard + scrolling activity feed."""

    FEED_MAX = 12  # keep last N activity lines

    def __init__(self):
        self.feed: deque[str] = deque(maxlen=self.FEED_MAX)
        self._dashboard_lines = 0  # how many lines the last dashboard print used

    # ── activity feed (append-only, scrolls) ─────────────────────────────
    def activity(self, icon: str, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.feed.append(f"  {_dim(ts)}  {icon} {msg}")

    def trade_opened(self, pos: Position) -> None:
        side_s = _green("YES") if pos.side == Side.BUY_YES else _red("NO")
        q = pos.question[:50] + (".." if len(pos.question) > 50 else "")
        self.activity(
            "+",
            f"{_bold(pos.position_id)} {side_s} {_white(f'${pos.size_usd:.0f}')} @ "
            f"{pos.entry_price:.3f}  {_dim(q)}  [{pos.strategy_name}]",
        )

    def trade_closed(self, position_id: str, reason: ExitReason, pnl: float,
                     question: str, strategy: str) -> None:
        icon = {
            ExitReason.RESOLVED_WIN: _green("W"),
            ExitReason.RESOLVED_LOSS: _red("L"),
            ExitReason.TAKE_PROFIT: _green("$"),
            ExitReason.STOP_LOSS: _red("X"),
            ExitReason.HOLD_EXPIRED: _yellow("T"),
            ExitReason.SHUTDOWN: _dim("-"),
        }.get(reason, _dim("x"))
        q = question[:40] + (".." if len(question) > 40 else "")
        self.activity(
            icon,
            f"{_bold(position_id)} closed {_dim(str(reason))} "
            f"{_pnl_color(pnl)}  {_dim(q)}  [{strategy}]",
        )

    def scan_done(self, candidates: int, signals: int, trades: int, exits: int) -> None:
        parts = [f"{candidates} mkts"]
        if signals: parts.append(f"{signals} sigs")
        if trades:  parts.append(f"{_green(f'+{trades}')} opened")
        if exits:   parts.append(f"{_yellow(f'-{exits}')} closed")
        self.activity("~", " | ".join(parts))

    def error(self, msg: str) -> None:
        self.activity("!", _red(msg[:80]))

    # ── dashboard (redrawn in-place) ─────────────────────────────────────
    def render_dashboard(
        self,
        cycle: int,
        mode: str,
        portfolio_value: float,
        initial_bankroll: float,
        cash: float,
        open_positions: int,
        total_exposure: float,
        max_exposure_pct: float,
        realized_pnl: float,
        unrealized_pnl: float,
        total_trades: int,
        win_rate: float,
        next_scan_sec: int,
        strategy_counts: dict[str, int],
        top_positions: list[Position],
    ) -> None:
        w = min(_terminal_width(), 100)
        sep = _dim("-" * w)
        total_return = (portfolio_value / initial_bankroll - 1) * 100 if initial_bankroll > 0 else 0
        total_pnl = realized_pnl + unrealized_pnl
        exposure_pct = total_exposure / portfolio_value if portfolio_value > 0 else 0

        lines: list[str] = []
        lines.append("")

        # ── header ──
        now_s = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        header = f"  {_bold('POLYMARKET BOT')}  {_dim('|')}  {mode.upper()}  {_dim('|')}  cycle {cycle}  {_dim('|')}  {_dim(now_s)}"
        lines.append(header)
        lines.append(sep)

        # ── row 1: portfolio / return / cash ──
        lines.append(
            f"  Portfolio {_bold(_white(f'${portfolio_value:,.2f}'))}  "
            f"{_pct_color(total_return)}  {_dim('|')}  "
            f"Cash {_white(f'${cash:,.2f}')}"
        )

        # ── row 2: P&L breakdown ──
        lines.append(
            f"  P&L  total {_pnl_color(total_pnl)}  "
            f"{_dim('(')}realized {_pnl_color(realized_pnl)}"
            f"  unrealized {_pnl_color(unrealized_pnl)}{_dim(')')}"
        )

        # ── row 3: positions / exposure bar / trades / win rate ──
        lines.append(
            f"  Positions {_bold(str(open_positions))}  "
            f"Exposure {_bar(exposure_pct, 15)} {exposure_pct*100:.0f}%/{max_exposure_pct*100:.0f}%  {_dim('|')}  "
            f"Trades {total_trades}  "
            f"Win {_green(f'{win_rate*100:.0f}%') if win_rate > 0.5 else _yellow(f'{win_rate*100:.0f}%') if total_trades > 0 else _dim('N/A')}"
        )

        # ── row 4: strategy spread ──
        if strategy_counts:
            strats = "  ".join(f"{_dim(k)}:{v}" for k, v in sorted(strategy_counts.items()))
            lines.append(f"  By strategy  {strats}")

        lines.append(sep)

        # ── top positions (up to 5) ──
        if top_positions:
            lines.append(_dim("  Top positions by |unrealized P&L|:"))
            for p in top_positions[:5]:
                side_s = _green("Y") if p.side == Side.BUY_YES else _red("N")
                q = p.question[:42] + (".." if len(p.question) > 42 else "")
                move = ""
                if p.current_price is not None:
                    diff = p.current_price - p.entry_price
                    if diff > 0:
                        move = _green(f"{_CHR_UP}{diff:+.3f}")
                    elif diff < 0:
                        move = _red(f"{_CHR_DOWN}{diff:+.3f}")
                    else:
                        move = _dim(_CHR_DASH)
                lines.append(
                    f"    {p.position_id} {side_s} {_pnl_color(p.unrealized_pnl):>10}  "
                    f"{move:>8}  {_dim(q)}"
                )
            lines.append(sep)

        # ── activity feed ──
        if self.feed:
            lines.append(_dim("  Recent activity:"))
            for entry in self.feed:
                lines.append(entry)
            lines.append("")

        # ── countdown ──
        lines.append(f"  {_dim(f'Next scan in {next_scan_sec}s  |  Ctrl+C to stop')}")
        lines.append("")

        # Clear previous dashboard and print new one
        self._clear_previous()
        output = "\n".join(lines)
        sys.stdout.write(output + "\n")
        sys.stdout.flush()
        self._dashboard_lines = output.count("\n") + 1

    def _clear_previous(self) -> None:
        """Move cursor up and clear lines from the previous dashboard render."""
        if self._dashboard_lines > 0 and _ANSI_OK:
            # Move up N lines and clear each
            sys.stdout.write(f"\033[{self._dashboard_lines}A")
            for _ in range(self._dashboard_lines):
                sys.stdout.write("\033[2K\n")
            sys.stdout.write(f"\033[{self._dashboard_lines}A")
            sys.stdout.flush()

    def print_static(self, text: str) -> None:
        """Print a line that won't be overwritten (used for startup/shutdown)."""
        self._dashboard_lines = 0  # reset so next render doesn't clobber this
        print(text)


class TradingBot:
    """Main bot loop - ties scanner, strategies, execution, and logging together."""

    def __init__(self, config_path: str = "config.json"):
        self.config = load_config(config_path)
        self.data_service = MarketDataService()
        self.scanner = MarketScanner(self.data_service, self.config)
        self.bot_logger = BotLogger(log_dir="logs")

        # Build strategies
        self.strategies = self._build_strategies()
        self.composite = CompositeStrategy(self.strategies, self.config)

        # Build execution engine
        if self.config.mode == "simulation":
            self.engine = SimulationEngine(self.config.simulation_bankroll, data_service=self.data_service)
        else:
            self.engine = LiveEngine(self.config.private_key, self.config.funder_address)

        self.portfolio = Portfolio(self.engine, self.config.position_sizing)
        self.display = _LiveDisplay()

        self._running = False
        self._shutdown_event = threading.Event()
        self._mark_stop = threading.Event()
        self._cycle_count = 0

    def _build_strategies(self) -> list[Strategy]:
        strategies: list[Strategy] = []
        sc = self.config.strategies

        if sc.new_market.enabled:
            strategies.append(NewMarketBuyer(sc.new_market))
        if sc.breakout.enabled:
            strategies.append(BreakoutFollower(sc.breakout))
        if sc.category.enabled:
            strategies.append(CategoryBias(sc.category))
        if sc.late_stage.enabled:
            strategies.append(LateStageMomentum(sc.late_stage))

        return strategies

    def run(self) -> None:
        """Main bot loop. Blocks until Ctrl+C."""
        self._running = True

        # Log session start
        self.bot_logger.log(
            "session_start",
            config=self.config.model_dump(),
            bankroll=self.config.simulation_bankroll,
            mode=self.config.mode,
            strategies=[s.name for s in self.strategies],
        )

        # Register signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        strats = ", ".join(s.name for s in self.strategies)
        self.display.print_static(f"\n{'='*60}")
        self.display.print_static(f"  {_bold('Polymarket Trading Bot')} - {self.config.mode.upper()}")
        self.display.print_static(
            f"  Bankroll ${self.config.simulation_bankroll:,.0f} | "
            f"Scan every {self.config.scan_interval_seconds}s | "
            f"Strategies: {strats}"
        )
        self.display.print_static(f"  Log: {self.bot_logger.log_path}")
        self.display.print_static(f"{'='*60}")

        try:
            while self._running:
                self._cycle_count += 1
                self._run_cycle()

                # Countdown between scans: dashboard redraws every 1s,
                # background thread refreshes position marks every 5s
                remaining = self.config.scan_interval_seconds
                self._mark_stop.clear()
                mark_thread = None
                if isinstance(self.engine, SimulationEngine) and self.engine.positions:
                    mark_thread = threading.Thread(
                        target=self._background_mark_loop, daemon=True,
                    )
                    mark_thread.start()
                while remaining > 0 and self._running:
                    self._render_dashboard(next_scan_sec=remaining)
                    if self._shutdown_event.wait(timeout=1):
                        break
                    remaining -= 1
                self._mark_stop.set()
                if mark_thread is not None:
                    mark_thread.join(timeout=2)
        finally:
            self._graceful_shutdown()

    def _render_dashboard(self, next_scan_sec: int = 0) -> None:
        """Redraw the live dashboard in-place."""
        summary = self.portfolio.get_summary()
        positions = self.engine.get_open_positions()

        # Strategy breakdown for open positions
        strat_counts: dict[str, int] = {}
        for p in positions:
            strat_counts[p.strategy_name] = strat_counts.get(p.strategy_name, 0) + 1

        # Top positions sorted by |unrealized_pnl|
        top = sorted(positions, key=lambda p: abs(p.unrealized_pnl), reverse=True)

        self.display.render_dashboard(
            cycle=self._cycle_count,
            mode=self.config.mode,
            portfolio_value=summary["portfolio_value"],
            initial_bankroll=self.config.simulation_bankroll,
            cash=summary["cash"],
            open_positions=summary["open_positions"],
            total_exposure=summary["total_exposure"],
            max_exposure_pct=self.config.position_sizing.max_total_exposure_pct,
            realized_pnl=summary["total_realized_pnl"],
            unrealized_pnl=summary["total_unrealized_pnl"],
            total_trades=summary["total_trades"],
            win_rate=summary["win_rate"],
            next_scan_sec=next_scan_sec,
            strategy_counts=strat_counts,
            top_positions=top,
        )

    def _background_mark_loop(self) -> None:
        """Refresh position marks every 5s in a background thread."""
        while not self._mark_stop.wait(timeout=5):
            try:
                if isinstance(self.engine, SimulationEngine) and self.engine.positions:
                    self.engine.update_marks_batch(self.data_service)
            except Exception as e:
                logger.debug("Background mark refresh failed: %s", e)

    def _run_cycle(self) -> None:
        """Execute one scan-evaluate-trade cycle."""
        # 1. Scan for candidate markets
        try:
            candidates = self.scanner.scan()
        except Exception as e:
            logger.error("Scanner error: %s", e)
            self.bot_logger.log("error", source="scanner", message=str(e))
            self.display.error(f"Scanner: {e}")
            return

        self.bot_logger.log(
            "scan_result",
            cycle=self._cycle_count,
            candidates=len(candidates),
        )

        # 2. Evaluate and trade
        signals_generated = 0
        trades_executed = 0

        for snapshot in candidates:
            # Skip if already have a position in this market
            if self.portfolio.has_position_in_market(snapshot.market_id):
                continue

            # Evaluate through composite strategy
            decision = self.composite.evaluate(snapshot)
            if decision is None:
                continue

            signals_generated += 1
            self.bot_logger.log("signal", **decision.signal.to_dict())

            # Risk check
            if not self.portfolio.can_open_position(decision.size_usd):
                self.bot_logger.log(
                    "trade_rejected",
                    reason="risk_limit",
                    market_id=snapshot.market_id,
                    size_usd=decision.size_usd,
                )
                continue

            # Execute
            position = self.engine.execute_trade(
                decision,
                question=snapshot.question,
                condition_id=snapshot.condition_id,
            )
            if position:
                trades_executed += 1
                self.bot_logger.log("trade_executed", **position.to_dict())
                self.display.trade_opened(position)

        # 3. Check exits on open positions
        exits = self._check_exits()

        # 4. Update marks and log portfolio
        if isinstance(self.engine, SimulationEngine) and self.engine.positions:
            self.engine.update_marks(self.data_service)

        summary = self.portfolio.get_summary()
        self.bot_logger.log("portfolio_snapshot", cycle=self._cycle_count, **summary)

        # Add scan summary to activity feed
        self.display.scan_done(len(candidates), signals_generated, trades_executed, exits)

        # Render dashboard immediately after cycle
        self._render_dashboard(next_scan_sec=self.config.scan_interval_seconds)

    def _check_exits(self) -> int:
        """Check all open positions for exit conditions. Returns count of exits."""
        exit_count = 0

        for position in list(self.engine.get_open_positions()):
            # Refresh market data
            market = self.data_service.get_market_by_id(position.market_id)
            if market is None:
                continue

            snapshot = self.data_service.build_snapshot(market)
            if snapshot is None:
                continue

            # Check for resolution first
            if snapshot.outcome_prices:
                yes_price = snapshot.outcome_prices[0]
                if yes_price >= 0.99 or yes_price <= 0.01:
                    won = (
                        position.side == Side.BUY_YES and yes_price >= 0.99
                    ) or (
                        position.side == Side.BUY_NO and yes_price <= 0.01
                    )
                    reason = ExitReason.RESOLVED_WIN if won else ExitReason.RESOLVED_LOSS
                    # Pass price of the token we hold
                    if position.side == Side.BUY_YES:
                        exit_price = yes_price
                    else:
                        exit_price = 1.0 - yes_price
                    closed = self.engine.close_position(position, exit_price, reason)
                    self.bot_logger.log("position_closed", **closed.to_dict())
                    self.display.trade_closed(
                        closed.position_id, reason, closed.realized_pnl,
                        closed.question, closed.strategy_name,
                    )
                    exit_count += 1
                    continue

            # Check strategy-specific exit conditions
            for strategy in self.strategies:
                if strategy.name == position.strategy_name:
                    should_close, reason = strategy.should_close(position, snapshot)
                    if should_close and reason is not None:
                        # Pass price of the token we hold
                        if position.side == Side.BUY_YES:
                            exit_price = snapshot.price_yes or position.entry_price
                        else:
                            exit_price = snapshot.price_no or position.entry_price
                        closed = self.engine.close_position(position, exit_price, reason)
                        self.bot_logger.log("position_closed", **closed.to_dict())
                        self.display.trade_closed(
                            closed.position_id, reason, closed.realized_pnl,
                            closed.question, closed.strategy_name,
                        )
                        exit_count += 1
                    break

        return exit_count

    def _handle_shutdown(self, signum, frame) -> None:
        """Signal handler for graceful shutdown."""
        print("\n\nShutdown signal received. Finishing current cycle...")
        self._running = False
        self._shutdown_event.set()

    def _graceful_shutdown(self) -> None:
        """Export all logs and print final summary."""
        # Reset display so shutdown output isn't overwritten
        self.display._dashboard_lines = 0
        p = self.display.print_static

        p(f"\n{'='*60}")
        p(f"  {_bold('Shutting down')} - exporting session data...")

        # Close remaining positions at current market prices (mark them for the log)
        for position in list(self.engine.get_open_positions()):
            if position.current_price is not None:
                self.engine.close_position(
                    position, position.current_price, ExitReason.SHUTDOWN
                )

        # Log session end
        summary = self.portfolio.get_summary()
        self.bot_logger.log(
            "session_end",
            final_portfolio=self.engine.get_portfolio_value(),
            **summary,
        )

        # Export files
        config_dict = self.config.model_dump()
        csv_path = self.bot_logger.export_trades_csv(self.engine)
        summary_path = self.bot_logger.export_summary(self.engine, config_dict)

        # Close connections
        self.data_service.close()

        # Print final summary
        final_value = self.engine.get_portfolio_value()
        initial = self.config.simulation_bankroll
        total_return = (final_value / initial - 1) * 100 if initial > 0 else 0

        closed = self.engine.get_closed_trades()
        wins = sum(1 for t in closed if t.realized_pnl > 0)

        p("")
        p(f"  {_bold('SESSION SUMMARY')}")
        p(f"  {'Cycles':.<24} {self._cycle_count}")
        p(f"  {'Trades':.<24} {len(closed)}")
        if closed:
            p(f"  {'Win rate':.<24} {wins}/{len(closed)} ({wins/len(closed)*100:.0f}%)")
        else:
            p(f"  {'Win rate':.<24} N/A")
        p(f"  {'Realized P&L':.<24} {_pnl_color(summary['total_realized_pnl'])}")
        p(f"  {'Final portfolio':.<24} {_white(f'${final_value:,.2f}')}")
        p(f"  {'Return':.<24} {_pct_color(total_return)}")
        p("")
        p(f"  {_dim('Files:')}")
        p(f"    {_dim('Log')}    {self.bot_logger.log_path}")
        p(f"    {_dim('CSV')}    {csv_path}")
        p(f"    {_dim('Summary')} {summary_path}")
        p(f"{'='*60}\n")
