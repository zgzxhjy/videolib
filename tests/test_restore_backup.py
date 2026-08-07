import pytest


def _mk_backup(name: str, size: int = 42):
    import config

    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    (config.BACKUPS_DIR / name).write_bytes(b"x" * size)


def _make_restore_dialog(app_env):
    from ui.dialogs.restore_backup import RestoreBackupDialog

    return RestoreBackupDialog(app_env)


def test_restore_dialog_empty_state_disables_restore(qapp, app_env):
    d = _make_restore_dialog(app_env)
    try:
        assert d.list.count() == 1
        assert "(暂无备份)" in d.list.item(0).text()
        assert not d.btn_restore.isEnabled()
    finally:
        d.close()


def test_restore_dialog_lists_backups_newest_first(qapp, app_env):
    _mk_backup("videolib-20260101-010000.db")
    _mk_backup("videolib-20260102-020000.db")
    _mk_backup("videolib-20260102-010000.db")
    _mk_backup("other.db")

    d = _make_restore_dialog(app_env)
    try:
        assert d.list.count() == 3, "non-matching names are ignored"
        assert d.btn_restore.isEnabled()
        assert "2026-01-02 02:00:00" in d.list.item(0).text()
        assert "2026-01-02 01:00:00" in d.list.item(1).text()
        assert "2026-01-01 01:00:00" in d.list.item(2).text()
    finally:
        d.close()


def test_restore_dialog_select_returns_choice(qapp, app_env, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox

    _mk_backup("videolib-20260102-020000.db")
    d = _make_restore_dialog(app_env)
    try:
        monkeypatch.setattr(
            "ui.dialogs.restore_backup.QMessageBox.question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        d.list.setCurrentRow(0)
        d._choose_restore()
        assert d.restore_choice is not None
        assert d.restore_choice.name == "videolib-20260102-020000.db"
    finally:
        d.close()


def test_restore_dialog_take_backup_refreshes(qapp, app_env, monkeypatch):
    from ui.dialogs.restore_backup import RestoreBackupDialog

    d = RestoreBackupDialog(app_env)
    try:
        monkeypatch.setattr(
            "ui.dialogs.restore_backup.QMessageBox.information",
            lambda *a, **k: None,
        )
        assert d.list.count() == 1  # empty hint
        d._take_backup()
        assert d.list.count() == 1
        assert d.btn_restore.isEnabled()
        assert "videolib-" in d.list.item(0).text()
    finally:
        d.close()