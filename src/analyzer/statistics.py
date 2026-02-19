"""Pure-function performance statistics computed from trade data."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


def compute_statistics(trades_df: pd.DataFrame, summary: dict) -> dict:
    """Return {metric_name: (raw_value, formatted_string)} from the trades CSV."""
    perf = summary.get("performance", {})
    initial_bankroll = perf.get("initial_bankroll", 10000.0)

    if trades_df.empty:
        return _empty_stats()

    pnl = trades_df["realized_pnl"]
    n = len(pnl)

    net_pnl = pnl.sum()
    wins = int((pnl > 0).sum())
    losses = n - wins
    win_rate = wins / n

    w = pnl[pnl > 0]
    l = pnl[pnl <= 0]

    avg_win = w.mean() if len(w) else 0.0
    avg_loss = l.mean() if len(l) else 0.0

    gross_profit = w.sum() if len(w) else 0.0
    gross_loss = abs(l.sum()) if len(l) else 0.0
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf") if gross_profit > 0 else 0.0
    )

    # Drawdown
    cum = pnl.cumsum()
    peak = cum.cummax()
    dd = peak - cum
    max_dd = dd.max()
    max_dd_pct = (max_dd / initial_bankroll) * 100 if initial_bankroll else 0.0

    # Sharpe (per-trade, not annualised)
    std = pnl.std(ddof=1) if n > 1 else 0.0
    sharpe = pnl.mean() / std if std > 0 else 0.0

    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    largest_win = w.max() if len(w) else 0.0
    largest_loss = l.min() if len(l) else 0.0

    avg_hold = trades_df["hold_hours"].mean() if "hold_hours" in trades_df.columns else 0.0
    median_hold = trades_df["hold_hours"].median() if "hold_hours" in trades_df.columns else 0.0

    total_return = perf.get(
        "total_return_pct",
        (net_pnl / initial_bankroll) * 100 if initial_bankroll else 0.0,
    )

    # ── Additional strategy-analysis metrics ───────────────────────────

    # Sortino ratio (downside deviation only)
    neg_pnl = pnl[pnl < 0]
    downside_std = neg_pnl.std(ddof=1) if len(neg_pnl) > 1 else 0.0
    sortino = pnl.mean() / downside_std if downside_std > 0 else 0.0

    # Win/loss streak
    is_win = (pnl > 0).astype(int)
    streaks = is_win.groupby((is_win != is_win.shift()).cumsum())
    max_win_streak = 0
    max_loss_streak = 0
    for _, grp in streaks:
        length = len(grp)
        if grp.iloc[0] == 1:
            max_win_streak = max(max_win_streak, length)
        else:
            max_loss_streak = max(max_loss_streak, length)

    # Payoff ratio (avg win / abs avg loss)
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf") if avg_win > 0 else 0.0

    # Recovery factor (net P&L / max drawdown)
    recovery = net_pnl / max_dd if max_dd > 0 else float("inf") if net_pnl > 0 else 0.0

    # Kelly criterion: f* = (win_rate * payoff - (1 - win_rate)) / payoff
    kelly = 0.0
    if payoff > 0 and payoff != float("inf"):
        kelly = (win_rate * payoff - (1 - win_rate)) / payoff

    # Exit reason breakdown
    exit_counts = {}
    if "exit_reason" in trades_df.columns:
        exit_counts = trades_df["exit_reason"].value_counts().to_dict()
    exit_str = ", ".join(f"{k}: {v}" for k, v in exit_counts.items())

    # Volatility of returns
    pnl_vol = std

    return {
        "Net P&L": (net_pnl, f"${net_pnl:+,.2f}"),
        "Total Return": (total_return, f"{total_return:+.2f}%"),
        "Total Trades": (n, str(n)),
        "Wins / Losses": (wins, f"{wins} / {losses}"),
        "Win Rate": (win_rate, f"{win_rate * 100:.1f}%"),
        "Profit Factor": (
            profit_factor,
            f"{profit_factor:.2f}" if profit_factor != float("inf") else "\u221e",
        ),
        "Sharpe Ratio": (sharpe, f"{sharpe:.3f}"),
        "Sortino Ratio": (sortino, f"{sortino:.3f}"),
        "Expectancy": (expectancy, f"${expectancy:+.3f}"),
        "Max Drawdown": (max_dd, f"${max_dd:,.2f} ({max_dd_pct:.2f}%)"),
        "Recovery Factor": (
            recovery,
            f"{recovery:.2f}" if recovery != float("inf") else "\u221e",
        ),
        "Avg Win": (avg_win, f"${avg_win:+.2f}"),
        "Avg Loss": (avg_loss, f"${avg_loss:.2f}"),
        "Payoff Ratio": (
            payoff,
            f"{payoff:.2f}" if payoff != float("inf") else "\u221e",
        ),
        "Largest Win": (largest_win, f"${largest_win:+.2f}"),
        "Largest Loss": (largest_loss, f"${largest_loss:.2f}"),
        "Kelly Criterion": (kelly, f"{kelly * 100:.1f}%"),
        "P&L Volatility": (pnl_vol, f"${pnl_vol:.3f}"),
        "Win Streak": (max_win_streak, str(max_win_streak)),
        "Loss Streak": (max_loss_streak, str(max_loss_streak)),
        "Avg Hold (hours)": (avg_hold, f"{avg_hold:.1f}h"),
        "Med Hold (hours)": (median_hold, f"{median_hold:.1f}h"),
        "Exit Reasons": (0, exit_str if exit_str else "--"),
    }


_ALL_STAT_NAMES = [
    "Net P&L", "Total Return", "Total Trades", "Wins / Losses",
    "Win Rate", "Profit Factor", "Sharpe Ratio", "Sortino Ratio",
    "Expectancy", "Max Drawdown", "Recovery Factor",
    "Avg Win", "Avg Loss", "Payoff Ratio",
    "Largest Win", "Largest Loss", "Kelly Criterion", "P&L Volatility",
    "Win Streak", "Loss Streak", "Avg Hold (hours)", "Med Hold (hours)",
    "Exit Reasons",
]


def _empty_stats() -> dict:
    return {k: (0, "--") for k in _ALL_STAT_NAMES}


def build_equity_curve(
    trades_df: pd.DataFrame,
    initial_bankroll: float = 10000.0,
) -> tuple[list[float], list[float]]:
    """Return (epoch_timestamps, realized_pnl_values) for the realized P&L chart.

    Y-axis is cumulative realized P&L (not portfolio value).
    Trades must already be sorted by exit_time.
    """
    if trades_df.empty:
        return [], []

    cum = trades_df["realized_pnl"].cumsum()

    times = trades_df["exit_time"].apply(lambda dt: dt.timestamp()).tolist()
    values = cum.tolist()

    # Prepend zero at first trade's entry time
    first_entry = trades_df["entry_time"].iloc[0]
    times.insert(0, first_entry.timestamp())
    values.insert(0, 0.0)

    return times, values


def build_unrealized_curve(
    snapshots: list[dict],
) -> tuple[list[float], list[float]]:
    """Return (epoch_timestamps, unrealized_pnl_values) from JSONL portfolio snapshots."""
    if not snapshots:
        return [], []

    times: list[float] = []
    values: list[float] = []

    for snap in snapshots:
        ts_str = snap.get("timestamp", "")
        upnl = snap.get("total_unrealized_pnl", 0.0)
        try:
            dt = datetime.fromisoformat(ts_str)
            times.append(dt.timestamp())
            values.append(upnl)
        except (ValueError, TypeError):
            continue

    return times, values


def compute_strategy_breakdown(trades_df: pd.DataFrame) -> dict[str, dict]:
    """Per-strategy summary table data."""
    if trades_df.empty:
        return {}

    out: dict[str, dict] = {}
    for strat, grp in trades_df.groupby("strategy"):
        pnl = grp["realized_pnl"]
        w_trades = pnl[pnl > 0]
        l_trades = pnl[pnl <= 0]
        w = int(len(w_trades))
        n = len(grp)
        gp = w_trades.sum() if len(w_trades) else 0.0
        gl = abs(l_trades.sum()) if len(l_trades) else 0.0
        pf = gp / gl if gl > 0 else float("inf") if gp > 0 else 0.0
        out[strat] = {
            "trades": n,
            "wins": w,
            "win_rate": w / n if n else 0.0,
            "total_pnl": pnl.sum(),
            "avg_pnl": pnl.mean(),
            "profit_factor": pf,
        }
    return out
