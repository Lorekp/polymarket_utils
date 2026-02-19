"""Session dropdown selector + info bar."""

from __future__ import annotations

from typing import Callable

import dearpygui.dearpygui as dpg

from src.analyzer.components.theme import YELLOW_ACCENT, TEXT_DIM
from src.analyzer.data_loader import SessionData, discover_sessions, load_session


class SessionPicker:
    def __init__(self, on_session_changed: Callable[[SessionData], None]) -> None:
        self._callback = on_session_changed
        self._sessions: dict[str, str] = {}  # label -> session_id
        self._combo_id: int | str | None = None
        self._info_id: int | str | None = None

    def build(self, parent: int | str) -> None:
        labels = self._scan()

        with dpg.group(horizontal=True, parent=parent):
            dpg.add_text("SESSION", color=YELLOW_ACCENT)
            self._combo_id = dpg.add_combo(
                items=labels,
                default_value=labels[0] if labels else "",
                callback=self._on_select,
                width=500,
            )
            dpg.add_button(label="Refresh", callback=self._refresh)

        self._info_id = dpg.add_text("", parent=parent, color=TEXT_DIM)
        dpg.add_separator(parent=parent)
        dpg.add_spacer(height=4, parent=parent)

    def load_initial(self) -> None:
        """Load the first session. Call after all components are built."""
        if self._sessions:
            first_id = next(iter(self._sessions.values()))
            self._load_and_notify(first_id)

    # ── private ────────────────────────────────────────────────────────
    def _scan(self) -> list[str]:
        ids = discover_sessions()
        self._sessions.clear()
        labels: list[str] = []
        for sid in ids:
            data = load_session(sid)
            self._sessions[data.display_label] = sid
            labels.append(data.display_label)
        return labels

    def _on_select(self, sender: int, app_data: str) -> None:
        sid = self._sessions.get(app_data)
        if sid:
            self._load_and_notify(sid)

    def _load_and_notify(self, session_id: str) -> None:
        data = load_session(session_id)
        perf = data.summary.get("performance", {})
        cfg = data.summary.get("config", {})
        info = (
            f"Mode: {cfg.get('mode', '?')}   |   "
            f"Runtime: {data.summary.get('runtime_hours', 0):.2f}h   |   "
            f"Bankroll: ${perf.get('initial_bankroll', 0):,.0f}   |   "
            f"Strategies: {', '.join(cfg.get('strategies', {}).keys())}"
        )
        dpg.set_value(self._info_id, info)
        self._callback(data)

    def _refresh(self) -> None:
        labels = self._scan()
        dpg.configure_item(self._combo_id, items=labels)
        if labels:
            dpg.set_value(self._combo_id, labels[0])
