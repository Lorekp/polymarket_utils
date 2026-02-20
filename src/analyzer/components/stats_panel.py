"""Collapsible statistics panel with strategy breakdown."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from src.analyzer.components.theme import (
    GREEN_PROFIT, RED_LOSS, TEXT_DIM, YELLOW_ACCENT,
)
from src.analyzer.data_loader import SessionData
from src.analyzer.statistics import (
    compute_statistics, compute_strategy_breakdown, compute_backtest_statistics,
)

# Layout: rows of 4 metrics each (label, value, label, value)
_STAT_ROWS = [
    ("Net P&L", "Total Return", "Sharpe Ratio", "Sortino Ratio"),
    ("Total Trades", "Win Rate", "Profit Factor", "Payoff Ratio"),
    ("Max Drawdown", "Recovery Factor", "Expectancy", "Kelly Criterion"),
    ("Avg Win", "Avg Loss", "Largest Win", "Largest Loss"),
    ("Wins / Losses", "Win Streak", "Loss Streak", "P&L Volatility"),
    ("Avg Hold (hours)", "Med Hold (hours)", None, None),
]

# Separate row for exit reasons (full-width)
_COLOR_METRICS = {
    "Net P&L", "Total Return", "Expectancy", "Recovery Factor",
}
_SHARPE_METRICS = {"Sharpe Ratio", "Sortino Ratio"}


_BACKTEST_ROWS = [
    ("Annualized Return", "CAGR", "Trade Frequency", "Capital Utilization"),
    ("Max DD Duration", None, None, None),
]

_BACKTEST_COLOR_METRICS = {"Annualized Return", "CAGR"}


class StatsPanel:
    def __init__(self) -> None:
        self._text_ids: dict[str, int | str] = {}
        self._strat_container: int | str | None = None
        self._exit_id: int | str | None = None
        self._bt_header: int | str | None = None
        self._bt_text_ids: dict[str, int | str] = {}

    def build(self, parent: int | str) -> None:
        with dpg.collapsing_header(
            label="  Performance Statistics",
            parent=parent,
            default_open=True,
        ):
            with dpg.table(
                header_row=False,
                resizable=False,
                borders_innerH=False,
                borders_outerH=False,
                borders_innerV=False,
                borders_outerV=False,
            ):
                dpg.add_table_column(width_fixed=True, init_width_or_weight=160)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=220)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=160)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=220)

                for row in _STAT_ROWS:
                    with dpg.table_row():
                        for name in row:
                            if name is None:
                                dpg.add_text("")
                            else:
                                with dpg.group(horizontal=True):
                                    dpg.add_text(f"{name}:", color=TEXT_DIM)
                                    self._text_ids[name] = dpg.add_text("--")

            # Exit reasons on its own line
            with dpg.group(horizontal=True):
                dpg.add_text("Exit Reasons:", color=TEXT_DIM)
                self._exit_id = dpg.add_text("--")

            # Backtest-specific metrics (hidden until a backtest session is loaded)
            self._bt_header = dpg.add_collapsing_header(
                label="  Backtest Metrics", default_open=True, show=False,
            )
            with dpg.table(
                parent=self._bt_header,
                header_row=False,
                resizable=False,
                borders_innerH=False,
                borders_outerH=False,
                borders_innerV=False,
                borders_outerV=False,
            ):
                dpg.add_table_column(width_fixed=True, init_width_or_weight=160)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=220)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=160)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=220)

                for row in _BACKTEST_ROWS:
                    with dpg.table_row():
                        for name in row:
                            if name is None:
                                dpg.add_text("")
                            else:
                                with dpg.group(horizontal=True):
                                    dpg.add_text(f"{name}:", color=TEXT_DIM)
                                    self._bt_text_ids[name] = dpg.add_text("--")

            dpg.add_separator()

            with dpg.collapsing_header(label="  Strategy Breakdown", default_open=True):
                self._strat_container = dpg.add_group()

    def update(self, session_data: SessionData) -> None:
        stats = compute_statistics(session_data.trades_df, session_data.summary)

        for name, (val, fmt) in stats.items():
            if name == "Exit Reasons":
                if self._exit_id is not None:
                    dpg.set_value(self._exit_id, fmt)
                continue

            tid = self._text_ids.get(name)
            if tid is None:
                continue
            dpg.set_value(tid, fmt)

            if name in _COLOR_METRICS:
                dpg.configure_item(
                    tid,
                    color=GREEN_PROFIT if val > 0 else RED_LOSS if val < 0 else TEXT_DIM,
                )
            elif name in _SHARPE_METRICS:
                dpg.configure_item(
                    tid,
                    color=GREEN_PROFIT if val > 0.5 else YELLOW_ACCENT if val > 0 else RED_LOSS,
                )
            elif name == "Win Rate":
                dpg.configure_item(
                    tid,
                    color=GREEN_PROFIT if val > 0.5 else YELLOW_ACCENT if val > 0.3 else RED_LOSS,
                )
            elif name == "Profit Factor":
                dpg.configure_item(
                    tid,
                    color=GREEN_PROFIT if val > 1.5 else YELLOW_ACCENT if val > 1.0 else RED_LOSS,
                )
            elif name == "Kelly Criterion":
                dpg.configure_item(
                    tid,
                    color=GREEN_PROFIT if val > 0 else RED_LOSS if val < 0 else TEXT_DIM,
                )

        self._update_backtest_metrics(session_data)
        self._update_strategies(session_data)

    # ── private ────────────────────────────────────────────────────────
    def _update_backtest_metrics(self, session_data: SessionData) -> None:
        is_backtest = session_data.summary.get("config", {}).get("mode") == "backtest"
        dpg.configure_item(self._bt_header, show=is_backtest)

        if not is_backtest:
            return

        bt_stats = compute_backtest_statistics(
            session_data.trades_df, session_data.summary, session_data.snapshots,
        )
        for name, (val, fmt) in bt_stats.items():
            tid = self._bt_text_ids.get(name)
            if tid is None:
                continue
            dpg.set_value(tid, fmt)
            if name in _BACKTEST_COLOR_METRICS:
                dpg.configure_item(
                    tid,
                    color=GREEN_PROFIT if val > 0 else RED_LOSS if val < 0 else TEXT_DIM,
                )

    def _update_strategies(self, session_data: SessionData) -> None:
        dpg.delete_item(self._strat_container, children_only=True)

        breakdown = compute_strategy_breakdown(session_data.trades_df)
        if not breakdown:
            dpg.add_text("No strategy data.", parent=self._strat_container, color=TEXT_DIM)
            return

        with dpg.table(
            parent=self._strat_container,
            header_row=True,
            resizable=True,
            borders_innerH=True,
            borders_outerH=True,
            borders_innerV=True,
            row_background=True,
        ):
            dpg.add_table_column(label="Strategy")
            dpg.add_table_column(label="Trades")
            dpg.add_table_column(label="Wins")
            dpg.add_table_column(label="Win Rate")
            dpg.add_table_column(label="Total P&L")
            dpg.add_table_column(label="Avg P&L")
            dpg.add_table_column(label="Profit Factor")

            for name, info in breakdown.items():
                with dpg.table_row():
                    dpg.add_text(name)
                    dpg.add_text(str(info["trades"]))
                    dpg.add_text(str(info["wins"]))
                    dpg.add_text(f"{info['win_rate'] * 100:.1f}%")
                    c = GREEN_PROFIT if info["total_pnl"] > 0 else RED_LOSS
                    dpg.add_text(f"${info['total_pnl']:+.2f}", color=c)
                    c = GREEN_PROFIT if info["avg_pnl"] > 0 else RED_LOSS
                    dpg.add_text(f"${info['avg_pnl']:+.2f}", color=c)
                    pf = info["profit_factor"]
                    pf_str = f"{pf:.2f}" if pf != float("inf") else "\u221e"
                    c = GREEN_PROFIT if pf > 1.0 else RED_LOSS
                    dpg.add_text(pf_str, color=c)
