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


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import config

    from domain.repository import Repository

    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "videolib.db")
    monkeypatch.setattr(config, "THUMBS_DIR", tmp_path / "thumbs")
    repo = Repository(tmp_path / "videolib.db")
    yield repo
    repo.close()


def _mk_backup(name: str, size: int = 42):
    import config

    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    (config.BACKUPS_DIR / name).write_bytes(b"x" * size)


def _make_restore_dialog(env):
    from ui.dialogs.restore_backup import RestoreBackupDialog

    return RestoreBackupDialog(env)


def test_restore_dialog_empty_state_disables_restore(qapp, env):
    d = _make_restore_dialog(env)
    try:
        assert d.list.count() == 1
        assert "(暂无备份)" in d.list.item(0).text()
        assert not d.btn_restore.isEnabled()
    finally:
        d.close()


def test_restore_dialog_lists_backups_newest_first(qapp, env):
    _mk_backup("videolib-20260101-010000.db")
    _mk_backup("videolib-20260102-020000.db")
    _mk_backup("videolib-20260102-010000.db")
    _mk_backup("other.db")

    d = _make_restore_dialog(env)
    try:
        assert d.list.count() == 3, "non-matching names are ignored"
        assert d.btn_restore.isEnabled()
        assert "2026-01-02 02:00:00" in d.list.item(0).text()
        assert "2026-01-02 01:00:00" in d.list.item(1).text()
        assert "2026-01-01 01:00:00" in d.list.item(2).text()
    finally:
        d.close()


def test_restore_dialog_select_returns_choice(qapp, env, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox

    _mk_backup("videolib-20260102-020000.db")
    d = _make_restore_dialog(env)
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


def test_restore_dialog_take_backup_refreshes(qapp, env, monkeypatch):
    from ui.dialogs.restore_backup import RestoreBackupDialog

    d = RestoreBackupDialog(env)
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