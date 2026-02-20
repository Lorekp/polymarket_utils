"""Polymarket Session Analyzer — main application window."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from src.analyzer.components.theme import apply_theme, YELLOW_ACCENT, GREEN_PROFIT, TEXT_DIM
from src.analyzer.components.session_picker import SessionPicker
from src.analyzer.components.equity_chart import EquityChart
from src.analyzer.components.stats_panel import StatsPanel
from src.analyzer.components.trades_table import TradesTable
from src.analyzer.components.backtest_tab import BacktestTab
from src.analyzer.data_loader import SessionData
from src.analyzer.report import generate_report

WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900
TITLE = "Polymarket Session Analyzer"


class SessionAnalyzerApp:
    def __init__(self) -> None:
        self.picker = SessionPicker(on_session_changed=self._on_session_changed)
        self.chart = EquityChart()
        self.stats = StatsPanel()
        self.trades = TradesTable()
        self.backtest = BacktestTab(on_backtest_done=self._on_backtest_done)
        self._current_session: SessionData | None = None
        self._report_status_id: int | str | None = None

    def run(self) -> None:
        dpg.create_context()
        dpg.create_viewport(
            title=TITLE,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_width=900,
            min_height=600,
        )

        apply_theme()

        with dpg.window(tag="primary_window"):
            # Top bar: session picker on the left, save button on the right
            with dpg.group(horizontal=True):
                with dpg.group():
                    self.picker.build(parent=dpg.last_item())
                # Push to right using a spacer trick — add button in same horizontal group
            # We need the button at top-right, so use a separate row
            # Rebuild: picker builds its own rows, so put the save button after the picker's separator
            # Instead, we add a floating-style group
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Save Report",
                    callback=self._save_report,
                )
                self._report_status_id = dpg.add_text("", color=TEXT_DIM)

            dpg.add_spacer(height=4)

            with dpg.tab_bar():
                with dpg.tab(label="  Overview  ") as overview_tab:
                    self.chart.build(parent=overview_tab)
                    dpg.add_spacer(height=8)
                    self.stats.build(parent=overview_tab)

                with dpg.tab(label="  Trades  ") as trades_tab:
                    self.trades.build(parent=trades_tab)

                with dpg.tab(label="  Backtest  ") as backtest_tab:
                    self.backtest.build(parent=backtest_tab)

        # Load first session now that all components exist
        self.picker.load_initial()

        dpg.set_primary_window("primary_window", True)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.start_dearpygui()
        dpg.destroy_context()

    def _on_session_changed(self, session_data: SessionData) -> None:
        self._current_session = session_data
        self.chart.update(session_data)
        self.stats.update(session_data)
        self.trades.update(session_data)
        # Clear any old report status
        if self._report_status_id:
            dpg.set_value(self._report_status_id, "")

    def _on_backtest_done(self, session_id: str) -> None:
        """Called when a backtest finishes — refresh the session picker."""
        self.picker._refresh()

    def _save_report(self) -> None:
        if self._current_session is None:
            dpg.set_value(self._report_status_id, "No session loaded.")
            return
        try:
            path = generate_report(self._current_session)
            dpg.set_value(self._report_status_id, f"Saved: {path}")
            dpg.configure_item(self._report_status_id, color=GREEN_PROFIT)
        except Exception as e:
            dpg.set_value(self._report_status_id, f"Error: {e}")
            dpg.configure_item(self._report_status_id, color=(220, 80, 80, 255))


def main() -> None:
    app = SessionAnalyzerApp()
    app.run()
