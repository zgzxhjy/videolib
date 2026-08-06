import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_qss_dark_differs_from_light():
    from ui.theme import qss_for

    dark, light = qss_for("dark"), qss_for("light")
    assert dark != light
    assert len(dark) > 1000 and len(light) > 1000


def test_qss_palette_key_colours_land():
    """Both palettes must fill the critical blocks the UI depends on."""
    from ui.theme import qss_for

    for name in ("dark", "light"):
        qss = qss_for(name)
        assert "QTableView {" in qss
        assert "QHeaderView::section" in qss
        assert "QPushButton" in qss
        assert "QScrollBar" in qss
        assert "QMenu::item" in qss
        assert "QToolTip" in qss


def test_qss_styles_top_level_chrome():
    """Toolbar/status bar/menu bar must have explicit backgrounds so the top
    strip never falls back to the native (dark-on-dark) paint."""
    from ui.theme import qss_for

    for name in ("dark", "light"):
        qss = qss_for(name)
        assert "QToolBar {" in qss
        assert "QStatusBar {" in qss
        assert "QMenuBar {" in qss
        assert "QMainWindow, QDialog {" in qss


def test_qss_dark_uses_dark_view_background():
    from ui.theme import qss_for

    assert "background-color: #1e1f24" in qss_for("dark")
    assert "background-color: #ffffff" in qss_for("light")


def test_app_qss_windows_registry_dark(monkeypatch):
    from ui.theme import app_qss, qss_for

    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: True)
    assert app_qss(None) == qss_for("dark")


def test_app_qss_windows_registry_light(monkeypatch):
    from ui.theme import app_qss, qss_for

    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: False)
    assert app_qss(None) == qss_for("light")


def test_app_qss_honours_colour_scheme(qapp, monkeypatch):
    """When the registry is silent, Qt's colour scheme hint decides."""
    from PyQt6.QtCore import Qt

    from ui.theme import app_qss, qss_for

    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: None)

    class FakeHints:
        colorScheme = Qt.ColorScheme.Dark

    class FakeApp:
        def styleHints(self):
            return FakeHints()

    assert app_qss(FakeApp()) == qss_for("dark")


def test_app_qss_unknown_scheme_falls_back_to_palette_brightness(qapp, monkeypatch):
    """Unknown colourScheme (common on Windows) must not force light — the
    palette brightness decides instead."""
    from PyQt6.QtCore import Qt

    from ui.theme import app_qss, qss_for

    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: None)

    class UnknownHints:
        colorScheme = Qt.ColorScheme.Unknown

    class FakeApp:
        def styleHints(self):
            return UnknownHints()

        def palette(self):
            from PyQt6.QtGui import QPalette

            pal = QPalette()
            pal.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
            return pal

    assert app_qss(FakeApp()) == qss_for("dark")
