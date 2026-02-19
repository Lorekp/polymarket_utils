"""Discover sessions and load summary JSON + trades CSV + JSONL snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

LOGS_DIR = Path("logs")


@dataclass
class SessionData:
    session_id: str
    summary: dict
    trades_df: pd.DataFrame
    snapshots: list[dict] = field(default_factory=list)  # portfolio_snapshot entries
    display_label: str = ""


def discover_sessions(logs_dir: Path = LOGS_DIR) -> list[str]:
    """Return session IDs that have a .summary.json, newest first."""
    summaries = sorted(logs_dir.glob("session_*.summary.json"), reverse=True)
    ids: list[str] = []
    for p in summaries:
        stem = p.stem.replace(".summary", "")  # session_20260219_134555
        ids.append(stem.replace("session_", ""))
    return ids


def load_session(session_id: str, logs_dir: Path = LOGS_DIR) -> SessionData:
    """Load the summary dict, trades DataFrame, and JSONL snapshots."""
    summary_path = logs_dir / f"session_{session_id}.summary.json"
    csv_path = logs_dir / f"session_{session_id}.trades.csv"
    jsonl_path = logs_dir / f"session_{session_id}.jsonl"

    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    if csv_path.exists() and csv_path.stat().st_size > 0:
        trades_df = pd.read_csv(csv_path, parse_dates=["entry_time", "exit_time"])
        trades_df = trades_df.sort_values("exit_time").reset_index(drop=True)
    else:
        trades_df = pd.DataFrame()

    # Extract portfolio snapshots from JSONL for unrealized P&L graph
    snapshots: list[dict] = []
    if jsonl_path.exists():
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "portfolio_snapshot":
                    snapshots.append(entry)

    perf = summary.get("performance", {})
    n = perf.get("total_trades", 0)
    pnl = perf.get("total_realized_pnl", 0)
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    label = f"{session_id}  |  {n} trades  |  {pnl_str}"

    return SessionData(
        session_id=session_id,
        summary=summary,
        trades_df=trades_df,
        snapshots=snapshots,
        display_label=label,
    )
