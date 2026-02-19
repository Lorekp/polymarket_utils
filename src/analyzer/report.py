"""Generate a plain-text session report and write it to disk."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.analyzer.data_loader import SessionData
from src.analyzer.statistics import (
    compute_statistics,
    compute_strategy_breakdown,
)


def generate_report(session_data: SessionData, out_dir: Path = Path("logs")) -> Path:
    """Write a human-readable .txt report and return its path."""
    sid = session_data.session_id
    summary = session_data.summary
    perf = summary.get("performance", {})
    cfg = summary.get("config", {})

    stats = compute_statistics(session_data.trades_df, summary)
    breakdown = compute_strategy_breakdown(session_data.trades_df)

    lines: list[str] = []
    _a = lines.append

    _a("=" * 72)
    _a(f"  POLYMARKET SESSION REPORT — {sid}")
    _a("=" * 72)
    _a("")

    # ── Session info ───────────────────────────────────────────────────
    _a("SESSION")
    _a(f"  Session ID       : {sid}")
    _a(f"  Mode             : {cfg.get('mode', '?')}")
    _a(f"  Start            : {summary.get('start_time', '?')}")
    _a(f"  End              : {summary.get('end_time', '?')}")
    _a(f"  Runtime          : {summary.get('runtime_hours', 0):.2f} hours")
    _a(f"  Initial Bankroll : ${perf.get('initial_bankroll', 0):,.2f}")
    _a(f"  Final Value      : ${perf.get('final_portfolio_value', 0):,.2f}")
    strats = ", ".join(cfg.get("strategies", {}).keys())
    _a(f"  Strategies       : {strats}")
    _a("")

    # ── Performance ────────────────────────────────────────────────────
    _a("-" * 72)
    _a("PERFORMANCE")
    _a("")

    # Two-column layout
    keys = list(stats.keys())
    for i in range(0, len(keys), 2):
        left_name = keys[i]
        left_val = stats[left_name][1]
        left_str = f"  {left_name:20s} {left_val:>16s}"
        if i + 1 < len(keys):
            right_name = keys[i + 1]
            right_val = stats[right_name][1]
            right_str = f"  {right_name:20s} {right_val:>16s}"
        else:
            right_str = ""
        _a(f"{left_str}{right_str}")

    _a("")

    # ── Strategy breakdown ─────────────────────────────────────────────
    _a("-" * 72)
    _a("STRATEGY BREAKDOWN")
    _a("")

    if breakdown:
        header = f"  {'Strategy':<14s} {'Trades':>7s} {'Wins':>6s} {'Win%':>7s} {'Total P&L':>11s} {'Avg P&L':>9s} {'PF':>7s}"
        _a(header)
        _a("  " + "-" * len(header.strip()))
        for name, info in breakdown.items():
            pf = info["profit_factor"]
            pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
            _a(
                f"  {name:<14s} {info['trades']:>7d} {info['wins']:>6d} "
                f"{info['win_rate'] * 100:>6.1f}% "
                f"${info['total_pnl']:>+9.2f} "
                f"${info['avg_pnl']:>+7.2f} "
                f"{pf_str:>7s}"
            )
    else:
        _a("  No strategy data.")

    _a("")

    # ── Top trades ─────────────────────────────────────────────────────
    df = session_data.trades_df
    if not df.empty:
        _a("-" * 72)
        _a("TOP 10 TRADES BY P&L")
        _a("")
        top = df.nlargest(10, "realized_pnl")
        for _, row in top.iterrows():
            q = str(row.get("question", ""))[:45]
            _a(
                f"  ${row['realized_pnl']:>+8.2f}  "
                f"{row.get('strategy', ''):>10s}  "
                f"{row.get('side', ''):>8s}  "
                f"{q}"
            )

        _a("")
        _a("BOTTOM 10 TRADES BY P&L")
        _a("")
        bottom = df.nsmallest(10, "realized_pnl")
        for _, row in bottom.iterrows():
            q = str(row.get("question", ""))[:45]
            _a(
                f"  ${row['realized_pnl']:>+8.2f}  "
                f"{row.get('strategy', ''):>10s}  "
                f"{row.get('side', ''):>8s}  "
                f"{q}"
            )

        _a("")

    # ── Config snapshot ────────────────────────────────────────────────
    _a("-" * 72)
    _a("CONFIG SNAPSHOT")
    _a("")
    ps = cfg.get("position_sizing", {})
    _a(f"  Fixed Size USD       : ${ps.get('fixed_size_usd', '?')}")
    _a(f"  Max Position %       : {ps.get('max_position_pct', '?')}")
    _a(f"  Max Total Exposure % : {ps.get('max_total_exposure_pct', '?')}")
    _a(f"  Scan Interval        : {cfg.get('scan_interval_seconds', '?')}s")
    vf = cfg.get("volume_filter", {})
    _a(f"  Volume Filter        : ${vf.get('min_volume', '?'):,} – ${vf.get('max_volume', '?'):,}")
    _a("")

    _a("=" * 72)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _a(f"  Generated {now_str}")
    _a("=" * 72)
    _a("")

    report_text = "\n".join(lines)

    out_dir.mkdir(exist_ok=True, parents=True)
    report_path = out_dir / f"session_{sid}.report.txt"
    report_path.write_text(report_text, encoding="utf-8")

    return report_path
