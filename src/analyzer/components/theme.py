"""Dark grey / yellow trading terminal theme for DearPyGui."""

import dearpygui.dearpygui as dpg

# ── Colour palette (RGBA 0-255) ───────────────────────────────────────
BG_DARK = (30, 30, 30, 255)
BG_PANEL = (40, 40, 40, 255)
BG_TABLE_ROW = (45, 45, 45, 255)
BG_FRAME = (50, 50, 50, 255)
BORDER = (60, 60, 60, 255)

TEXT_PRIMARY = (220, 220, 220, 255)
TEXT_DIM = (140, 140, 140, 255)

YELLOW_ACCENT = (255, 200, 50, 255)
YELLOW_HOVER = (255, 220, 100, 255)
YELLOW_DARK = (180, 150, 40, 255)

GREEN_PROFIT = (80, 200, 120, 255)
RED_LOSS = (220, 80, 80, 255)

PLOT_LINE = (255, 200, 50, 255)  # Yellow equity line
PLOT_BASELINE = (100, 100, 100, 255)


def apply_theme() -> None:
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            # Window / frame
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, BG_DARK)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, BG_FRAME)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (60, 60, 60, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (70, 70, 70, 255))

            # Text
            dpg.add_theme_color(dpg.mvThemeCol_Text, TEXT_PRIMARY)

            # Tabs
            dpg.add_theme_color(dpg.mvThemeCol_Tab, BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered, YELLOW_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_TabActive, YELLOW_ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_TabUnfocusedActive, YELLOW_DARK)

            # Headers (collapsing)
            dpg.add_theme_color(dpg.mvThemeCol_Header, BG_FRAME)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (70, 70, 70, 255))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, YELLOW_DARK)

            # Buttons
            dpg.add_theme_color(dpg.mvThemeCol_Button, (55, 55, 55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, YELLOW_HOVER)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, YELLOW_ACCENT)

            # Table
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, BG_FRAME)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, BG_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, BG_TABLE_ROW)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong, BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, BORDER)

            # Separator
            dpg.add_theme_color(dpg.mvThemeCol_Separator, BORDER)

            # Scrollbar
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, BG_DARK)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (80, 80, 80, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, (100, 100, 100, 255))

            # Rounding
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)

        # Plot-specific overrides
        with dpg.theme_component(dpg.mvLineSeries):
            dpg.add_theme_color(dpg.mvPlotCol_Line, PLOT_LINE, category=dpg.mvThemeCat_Plots)
            dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2.0, category=dpg.mvThemeCat_Plots)

    dpg.bind_theme(global_theme)
