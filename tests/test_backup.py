import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.models import Video
from domain.repository import Repository


@pytest.fixture()
def repo(tmp_path):
    r = Repository(tmp_path / "db.sqlite")
    yield r
    r.close()


@pytest.fixture(autouse=True)
def _backup_dir(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")


def _today():
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d")


def test_backup_db_writes_snapshot_and_rotates(repo, tmp_path):
    from services.backup import _rotate, backup_db

    repo.upsert_videos([Video(filename="a.mp4", filepath=r"D:\v\a.mp4")])

    first = backup_db(repo, force=True)
    assert first is not None, "force=True must always write"

    # rotation: only the newest BACKUP_KEEP snapshots survive
    d = first.parent
    for i in range(7):
        (d / f"videolib-2026010{i}-000000.db").write_bytes(b"x")
    _rotate(d)
    files = sorted(d.glob("videolib-*.db"))
    assert len(files) == 5, "rotation must keep BACKUP_KEEP snapshots"
    assert first.name in [f.name for f in files], "the real snapshot must survive"

    # the snapshot is a usable db with our data
    import sqlite3

    conn = sqlite3.connect(str(first))
    try:
        assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
    finally:
        conn.close()


def test_backup_db_daily_startup_skips_same_day(repo, tmp_path):
    from services.backup import backup_db

    first = backup_db(repo)
    assert first is not None
    second = backup_db(repo)
    assert second is None, "a plain startup backup happens once per day"

    files = list(tmp_path.glob("backups/videolib-*.db"))
    assert len(files) == 1


def test_library_destructive_ops_snapshot_first(repo, tmp_path):
    """remove_root/remove_paths must leave a recoverable snapshot behind."""
    import config

    from services.library import Library

    repo.upsert_videos([Video(filename="a.mp4", filepath=r"D:\v\a.mp4")])
    lib = Library(repo, tmp_path / "thumbs")
    lib.remove_root(r"D:\v")

    files = list(config.BACKUPS_DIR.glob("videolib-*.db"))
    assert files, "destructive operations must snapshot first"

    import sqlite3

    conn = sqlite3.connect(str(files[-1]))
    try:
        assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
    finally:
        conn.close()
