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
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "videolib.db")
    monkeypatch.setattr(config, "THUMBS_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)


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


def test_list_backups_newest_first(tmp_path):
    from services.backup import list_backups

    d = tmp_path / "backups"
    d.mkdir()
    names = [
        "videolib-20260101-010000.db",
        "videolib-20260102-010000.db",
        "videolib-20260102-020000.db",
        "other.db",
    ]
    for n in names:
        (d / n).write_bytes(b"x")

    got = [p.name for p in list_backups()]
    assert got == [names[2], names[1], names[0]], "newest first, non-matching ignored"


def test_restore_backup_rewinds_db(tmp_path):
    """restore_backup: snapshot first, drop WAL sidecars, wipe thumbs, and
    rewind the live DB to the chosen snapshot."""
    import config

    from services.backup import backup_db, list_backups, restore_backup

    old = Repository(config.DB_PATH)
    old.upsert_videos([Video(filename="a.mp4", filepath=r"D:\v\a.mp4")])
    snap = backup_db(old, force=True)
    # give the snapshot a clearly older stamp so the pre-restore snapshot
    # (created a moment later) cannot collide with it on the same second
    snap = snap.rename(config.BACKUPS_DIR / "videolib-20260101-010000.db")
    old.close()

    live = Repository(config.DB_PATH)
    live.upsert_videos([Video(filename="b.mp4", filepath=r"D:\v\b.mp4")])
    live.close()

    # fake WAL sidecars + a thumbnail (db closed, so the files are not locked)
    config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    (config.THUMBS_DIR / "1.jpg").write_bytes(b"\xff\xd8")
    db = Path(config.DB_PATH)
    wal = db.with_name(db.name + "-wal")
    wal.write_bytes(b"stale")
    shm = db.with_name(db.name + "-shm")
    shm.write_bytes(b"stale")

    live = Repository(config.DB_PATH)  # reopen for the restore call
    n_before = len(list_backups())
    restore_backup(live, snap)
    assert len(list_backups()) == n_before + 1, "pre-restore state must be snapshotted"
    assert not wal.exists(), "WAL sidecar must be dropped"
    assert not shm.exists(), "SHM sidecar must be dropped"
    assert not config.THUMBS_DIR.joinpath("1.jpg").exists(), "thumbs must be wiped"

    reopened = Repository(config.DB_PATH)
    try:
        paths = {v.filepath for v in reopened.all_videos()}
        assert r"D:\v\a.mp4" in paths
        assert r"D:\v\b.mp4" not in paths, "live DB must match the snapshot"
    finally:
        reopened.close()


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
