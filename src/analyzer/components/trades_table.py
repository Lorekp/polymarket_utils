"""Sortable, truncated trades table."""

from __future__ import annotations

import dearpygui.dearpygui as dpg
import pandas as pd

from src.analyzer.components.theme import GREEN_PROFIT, RED_LOSS, TEXT_DIM, YELLOW_ACCENT
from src.analyzer.data_loader import SessionData

MAX_DISPLAY_ROWS = 200

# (csv_column, header_label, width, formatter)
_COLUMNS: list[tuple[str, str, int, callable]] = [
    ("position_id", "ID", 100, str),
    ("question", "Question", 280, lambda v: (str(v)[:50] + "..") if len(str(v)) > 50 else str(v)),
    ("side", "Side", 75, str),
    ("strategy", "Strategy", 85, str),
    ("entry_price", "Entry", 65, lambda v: f"{float(v):.4f}"),
    ("exit_price", "Exit", 65, lambda v: f"{float(v):.4f}"),
    ("size_usd", "Size $", 65, lambda v: f"{float(v):.2f}"),
    ("realized_pnl", "P&L", 80, lambda v: f"${float(v):+.2f}"),
    ("exit_reason", "Reason", 95, str),
    ("hold_hours", "Hold (h)", 65, lambda v: f"{float(v):.1f}"),
    ("exit_time", "Exit Time", 155, lambda v: str(v)[:19]),
]


class TradesTable:
    def __init__(self) -> None:
        self._container: int | str | None = None
        self._info_id: int | str | None = None
        self._table_parent: int | str | None = None  # stable parent for rebuilds
        self._table_id: int | str | None = None
        self._df: pd.DataFrame = pd.DataFrame()
        self._sort_col: str = "exit_time"
        self._sort_asc: bool = True
        self._rebuilding: bool = False  # guard against re-entrant sort callbacks

    def build(self, parent: int | str) -> None:
        self._container = dpg.add_group(parent=parent)

        # Sort buttons row
        with dpg.group(horizontal=True, parent=self._container):
            dpg.add_text("Sort by:", color=TEXT_DIM)
            for col_name, label, _, _ in _COLUMNS:
                if col_name in ("realized_pnl", "strategy", "exit_reason", "hold_hours", "exit_time", "side"):
                    dpg.add_button(
                        label=label,
                        callback=self._make_sort_cb(col_name),
                        small=True,
                    )

        self._info_id = dpg.add_text("", parent=self._container, color=YELLOW_ACCENT)
        # Stable group that holds the table; we only delete its children on rebuild
        self._table_parent = dpg.add_group(parent=self._container)

    def update(self, session_data: SessionData) -> None:
        self._df = session_data.trades_df.copy()
        self._sort_col = "exit_time"
        self._sort_asc = True
        self._rebuild()

    # ── private ────────────────────────────────────────────────────────
    def _make_sort_cb(self, col_name: str):
        """Return a callback that toggles sort on the given column."""
        def cb(sender=None, app_data=None):
            if self._sort_col == col_name:
                self._sort_asc = not self._sort_asc
            else:
                self._sort_col = col_name
                self._sort_asc = col_name != "realized_pnl"  # default P&L to descending
            self._rebuild()
        return cb

    def _rebuild(self) -> None:
        if self._rebuilding:
            return
        self._rebuilding = True

        try:
            # Clear previous table
            dpg.delete_item(self._table_parent, children_only=True)
            self._table_id = None

            df = self._df
            total = len(df)

            if total == 0:
                dpg.set_value(self._info_id, "No trades in this session.")
                return

            if self._sort_col in df.columns:
                df = df.sort_values(self._sort_col, ascending=self._sort_asc)

            truncated = total > MAX_DISPLAY_ROWS
            show = df.head(MAX_DISPLAY_ROWS)

            arrow = " \u25b2" if self._sort_asc else " \u25bc"
            sort_info = f"  [sorted by {self._sort_col}{arrow}]"

            if truncated:
                dpg.set_value(
                    self._info_id,
                    f"Showing {MAX_DISPLAY_ROWS} of {total} trades{sort_info}",
                )
            else:
                dpg.set_value(self._info_id, f"{total} trades{sort_info}")

            with dpg.table(
                parent=self._table_parent,
                header_row=True,
                resizable=True,
                borders_innerH=True,
                borders_outerH=True,
                borders_innerV=True,
                borders_outerV=True,
                row_background=True,
                scrollY=True,
                height=500,
                freeze_rows=1,
            ) as self._table_id:
                for col_name, label, w, _ in _COLUMNS:
                    dpg.add_table_column(label=label, init_width_or_weight=w)

                for _, row in show.iterrows():
                    with dpg.table_row():
                        for col_name, _, _, fmt in _COLUMNS:
                            val = row.get(col_name, "")
                            try:
                                text = fmt(val)
                            except Exception:
                                text = str(val)

                            if col_name == "realized_pnl":
                                fv = float(val)
                                c = GREEN_PROFIT if fv > 0 else RED_LOSS if fv < 0 else TEXT_DIM
                                dpg.add_text(text, color=c)
                            elif col_name == "side":
                                c = GREEN_PROFIT if str(val) == "BUY_YES" else RED_LOSS
                                dpg.add_text(text, color=c)
                            else:
                                dpg.add_text(text)
        finally:
            self._rebuilding = False
