import pytest


@pytest.fixture(autouse=True)
def _isolate_backups(monkeypatch, tmp_path):
    """Destructive operations snapshot the DB to config.BACKUPS_DIR; keep
    tests away from the user's real backup folder (iron law #24-style)."""
    import config

    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
