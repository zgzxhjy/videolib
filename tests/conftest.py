import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def repo(tmp_path):
    from domain.repository import Repository

    r = Repository(tmp_path / "db.sqlite")
    yield r
    r.close()


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Isolate config paths so UI code never touches the real ~/.videolib."""
    import config

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "videolib.db")
    monkeypatch.setattr(config, "THUMBS_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")

    from domain.repository import Repository

    r = Repository(tmp_path / "videolib.db")
    yield r
    r.close()


@pytest.fixture()
def backup_env(monkeypatch, tmp_path):
    """Config isolation without an open Repository — for tests that poke the
    raw DB files (WAL sidecars, restore) where a live connection would keep
    the -shm mapping and make the sidecar writes fail on Windows."""
    import config

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "videolib.db")
    monkeypatch.setattr(config, "THUMBS_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")


@pytest.fixture(autouse=True)
def _isolate_backups(monkeypatch, tmp_path):
    """Destructive operations snapshot the DB to config.BACKUPS_DIR; keep
    tests away from the user's real backup folder (iron law #24-style)."""
    import config

    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
