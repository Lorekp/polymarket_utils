"""Structured trade logger — JSONL (crash-safe) + CSV + summary export."""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.bot.execution.base import ExecutionEngine

logger = logging.getLogger(__name__)


class BotLogger:
    """Logs every bot event to NDJSON. Exports CSV + summary on shutdown."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True, parents=True)

        session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.session_id = session_id
        self.log_path = self.log_dir / f"session_{session_id}.jsonl"
        self.start_time = datetime.now(timezone.utc)
        self._entry_count = 0

    def log(self, entry_type: str, **data) -> None:
        """Append a log entry (written immediately for crash safety)."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": entry_type,
            **data,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        self._entry_count += 1

    def export_trades_csv(self, engine: ExecutionEngine) -> Path:
        """Export all closed trades to CSV."""
        csv_path = self.log_path.with_suffix(".trades.csv")
        closed = engine.get_closed_trades()

        if not closed:
            # Write empty CSV with headers
            fieldnames = [
                "position_id", "market_id", "question", "side",
                "entry_price", "exit_price", "size_usd", "size_shares",
                "realized_pnl", "strategy", "exit_reason",
                "entry_time", "exit_time", "hold_hours",
            ]
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            return csv_path

        rows = [t.to_dict() for t in closed]
        fieldnames = list(rows[0].keys())

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return csv_path

    def export_summary(self, engine: ExecutionEngine, config_dict: dict) -> Path:
        """Export session summary as JSON."""
        summary_path = self.log_path.with_suffix(".summary.json")
        now = datetime.now(timezone.utc)
        runtime_hours = (now - self.start_time).total_seconds() / 3600

        closed = engine.get_closed_trades()
        positions = engine.get_open_positions()

        total_realized = sum(t.realized_pnl for t in closed)
        total_unrealized = sum(p.unrealized_pnl for p in positions)
        wins = sum(1 for t in closed if t.realized_pnl > 0)
        losses = sum(1 for t in closed if t.realized_pnl <= 0)

        # Per-strategy breakdown
        strategy_stats: dict[str, dict] = {}
        for trade in closed:
            s = trade.strategy_name
            if s not in strategy_stats:
                strategy_stats[s] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            strategy_stats[s]["trades"] += 1
            strategy_stats[s]["total_pnl"] += trade.realized_pnl
            if trade.realized_pnl > 0:
                strategy_stats[s]["wins"] += 1

        for s in strategy_stats.values():
            s["win_rate"] = round(s["wins"] / s["trades"], 3) if s["trades"] > 0 else 0.0
            s["avg_pnl"] = round(s["total_pnl"] / s["trades"], 2) if s["trades"] > 0 else 0.0
            s["total_pnl"] = round(s["total_pnl"], 2)

        # Max drawdown
        running_pnl = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for trade in closed:
            running_pnl += trade.realized_pnl
            peak = max(peak, running_pnl)
            drawdown = peak - running_pnl
            max_drawdown = max(max_drawdown, drawdown)

        summary = {
            "session_id": self.session_id,
            "start_time": self.start_time.isoformat(),
            "end_time": now.isoformat(),
            "runtime_hours": round(runtime_hours, 2),
            "config": config_dict,
            "performance": {
                "initial_bankroll": engine.initial_bankroll if hasattr(engine, "initial_bankroll") else None,
                "final_portfolio_value": round(engine.get_portfolio_value(), 2),
                "total_realized_pnl": round(total_realized, 2),
                "total_unrealized_pnl": round(total_unrealized, 2),
                "total_return_pct": round(
                    (engine.get_portfolio_value() / engine.initial_bankroll - 1) * 100, 2
                ) if hasattr(engine, "initial_bankroll") and engine.initial_bankroll > 0 else None,
                "total_trades": len(closed),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / len(closed), 3) if closed else 0.0,
                "avg_pnl_per_trade": round(total_realized / len(closed), 2) if closed else 0.0,
                "max_drawdown": round(max_drawdown, 2),
                "open_positions": len(positions),
            },
            "strategy_breakdown": strategy_stats,
            "log_entries": self._entry_count,
            "log_file": str(self.log_path),
        }

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)

        return summary_path
