import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtWidgets import QApplication

from domain.models import Video
from domain.repository import Repository


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def repo(tmp_path):
    r = Repository(tmp_path / "test.db")
    yield r
    r.close()


def _mk(filename: str, filepath: str, **kw) -> Video:
    defaults = dict(filename=filename, filepath=filepath, file_size=1024)
    defaults.update(kw)
    return Video(**defaults)


def _make_test_video(path: Path, seconds: int = 1) -> Path:
    """Tiny real mpeg4 so probes succeed (mirrors test_scan helper)."""
    import av

    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = 64
        stream.height = 48
        stream.pix_fmt = "yuv420p"
        for i in range(seconds * 10):
            frame = av.VideoFrame(64, 48, "yuv420p")
            for plane in frame.planes:
                plane.update(bytes([i % 256]) * plane.buffer_size)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def test_schema_has_probe_retry_at(repo):
    cols = {r[1] for r in repo._conn.execute("PRAGMA table_info(videos)")}
    assert "probe_retry_at" in cols


def test_migration_adds_probe_retry_at(tmp_path):
    """A DB created before the column existed must gain it on open."""
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """CREATE TABLE videos (
            id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            filepath TEXT UNIQUE NOT NULL,
            file_size INTEGER DEFAULT 0,
            file_mtime REAL,
            duration REAL,
            resolution TEXT,
            codec TEXT,
            thumb_path TEXT,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );"""
    )
    conn.commit()
    conn.close()
    r = Repository(db)
    try:
        cols = {row[1] for row in r._conn.execute("PRAGMA table_info(videos)")}
        assert "probe_retry_at" in cols, "migration must backfill the column"
    finally:
        r.close()


def test_missing_metadata_files_filters_null_rows(repo):
    repo.upsert_videos(
        [
            _mk("a.mp4", r"D:\v\a.mp4"),  # no metadata at all
            _mk("b.mp4", r"D:\v\b.mp4", duration=5.0, resolution="64x48", codec="mpeg4"),
            _mk("c.mp4", r"D:\v\c.mp4", duration=5.0),  # partial: leave alone
        ]
    )
    missing = repo.missing_metadata_files()
    assert [v.filepath for v in missing] == [r"D:\v\a.mp4"], (
        "only all-NULL rows are repairable; partial rows were probed once"
    )


def test_missing_metadata_files_respects_cutoff(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4")])
    now = time.time()
    repo.mark_probe_failed([r"D:\v\a.mp4"])
    assert repo.missing_metadata_files() != []
    assert repo.missing_metadata_files(cutoff=now - 1) == [], (
        "rows retried after the cutoff must not come back"
    )
    assert repo.missing_metadata_files(cutoff=now + 1) != [], (
        "rows retried before the cutoff are eligible again"
    )


def test_missing_metadata_under_scoped_to_root(repo):
    repo.upsert_videos(
        [
            _mk("a.mp4", r"D:\x\a.mp4"),
            _mk("b.mp4", r"D:\x\b.mp4", duration=5.0, resolution="16x16", codec="mpeg4"),
            _mk("c.mp4", r"D:\y\c.mp4"),
        ]
    )
    assert repo.missing_metadata_under(r"D:\x") == {r"D:\x\a.mp4"}
    assert repo.missing_metadata_under(r"D:\y") == {r"D:\y\c.mp4"}


def test_diff_scan_includes_missing_meta(tmp_path):
    from services.scanner import diff_scan

    a = _make_test_video(tmp_path / "a.mp4")
    st = a.stat()
    files = [str(a)]
    existing = {str(a): (st.st_size, st.st_mtime)}
    need, stale = diff_scan(files, existing)
    assert need == [], "unchanged file alone is skipped"

    need, _ = diff_scan(files, existing, missing_meta={str(a)})
    assert need == [str(a)], "missing-metadata rows must re-probe even when unchanged"
    need, _ = diff_scan(files, existing, missing_meta={str(tmp_path / "nope.mp4")})
    assert need == [], "missing_meta paths not on disk are ignored"


def test_repair_thread_fixes_probeable_row(qapp, tmp_path):
    from ui.main_window import _MetadataRepairThread

    real = _make_test_video(tmp_path / "real.mp4")
    r = Repository(tmp_path / "repair.db")
    try:
        r.upsert_videos([_mk("real.mp4", str(real))])  # NULL metadata
        t = _MetadataRepairThread(r)
        t.run()
        v = r.get_by_path(str(real))
        assert v.duration is not None and v.duration > 0
        assert t.fixed == 1 and t.probed == 1
    finally:
        r.close()


def test_repair_thread_never_wipes_partial_metadata(qapp, tmp_path):
    """A row with duration but NULL resolution/codec must survive the repair
    pass untouched (its file may be unreachable; re-probing would destroy
    the good data)."""
    from ui.main_window import _MetadataRepairThread

    r = Repository(tmp_path / "repair.db")
    try:
        ghost = str(tmp_path / "ghost.mp4")  # does not exist on disk
        r.upsert_videos([_mk("ghost.mp4", ghost, duration=100.0)])
        t = _MetadataRepairThread(r)
        t.run()
        v = r.get_by_path(ghost)
        assert v.duration == 100.0, "partial metadata must not be clobbered"
        assert t.probed == 0 and t.fixed == 0
    finally:
        r.close()


def test_repair_thread_marks_broken_row_cooldown(qapp, tmp_path):
    from ui.main_window import _MetadataRepairThread

    r = Repository(tmp_path / "repair.db")
    try:
        broken = str(tmp_path / "missing.mp4")
        r.upsert_videos([_mk("missing.mp4", broken)])
        t = _MetadataRepairThread(r)
        t.run()
        assert t.fixed == 0
        assert r.missing_metadata_files() != []
        now = time.time()
        assert r.missing_metadata_files(cutoff=now + 1) != [], (
            "cooldown is a minimum age, newer retries are excluded"
        )
        assert r.missing_metadata_files(cutoff=now - 1) == [], (
            "the fresh stamp must prevent an immediate retry"
        )
    finally:
        r.close()


def test_repair_runs_at_startup(qapp, tmp_path, monkeypatch):
    """MainWindow must kick the repair thread on launch and the fixed row
    must show metadata afterwards."""
    import config

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "videolib.db")
    monkeypatch.setattr(config, "THUMBS_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")

    repo = Repository(tmp_path / "videolib.db")
    real = _make_test_video(tmp_path / "real.mp4")
    repo.upsert_videos([_mk("real.mp4", str(real))])

    from ui.main_window import MainWindow

    w = MainWindow(repo)
    w.show()
    try:
        qapp.processEvents()
        thread = w._repair_thread
        assert thread is not None, "repair timer must have started the thread"
        assert thread.wait(10000), "repair thread must finish promptly"
        qapp.processEvents()
        v = repo.get_by_path(str(real))
        assert v.duration is not None and v.duration > 0
    finally:
        w.close()
        repo.close()
