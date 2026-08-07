import sqlite3
import time
from pathlib import Path

import pytest

from domain.repository import Repository
from services.metadata import build_video
from services.thumbnailer import Thumbnailer
from tests.helpers import make_test_video, wait_for
from ui.delete_worker import DeleteWorker


def _thumb(thumbs_dir: Path, video_id: int) -> Path:
    return Thumbnailer(thumbs_dir).path_for(video_id)


def _drain(qapp) -> None:
    """Deliver queued cross-thread signal calls to their slots."""
    for _ in range(50):
        qapp.processEvents()


def _setup(tmp_path, repo, n=3):
    root = tmp_path / "root"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    files = [make_test_video(root / f"v{i}.mp4") for i in range(n)]
    keep = make_test_video(other / "keep.mp4")
    repo.upsert_videos([build_video(str(f)) for f in files] + [build_video(str(keep))])
    repo.register_scan(str(root))
    repo.register_scan(str(other))
    videos = [repo.get_by_path(str(f)) for f in files]
    thumbs_dir = tmp_path / "thumbs"
    for v in videos:
        _thumb(thumbs_dir, v.id).write_bytes(b"x")
    return root, other, files, keep, thumbs_dir, videos


def test_delete_worker_completes(qapp, tmp_path, repo):
    root, other, files, keep, thumbs_dir, videos = _setup(tmp_path, repo)
    events: list[tuple[int, bool]] = []
    worker = DeleteWorker(str(root), repo, thumbs_dir=thumbs_dir)
    worker.done.connect(lambda deleted, removed: events.append((deleted, removed)))
    worker.start()
    assert wait_for(lambda: worker.isFinished()), "worker did not finish"
    _drain(qapp)
    assert events == [(3, True)]
    assert repo.get_by_path(str(files[0])) is None
    assert repo.get_by_path(str(keep)) is not None, "other root must survive"
    assert str(root) not in repo.get_scan_roots()
    assert str(other) in repo.get_scan_roots()
    for v in videos:
        assert not _thumb(thumbs_dir, v.id).exists(), "thumb must die with its row"

    import config

    assert list(config.BACKUPS_DIR.glob("videolib-*.db")), "must snapshot first"


def test_delete_worker_cancel_keeps_root_for_retry(qapp, tmp_path, repo, monkeypatch):
    root, other, files, keep, thumbs_dir, videos = _setup(tmp_path, repo)
    import ui.delete_worker as dw

    holder: dict = {}

    def fake_backup(repo, force):
        holder["worker"].cancel()

    monkeypatch.setattr(dw, "backup_db", fake_backup)
    events: list[tuple[int, bool]] = []
    worker = DeleteWorker(str(root), repo, thumbs_dir=thumbs_dir)
    holder["worker"] = worker
    worker.done.connect(lambda deleted, removed: events.append((deleted, removed)))
    worker.start()
    assert wait_for(lambda: worker.isFinished()), "worker did not finish"
    _drain(qapp)
    assert events == [(3, False)]
    assert repo.get_by_path(str(files[0])) is None
    assert str(root) in repo.get_scan_roots(), "root must stay selectable after cancel"
    assert _thumb(thumbs_dir, videos[0].id).exists(), "thumbs must survive cancel"


def test_delete_worker_reports_progress(qapp, tmp_path, repo):
    root, other, files, keep, thumbs_dir, videos = _setup(tmp_path, repo)
    progress: list[tuple[int, int]] = []
    worker = DeleteWorker(str(root), repo, thumbs_dir=thumbs_dir)
    worker.progress.connect(lambda done, total, fp: progress.append((done, total)))
    worker.start()
    assert wait_for(lambda: worker.isFinished()), "worker did not finish"
    _drain(qapp)
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_delete_worker_clear_all_mode(qapp, tmp_path, repo):
    root, other, files, keep, thumbs_dir, videos = _setup(tmp_path, repo)
    events: list[tuple[int, bool]] = []
    worker = DeleteWorker(None, repo, thumbs_dir=thumbs_dir)
    worker.done.connect(lambda deleted, removed: events.append((deleted, removed)))
    worker.start()
    assert wait_for(lambda: worker.isFinished()), "worker did not finish"
    _drain(qapp)
    assert events == [(4, False)], "clear-all deletes every row, never a scan root"
    assert repo.count() == 0
    assert str(root) in repo.get_scan_roots(), "history must survive clear-all"
    assert str(other) in repo.get_scan_roots()
    for v in videos:
        assert not _thumb(thumbs_dir, v.id).exists(), "thumb must die with its row"


