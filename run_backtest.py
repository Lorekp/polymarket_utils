"""CLI entry point for running Polymarket backtests against historical parquet data."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from src.bot.backtest.runner import BacktestConfig, BacktestRunner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a Polymarket trading backtest against historical parquet data.",
    )
    parser.add_argument(
        "--markets", required=True, help="Path to markets parquet file (e.g. data/markets_2025_closed.parquet)",
    )
    parser.add_argument(
        "--prices", required=True, help="Path to price history parquet file",
    )
    parser.add_argument(
        "--start", required=True, help="Backtest start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end", required=True, help="Backtest end date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--bankroll", type=float, default=10_000.0, help="Starting bankroll (default: $10,000)",
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Scan interval in minutes (5/15/30/60, default: 60)",
    )
    parser.add_argument(
        "--config", default="config.json", help="Bot config path (default: config.json)",
    )
    args = parser.parse_args()

    # Parse dates
    try:
        start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc,
        )
    except ValueError as e:
        print(f"Invalid date format: {e}", file=sys.stderr)
        sys.exit(1)

    if end <= start:
        print("End date must be after start date.", file=sys.stderr)
        sys.exit(1)

    config = BacktestConfig(
        markets_parquet=args.markets,
        price_history_parquet=args.prices,
        start_date=start,
        end_date=end,
        scan_interval_minutes=args.interval,
        initial_bankroll=args.bankroll,
        bot_config_path=args.config,
    )

    runner = BacktestRunner(config)

    def progress(step: int, total: int, summary: dict) -> None:
        pct = step / total * 100
        pv = summary["portfolio_value"]
        trades = summary["total_trades"]
        sim = summary.get("sim_time", "")[:10]
        bar_width = 30
        filled = int(pct / 100 * bar_width)
        bar = "#" * filled + "." * (bar_width - filled)
        sys.stdout.write(
            f"\r  [{bar}] {pct:5.1f}%  |  {sim}  |  ${pv:,.2f}  |  {trades} trades"
        )
        sys.stdout.flush()

    print(f"\nPolymarket Backtest")
    print(f"  Markets:  {args.markets}")
    print(f"  Prices:   {args.prices}")
    print(f"  Period:   {args.start} -> {args.end}")
    print(f"  Interval: {args.interval} min")
    print(f"  Bankroll: ${args.bankroll:,.0f}")
    print(f"  Config:   {args.config}")
    print()

    session_id = runner.run(progress_callback=progress)
    # Clear progress line
    sys.stdout.write("\r" + " " * 100 + "\r")
    sys.stdout.flush()

    print(f"Run the analyzer to view results: python run_analyzer.py")


if __name__ == "__main__":
    main()
