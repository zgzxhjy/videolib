"""Global palette + stylesheet. Single source of truth for colours so the
hardcoded play-button delegate and the QSS can never drift apart.

Two palettes, light and dark, are selected once at startup by the system
colour scheme (see `app_qss`). Both keep the B channel restrained so hover
and selection do not glare.
"""

import sys

# ----- play button: a single accent brand shared by both themes -----

COLOR_BTN_IDLE_BG = "#e9ebf4"
COLOR_BTN_IDLE_FG = "#3a4150"
COLOR_BTN_HOVER_BG = "#4a6fc0"                # B 237 -> 192
COLOR_BTN_HOVER_FG = "#ffffff"
COLOR_BTN_PRESS_BG = "#38569e"                # B 201 -> 158
COLOR_BTN_PRESS_FG = "#ffffff"

_TEMPLATE = """
QWidget {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
    color: {text};
}}

/* ---------- chrome: top-level faces and strips ---------- */
QMainWindow, QDialog {{
    background: {app_bg};
}}
QToolBar {{
    background: {app_bg};
    border: none;
    spacing: 4px;
}}
QStatusBar {{
    background: {app_bg};
    color: {text};
}}
QMenuBar {{
    background: {app_bg};
    color: {text};
    border: none;
}}
QMenuBar::item {{
    padding: 5px 10px;
    border-radius: 5px;
}}
QMenuBar::item:selected {{ background: {menu_item_hover}; }}
QMenuBar::item:pressed {{ background: {menu_item_hover}; }}

/* ---------- buttons: rounded, soft ---------- */
QPushButton {{
    border: 1px solid {border};
    border-radius: 6px;
    background: {surface};
    padding: 4px 14px;
    color: {text};
}}
QPushButton:hover {{ background: {surface_hover}; border-color: {border_strong}; }}
QPushButton:pressed {{ background: {surface_pressed}; }}
QPushButton:disabled {{ color: {text_disabled}; background: {surface_pressed}; border-color: {border}; }}

QToolButton {{
    border: none;
    border-radius: 6px;
    background: transparent;
    padding: 4px 10px;
}}
QToolButton:hover {{ background: {hover_wash}; }}
QToolButton:pressed {{ background: {surface_pressed}; }}
QToolButton:checked {{ background: {surface_pressed}; }}
QToolButton:disabled {{ color: {text_disabled}; }}

QLineEdit {{
    border: 1px solid {border};
    border-radius: 5px;
    padding: 3px 6px;
    background: {input_bg};
    color: {text};
}}
QLineEdit:focus {{ border-color: {accent}; }}

/* ---------- menus ---------- */
QMenu {{
    background: {menu_bg};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px;
    color: {text};
}}
QMenu::item {{
    padding: 5px 22px 5px 12px;
    border-radius: 5px;
}}
QMenu::item:selected {{ background: {menu_item_hover}; }}
QMenu::item:disabled {{ color: {text_disabled}; }}
QMenu::separator {{ height: 1px; background: {separator}; margin: 4px 8px; }}

/* ---------- item views ---------- */
QTableView {{
    background-color: {view_bg};
    color: {text};
    selection-background-color: {selected};
    selection-color: {selected_text};
    alternate-background-color: {alternate};
    gridline-color: {gridline};
}}
QTableView::item {{ padding: 0; }}
QTableView::item:hover {{ background: {hover_wash}; }}
QTableView::item:selected {{ background: {selected}; color: {selected_text}; }}
QTableView::item:selected:hover {{ background: {selected_hover}; }}

QHeaderView::section {{
    background: {header_bg};
    color: {text};
    border: none;
    border-bottom: 1px solid {separator};
    padding: 5px 8px;
}}

QTreeWidget, QListWidget {{
    background-color: {view_bg};
    color: {text};
}}
QTreeWidget::item, QListWidget::item {{
    padding: 3px 6px;
    border-radius: 5px;
}}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: {hover_wash}; }}
QTreeWidget::item:selected, QListWidget::item:selected {{
    background: {selected};
    color: {selected_text};
}}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ width: 10px; background: transparent; margin: 0; }}
QScrollBar::handle:vertical {{ background: {scrollbar}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {scrollbar_hover}; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {scrollbar}; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {scrollbar_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {tooltip_bg};
    color: {tooltip_text};
    border: none;
    border-radius: 6px;
    padding: 5px 7px;
}}
"""

_LIGHT = dict(
    app_bg="#f2f5fa",
    text="#1b2230",
    text_disabled="#a2a9b5",
    border="#c4cddb",
    border_strong="#b3c0d3",
    surface="#f2f5fa",
    surface_hover="#e3eaf5",
    surface_pressed="#d4def0",
    input_bg="#ffffff",
    accent="#8fa8d8",
    menu_bg="#ffffff",
    menu_item_hover="#dbe4f4",
    separator="#e4e8ee",
    view_bg="#ffffff",
    alternate="#f4f6fa",
    gridline="#eef1f6",
    hover_wash="rgba(13, 27, 62, 0.10)",
    selected="#c9d5ec",
    selected_text="#1b2230",
    selected_hover="#c9d5ec",
    header_bg="#eef2f8",
    scrollbar="#c3cbda",
    scrollbar_hover="#a9b4cb",
    tooltip_bg="#2b3240",
    tooltip_text="#ffffff",
)

_DARK = dict(
    app_bg="#1f2126",
    text="#d8dbe4",
    text_disabled="#5f6672",
    border="#3c414b",
    border_strong="#545c69",
    surface="#26292f",
    surface_hover="#2e323a",
    surface_pressed="#20242b",
    input_bg="#191b20",
    accent="#5b7bb0",
    menu_bg="#23262b",
    menu_item_hover="#2c3540",
    separator="#333842",
    view_bg="#1e1f24",
    alternate="#26282e",
    gridline="#2c3038",
    hover_wash="rgba(255, 255, 255, 0.06)",
    selected="#2f4a75",
    selected_text="#e8eaf0",
    selected_hover="#35557f",
    header_bg="#282c34",
    scrollbar="#4a515e",
    scrollbar_hover="#5b6478",
    tooltip_bg="#0d0f12",
    tooltip_text="#e6e8ee",
)

_PALETTES = {"light": _LIGHT, "dark": _DARK}


def qss_for(scheme: str) -> str:
    """Build the application stylesheet for a palette ('light' | 'dark')."""
    return _TEMPLATE.format(**_PALETTES.get(scheme, _DARK))


def _detect_windows_dark() -> bool | None:
    """AppsUseLightTheme registry value: 0 -> dark, 1 -> light, absent -> None."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return None


def _palette_dark(app) -> bool:
    color = app.palette().color(app.palette().ColorRole.Window)
    return color.lightness() < 128


def app_qss(app) -> str:
    """Stylesheet matching the running application's colour scheme.

    Detection order: Windows registry (authoritative where present), then
    Qt's colourScheme hint, then palette background lightness. The registry
    check matters because styleHints().colorScheme() commonly reports
    Unknown on Windows, which used to force the light theme on dark systems.
    """
    if sys.platform == "win32":
        reg_dark = _detect_windows_dark()
        if reg_dark is not None:
            return qss_for("dark" if reg_dark else "light")

    from PyQt6.QtCore import Qt

    cs = getattr(app.styleHints(), "colorScheme", None)
    if cs == Qt.ColorScheme.Dark:
        return qss_for("dark")
    if cs == Qt.ColorScheme.Light:
        return qss_for("light")
    return qss_for("dark" if _palette_dark(app) else "light")