def test_clear_all_videos_keeps_roots_and_cascades(tmp_path):
    """clear_all_videos drops rows + FTS + cascaded links, keeps scan roots
    and the category tree."""
    db = tmp_path / "db.sqlite"
    r = Repository(db)
    f1 = make_test_video(tmp_path / "a.mp4")
    f2 = make_test_video(tmp_path / "b.mp4")
    r.upsert_videos([build_video(str(f1)), build_video(str(f2))])
    r.register_scan(str(tmp_path))
    v1 = r.get_by_path(str(f1))
    r.record_play(v1.id, 5.0)
    fl = r.create_favorite_list("收藏夹_一")
    r.add_favorite(v1.id, fl.id)
    cat = r.add_category("分类甲", root=str(tmp_path))
    r.assign_category(v1.id, cat.id)

    ids = r.clear_all_videos()
    assert len(ids) == 2
    assert r.count() == 0
    assert r.get_scan_roots() == [str(tmp_path)], "history must survive"
    assert r.recent_plays() == [], "play history must cascade away"
    assert r.count_favorites(fl.id) == 0, "favorite links must cascade away"
    assert r.videos_in_category(cat.id) == [], "category links must cascade away"
    with r._lock:
        fts = r._conn.execute("SELECT COUNT(*) AS c FROM videos_fts").fetchone()["c"]
        cats = r._conn.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]
    assert fts == 0, "FTS must be rebuilt to empty"
    assert cats == 1, "category tree is kept"
    assert r.search("a", limit=10) == []
    r.close()


def test_delete_for_progress_and_cancel(tmp_path):
    thumbs_dir = tmp_path / "thumbs"
    ids = [10, 20, 30]
    for i in ids:
        _thumb(thumbs_dir, i).write_bytes(b"x")

    t = Thumbnailer(thumbs_dir)
    progress: list[tuple[int, int]] = []
    assert t.delete_for(ids, progress=lambda d, total: progress.append((d, total))) == 3
    assert progress == [(1, 3), (2, 3), (3, 3)]

    for i in ids:
        _thumb(thumbs_dir, i).write_bytes(b"x")
    progress.clear()
    removed = t.delete_for(
        ids,
        progress=lambda d, total: progress.append((d, total)),
        should_cancel=lambda: len(progress) >= 1,
    )
    assert removed == 1, "loop must stop once should_cancel turns true"
    assert not _thumb(thumbs_dir, 10).exists()
    assert _thumb(thumbs_dir, 20).exists()


def test_fts_heal_on_open(tmp_path):
    """A kill between row deletion and FTS rebuild leaves a stale index;
    opening the repo must rebuild it so search shows no ghosts."""
    db = tmp_path / "db.sqlite"
    r = Repository(db)
    f1 = make_test_video(tmp_path / "a.mp4")
    f2 = make_test_video(tmp_path / "b.mp4")
    r.upsert_videos([build_video(str(f1)), build_video(str(f2))])
    r.close()

    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM videos WHERE id = (SELECT MIN(id) FROM videos)")
    conn.commit()
    conn.close()

    r2 = Repository(db)
    try:
        with r2._lock:
            fts = r2._conn.execute("SELECT COUNT(*) AS c FROM videos_fts").fetchone()["c"]
        assert fts == 1, "stale index must be rebuilt on open"
        assert r2.search("b", limit=10), "survivor must stay findable"
    finally:
        r2.close()
