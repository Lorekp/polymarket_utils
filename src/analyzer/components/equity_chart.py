"""Realized P&L + Unrealized P&L chart."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from src.analyzer.components.theme import GREEN_PROFIT, RED_LOSS, YELLOW_ACCENT, TEXT_DIM
from src.analyzer.data_loader import SessionData
from src.analyzer.statistics import build_equity_curve, build_unrealized_curve

# Colours for the two series
_REALIZED_COL = (80, 200, 120, 255)    # green
_UNREALIZED_COL = (100, 160, 255, 255)  # blue


class EquityChart:
    def __init__(self) -> None:
        self.plot_id: int | str | None = None
        self.realized_id: int | str | None = None
        self.unrealized_id: int | str | None = None
        self.zero_id: int | str | None = None
        self.x_axis: int | str | None = None
        self.y_axis: int | str | None = None
        self._realized_theme: int | str | None = None
        self._unrealized_theme: int | str | None = None

    def build(self, parent: int | str) -> None:
        # Per-series themes
        with dpg.theme() as self._realized_theme:
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(dpg.mvPlotCol_Line, _REALIZED_COL, category=dpg.mvThemeCat_Plots)
                dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2.0, category=dpg.mvThemeCat_Plots)

        with dpg.theme() as self._unrealized_theme:
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(dpg.mvPlotCol_Line, _UNREALIZED_COL, category=dpg.mvThemeCat_Plots)
                dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1.5, category=dpg.mvThemeCat_Plots)

        with dpg.plot(
            label="Profit & Loss",
            parent=parent,
            height=350,
            width=-1,
            anti_aliased=True,
            no_mouse_pos=True,
        ) as self.plot_id:
            dpg.add_plot_legend()

            self.x_axis = dpg.add_plot_axis(
                dpg.mvXAxis, label="Time",
                scale=dpg.mvPlotScale_Time,
                no_gridlines=True,
            )
            self.y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="P&L ($)")

            # Zero reference
            self.zero_id = dpg.add_inf_line_series(
                [0.0], parent=self.y_axis, horizontal=True,
            )

            self.realized_id = dpg.add_line_series(
                [], [], label="Realized P&L", parent=self.y_axis,
            )
            dpg.bind_item_theme(self.realized_id, self._realized_theme)

            self.unrealized_id = dpg.add_line_series(
                [], [], label="Unrealized P&L", parent=self.y_axis,
            )
            dpg.bind_item_theme(self.unrealized_id, self._unrealized_theme)

    def update(self, session_data: SessionData) -> None:
        perf = session_data.summary.get("performance", {})
        initial = perf.get("initial_bankroll", 10000.0)

        # Realized P&L curve from trade exits
        r_times, r_values = build_equity_curve(session_data.trades_df, initial)
        dpg.set_value(self.realized_id, [r_times, r_values] if r_times else [[], []])

        # Unrealized P&L curve from JSONL snapshots
        u_times, u_values = build_unrealized_curve(session_data.snapshots)
        dpg.set_value(self.unrealized_id, [u_times, u_values] if u_times else [[], []])

        dpg.fit_axis_data(self.x_axis)
        dpg.fit_axis_data(self.y_axis)
