import pytest


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


def test_app_qss_windows_registry_dark(monkeypatch, iso_settings):
    from ui.theme import app_qss, qss_for

    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: True)
    assert app_qss(None) == qss_for("dark")


def test_app_qss_windows_registry_light(monkeypatch, iso_settings):
    from ui.theme import app_qss, qss_for

    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: False)
    assert app_qss(None) == qss_for("light")


def test_app_qss_honours_colour_scheme(qapp, monkeypatch, iso_settings):
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


def test_app_qss_unknown_scheme_falls_back_to_palette_brightness(qapp, monkeypatch, iso_settings):
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


@pytest.fixture()
def iso_settings(tmp_path, monkeypatch):
    """Point config.SETTINGS_PATH at a fresh file (pitfall #24)."""
    import config

    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    return config


def test_app_qss_explicit_scheme_overrides_system(monkeypatch):
    """An explicit `scheme` argument must win over registry detection."""
    from ui.theme import app_qss, qss_for

    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: True)
    assert app_qss(None, scheme="light") == qss_for("light")
    assert app_qss(None, scheme="dark") == qss_for("dark")


def test_app_qss_settings_override_wins_over_registry(iso_settings, monkeypatch):
    """settings['theme']='dark' must force dark even when the registry says light."""
    import config

    from ui.theme import app_qss, qss_for

    config.save_setting("theme", "dark")
    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: False)
    assert app_qss(None) == qss_for("dark")


def test_app_qss_settings_system_falls_back_to_detection(iso_settings, monkeypatch):
    """settings['theme']='system' must defer to the registry detection."""
    import config

    from ui.theme import app_qss, qss_for

    config.save_setting("theme", "system")
    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: True)
    assert app_qss(None) == qss_for("dark")

    config.save_setting("theme", "light")
    monkeypatch.setattr("ui.theme._detect_windows_dark", lambda: True)
    assert app_qss(None) == qss_for("light")
