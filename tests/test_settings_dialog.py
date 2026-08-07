"""SettingsDialog: 回填当前设置、确定保存+立即应用主题、取消不改。"""

from PyQt6.QtWidgets import QApplication

import config
from ui.dialogs.settings_dialog import SettingsDialog, THEME_CHOICES, THUMB_MODE_CHOICES


def _open_dialog(app_env):
    return SettingsDialog()


def test_dialog_preloads_current_settings(qapp, app_env):
    config.save_setting("theme", "dark")
    config.save_setting("volume", 42)
    config.save_setting("backup_keep", 9)
    config.save_setting("thumb_mode", "fixed")

    d = _open_dialog(app_env)
    try:
        assert d.combo_theme.currentData() == "dark"
        assert d.slider_volume.value() == 42
        assert d.volume_label.text() == "42%"
        assert d.spin_backup.value() == 9
        assert d.combo_thumb.currentData() == "fixed"
    finally:
        d.close()


def test_dialog_defaults_when_no_settings(qapp, app_env):
    d = _open_dialog(app_env)
    try:
        assert d.combo_theme.currentData() == "system"
        assert d.slider_volume.value() == 80
        assert d.spin_backup.value() == config.BACKUP_KEEP
        assert d.combo_thumb.currentData() == "random"
    finally:
        d.close()


def test_accept_saves_and_applies_theme(qapp, app_env, monkeypatch):
    """accept() persists the three settings and triggers apply_theme.

    apply_theme is patched here: in a full-suite run, setStyleSheet on the
    session QApplication re-polishes widgets left over from earlier tests and
    can hit a torn-down object (access violation). The settings→qss mapping
    itself is covered by test_theme.
    """
    applied = []
    monkeypatch.setattr(
        "ui.dialogs.settings_dialog.apply_theme", lambda app: applied.append(app)
    )
    d = _open_dialog(app_env)
    try:
        d.combo_theme.setCurrentIndex(
            next(i for i, (v, _l) in enumerate(THEME_CHOICES) if v == "dark")
        )
        d.slider_volume.setValue(60)
        d.spin_backup.setValue(3)
        d.combo_thumb.setCurrentIndex(
            next(i for i, (v, _l) in enumerate(THUMB_MODE_CHOICES) if v == "fixed")
        )
        d.accept()
    finally:
        d.close()

    settings = config.load_settings()
    assert settings["theme"] == "dark"
    assert settings["volume"] == 60
    assert settings["backup_keep"] == 3
    assert settings["thumb_mode"] == "fixed"
    assert applied, "accept() must apply the new theme"

    from ui.theme import app_qss

    app = QApplication.instance()
    assert app is not None
    assert "background-color: #1e1f24" in app_qss(app)


def test_cancel_does_not_change_settings(qapp, app_env):
    config.save_setting("theme", "light")
    d = _open_dialog(app_env)
    try:
        d.combo_theme.setCurrentIndex(
            next(i for i, (v, _l) in enumerate(THEME_CHOICES) if v == "dark")
        )
        d.reject()
    finally:
        d.close()

    assert config.load_settings().get("theme") == "light"
