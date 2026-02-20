"""Backtest configuration and execution tab for the analyzer GUI."""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

import dearpygui.dearpygui as dpg

from src.analyzer.components.theme import (
    GREEN_PROFIT, RED_LOSS, TEXT_DIM, YELLOW_ACCENT,
)

DATA_DIR = Path("data")


class BacktestTab:
    """DearPyGui tab for configuring, launching, and monitoring backtests."""

    def __init__(self, on_backtest_done: callable | None = None) -> None:
        self._on_done = on_backtest_done
        self._markets_combo: int | str | None = None
        self._start_input: int | str | None = None
        self._end_input: int | str | None = None
        self._bankroll_input: int | str | None = None
        self._interval_combo: int | str | None = None
        self._config_input: int | str | None = None
        self._run_btn: int | str | None = None
        self._cancel_btn: int | str | None = None
        self._progress_bar: int | str | None = None
        self._progress_text: int | str | None = None
        self._status_text: int | str | None = None
        self._result_group: int | str | None = None
        self._running = False
        self._cancel_flag = False
        self._thread: threading.Thread | None = None
        self._last_session_id: str | None = None
        self._parquet_map: dict[str, str] = {}  # display label -> markets parquet path

    def build(self, parent: int | str) -> None:
        with dpg.group(parent=parent):
            dpg.add_text("BACKTEST CONFIGURATION", color=YELLOW_ACCENT)
            dpg.add_spacer(height=8)

            # ── Data file selection ──
            labels = self._scan_parquet_files()

            with dpg.group(horizontal=True):
                dpg.add_text("Markets File:", color=TEXT_DIM)
                self._markets_combo = dpg.add_combo(
                    items=labels,
                    default_value=labels[0] if labels else "",
                    width=450,
                    callback=self._on_markets_changed,
                )
                dpg.add_button(label="Refresh", callback=self._refresh_files)

            dpg.add_spacer(height=4)

            # ── Date range ──
            with dpg.group(horizontal=True):
                dpg.add_text("Start Date:", color=TEXT_DIM)
                self._start_input = dpg.add_input_text(
                    default_value="2025-01-01", width=120,
                )
                dpg.add_spacer(width=20)
                dpg.add_text("End Date:", color=TEXT_DIM)
                self._end_input = dpg.add_input_text(
                    default_value="2025-03-31", width=120,
                )

            dpg.add_spacer(height=4)

            # ── Parameters ──
            with dpg.group(horizontal=True):
                dpg.add_text("Bankroll ($):", color=TEXT_DIM)
                self._bankroll_input = dpg.add_input_float(
                    default_value=10000.0, width=120, format="%.0f",
                )
                dpg.add_spacer(width=20)
                dpg.add_text("Scan Interval:", color=TEXT_DIM)
                self._interval_combo = dpg.add_combo(
                    items=["5 min", "15 min", "30 min", "60 min"],
                    default_value="60 min",
                    width=100,
                )
                dpg.add_spacer(width=20)
                dpg.add_text("Config:", color=TEXT_DIM)
                self._config_input = dpg.add_input_text(
                    default_value="config.json", width=180,
                )

            dpg.add_spacer(height=12)

            # ── Run / Cancel buttons ──
            with dpg.group(horizontal=True):
                self._run_btn = dpg.add_button(
                    label="Run Backtest", callback=self._on_run, width=140,
                )
                self._cancel_btn = dpg.add_button(
                    label="Cancel", callback=self._on_cancel, width=100,
                    enabled=False,
                )

            dpg.add_spacer(height=8)

            # ── Progress ──
            self._progress_bar = dpg.add_progress_bar(
                default_value=0.0, width=-1, overlay="0%",
            )
            self._progress_text = dpg.add_text("", color=TEXT_DIM)

            dpg.add_spacer(height=8)

            # ── Status / Result ──
            self._status_text = dpg.add_text("Ready.", color=TEXT_DIM)
            self._result_group = dpg.add_group()

    # ── Parquet file discovery ─────────────────────────────────────────

    def _scan_parquet_files(self) -> list[str]:
        self._parquet_map.clear()
        labels: list[str] = []
        if not DATA_DIR.exists():
            return labels
        for p in sorted(DATA_DIR.glob("markets_*_closed.parquet")):
            name = p.stem  # e.g. markets_2025_closed
            price_file = p.parent / f"{name}_price_history.parquet"
            if price_file.exists():
                label = name.replace("markets_", "").replace("_closed", "")
                display = f"{label} — {p.name}"
                self._parquet_map[display] = str(p)
                labels.append(display)
        return labels

    def _refresh_files(self) -> None:
        labels = self._scan_parquet_files()
        dpg.configure_item(self._markets_combo, items=labels)
        if labels:
            dpg.set_value(self._markets_combo, labels[0])

    def _on_markets_changed(self, sender: int, app_data: str) -> None:
        pass  # Could auto-fill date range from parquet bounds in the future

    # ── Run / Cancel ───────────────────────────────────────────────────

    def _on_run(self) -> None:
        if self._running:
            return

        # Gather parameters
        markets_label = dpg.get_value(self._markets_combo)
        markets_path = self._parquet_map.get(markets_label)
        if not markets_path:
            dpg.set_value(self._status_text, "No markets file selected.")
            dpg.configure_item(self._status_text, color=RED_LOSS)
            return

        # Derive price history path from markets path
        mp = Path(markets_path)
        price_path = mp.parent / f"{mp.stem}_price_history.parquet"
        if not price_path.exists():
            dpg.set_value(
                self._status_text,
                f"Price history not found: {price_path.name}",
            )
            dpg.configure_item(self._status_text, color=RED_LOSS)
            return

        start_str = dpg.get_value(self._start_input).strip()
        end_str = dpg.get_value(self._end_input).strip()
        bankroll = dpg.get_value(self._bankroll_input)
        interval_str = dpg.get_value(self._interval_combo)
        config_path = dpg.get_value(self._config_input).strip()

        # Parse interval
        interval = int(interval_str.split()[0])

        # Parse dates
        from datetime import datetime, timezone
        try:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc,
            )
        except ValueError as e:
            dpg.set_value(self._status_text, f"Invalid date: {e}")
            dpg.configure_item(self._status_text, color=RED_LOSS)
            return

        if end_dt <= start_dt:
            dpg.set_value(self._status_text, "End date must be after start date.")
            dpg.configure_item(self._status_text, color=RED_LOSS)
            return

        # UI state
        self._running = True
        self._cancel_flag = False
        dpg.configure_item(self._run_btn, enabled=False)
        dpg.configure_item(self._cancel_btn, enabled=True)
        dpg.set_value(self._progress_bar, 0.0)
        dpg.configure_item(self._progress_bar, overlay="0%")
        dpg.set_value(self._progress_text, "")
        dpg.set_value(self._status_text, "Starting backtest...")
        dpg.configure_item(self._status_text, color=YELLOW_ACCENT)
        dpg.delete_item(self._result_group, children_only=True)

        # Launch in background thread
        self._thread = threading.Thread(
            target=self._run_backtest,
            args=(
                str(markets_path),
                str(price_path),
                start_dt,
                end_dt,
                interval,
                bankroll,
                config_path,
            ),
            daemon=True,
        )
        self._thread.start()

    def _on_cancel(self) -> None:
        self._cancel_flag = True
        dpg.set_value(self._status_text, "Cancelling...")
        dpg.configure_item(self._status_text, color=YELLOW_ACCENT)

    def _run_backtest(
        self,
        markets_path: str,
        price_path: str,
        start_dt,
        end_dt,
        interval: int,
        bankroll: float,
        config_path: str,
    ) -> None:
        """Execute backtest in background thread. Updates GUI via dpg.set_value (thread-safe)."""
        try:
            from src.bot.backtest.runner import BacktestConfig, BacktestRunner

            config = BacktestConfig(
                markets_parquet=markets_path,
                price_history_parquet=price_path,
                start_date=start_dt,
                end_date=end_dt,
                scan_interval_minutes=interval,
                initial_bankroll=bankroll,
                bot_config_path=config_path,
            )

            runner = BacktestRunner(config)

            def progress(step: int, total: int, summary: dict) -> None:
                if self._cancel_flag:
                    raise KeyboardInterrupt("Backtest cancelled by user.")

                pct = step / total if total > 0 else 0
                dpg.set_value(self._progress_bar, pct)
                dpg.configure_item(self._progress_bar, overlay=f"{pct * 100:.1f}%")

                sim_date = summary.get("sim_time", "")[:10]
                pv = summary.get("portfolio_value", 0)
                trades = summary.get("total_trades", 0)
                rpnl = summary.get("total_realized_pnl", 0)
                dpg.set_value(
                    self._progress_text,
                    f"Step {step}/{total}  |  {sim_date}  |  "
                    f"${pv:,.2f}  |  {trades} trades  |  P&L: ${rpnl:+,.2f}",
                )

            session_id = runner.run(progress_callback=progress)
            self._last_session_id = session_id

            # Success
            dpg.set_value(self._progress_bar, 1.0)
            dpg.configure_item(self._progress_bar, overlay="100%")
            dpg.set_value(
                self._status_text,
                f"Backtest complete! Session: {session_id}",
            )
            dpg.configure_item(self._status_text, color=GREEN_PROFIT)

            # Show result with load button
            dpg.add_text(
                f"Session ID: {session_id}",
                parent=self._result_group,
                color=GREEN_PROFIT,
            )
            dpg.add_text(
                "Use the session picker above to load this session.",
                parent=self._result_group,
                color=TEXT_DIM,
            )
            if self._on_done:
                self._on_done(session_id)

        except KeyboardInterrupt:
            dpg.set_value(self._status_text, "Backtest cancelled.")
            dpg.configure_item(self._status_text, color=YELLOW_ACCENT)

        except Exception as e:
            tb = traceback.format_exc()
            dpg.set_value(self._status_text, f"Error: {e}")
            dpg.configure_item(self._status_text, color=RED_LOSS)
            dpg.add_text(
                tb[-500:] if len(tb) > 500 else tb,
                parent=self._result_group,
                color=RED_LOSS,
            )

        finally:
            self._running = False
            dpg.configure_item(self._run_btn, enabled=True)
            dpg.configure_item(self._cancel_btn, enabled=False)
