from pathlib import Path

import pytest

from domain.models import Video
from domain.repository import Repository
from services.metadata import build_video, probe
from services.scanner import scan_directory
from services.thumbnailer import Thumbnailer
from tests.helpers import make_test_video, wait_for


@pytest.fixture()
def video_dir(tmp_path):
    return make_test_video(tmp_path / "sample" / "movie.mp4", seconds=1)


def test_scan_directory(video_dir, tmp_path):
    files = scan_directory(str(tmp_path))
    assert len(files) == 1
    assert files[0].endswith("movie.mp4")


def test_scan_directory_ignores_non_video(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    assert scan_directory(str(tmp_path)) == []


def test_scan_directory_finds_bin(tmp_path):
    """Extension-only enumeration must see .bin; the content filter runs later."""
    real = make_test_video(tmp_path / "movie.mp4").rename(tmp_path / "movie.bin")
    garbage = tmp_path / "fake.bin"
    garbage.write_bytes(b"not a video")
    assert set(scan_directory(str(tmp_path))) == {str(real), str(garbage)}


def test_diff_scan(tmp_path):
    from services.scanner import diff_scan

    a = make_test_video(tmp_path / "a.mp4")
    b = make_test_video(tmp_path / "b.mp4")
    st_a = a.stat()
    st_b = b.stat()
    existing = {
        str(a): (st_a.st_size, st_a.st_mtime),
        str(b): (st_b.st_size, st_b.st_mtime),
        str(tmp_path / "gone.mp4"): (1, 1.0),
    }
    files = [str(a), str(b), str(tmp_path / "c.mp4")]
    need, stale = diff_scan(files, existing)
    assert need == [str(tmp_path / "c.mp4")]
    assert stale == [str(tmp_path / "gone.mp4")]
    changed, _ = diff_scan(files, {str(a): (st_a.st_size + 100, st_a.st_mtime)})
    assert str(a) in changed


def test_probe_returns_metadata(video_dir):
    duration, resolution, codec = probe(str(video_dir))
    assert duration is not None and duration > 0
    assert resolution == "64x48"
    assert codec == "mpeg4"


def test_build_video(video_dir):
    v = build_video(str(video_dir))
    assert isinstance(v, Video)
    assert v.filename == "movie.mp4"
    assert v.duration > 0
    assert v.file_size > 0


def test_thumbnail_generation(video_dir, tmp_path):
    from services.thumbnailer import THUMB_PIXEL_HEIGHT, THUMB_PIXEL_WIDTH

    thumbs = Thumbnailer(tmp_path / "thumbs")
    ok = thumbs.ensure(str(video_dir), video_id=1)
    assert ok
    assert thumbs.path_for(1).exists()
    with open(thumbs.path_for(1), "rb") as f:
        assert f.read(2) == b"\xff\xd8"  # JPEG magic
    import av

    with av.open(str(thumbs.path_for(1))) as c:
        stream = c.streams.video[0]
        assert stream.width == THUMB_PIXEL_WIDTH
        assert stream.height == THUMB_PIXEL_HEIGHT


def test_thumbnail_legacy_1x_regenerates(video_dir, tmp_path):
    """Thumbnails made before the HiDPI change (170x96) must be regenerated
    at 2x on the next scheduled task."""
    import av

    from services.thumbnailer import THUMB_HEIGHT, THUMB_PIXEL_WIDTH, THUMB_WIDTH

    thumbs = Thumbnailer(tmp_path / "thumbs")
    assert thumbs.ensure(str(video_dir), video_id=7) is True
    assert thumbs.path_for(7).stat().st_size > 0

    # build a genuine 1x thumbnail (as old installs produced)
    with av.open(str(thumbs.path_for(7))) as c:
        frame = next(c.decode(video=0))
    legacy = frame.reformat(
        width=THUMB_WIDTH, height=THUMB_HEIGHT, format="yuv420p"
    )
    legacy_path = tmp_path / "legacy.jpg"
    with av.open(str(legacy_path), "w") as out:
        jpeg = out.add_stream("mjpeg")
        jpeg.width = legacy.width
        jpeg.height = legacy.height
        jpeg.pix_fmt = "yuvj420p"
        for packet in jpeg.encode(legacy):
            out.mux(packet)
        for packet in jpeg.encode():
            out.mux(packet)
    legacy_path.replace(thumbs.path_for(7))
    with av.open(str(thumbs.path_for(7))) as c:
        assert c.streams.video[0].width == THUMB_WIDTH, "precondition: 1x in place"

    assert thumbs.ensure(str(video_dir), video_id=7) is True
    with av.open(str(thumbs.path_for(7))) as c:
        assert c.streams.video[0].width == THUMB_PIXEL_WIDTH, "1x must be regenerated"


def test_thumbnail_crop_fills_cell(tmp_path):
    """A 2:1 wide source must still produce a filled 170x96 thumbnail."""
    import av

    from services.thumbnailer import THUMB_HEIGHT, THUMB_WIDTH, _center_crop

    frame = av.VideoFrame(128, 64, "yuv420p")  # 2:1
    cropped = _center_crop(frame)
    assert cropped.height == 64
    assert abs(cropped.width / cropped.height - THUMB_WIDTH / THUMB_HEIGHT) < 0.05

    frame = av.VideoFrame(64, 128, "yuv420p")  # 1:2 portrait
    cropped = _center_crop(frame)
    assert cropped.width == 64
    assert abs(cropped.width / cropped.height - THUMB_WIDTH / THUMB_HEIGHT) < 0.05


def test_thumbnail_failure_logged(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "APP_DIR", tmp_path / "appdata")
    thumbs = Thumbnailer(tmp_path / "thumbs")
    corrupt = tmp_path / "broken.mp4"
    corrupt.write_bytes(b"this is not a video")
    ok = thumbs.ensure(str(corrupt), video_id=99)
    assert ok is False
    assert not thumbs.path_for(99).exists()
    log = tmp_path / "appdata" / "thumbnails.log"
    assert log.exists()
    assert str(corrupt) in log.read_text(encoding="utf-8")


def test_thumbnail_persisted_to_repo(video_dir, tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    repo.upsert_videos([build_video(str(video_dir))])
    v = repo.get_by_path(str(video_dir))
    thumbs = Thumbnailer(tmp_path / "thumbs")
    thumbs.ensure(str(video_dir), v.id, repo=repo)
    assert repo.get_video(v.id).thumb_path is not None
    repo.close()


def test_thumbnail_failure_keeps_existing_good_thumb(video_dir, tmp_path, monkeypatch):
    """A failed regeneration must leave the previous good thumbnail in place:
    the write goes to a .tmp file first, so a mid-write failure can never
    leave a truncated/black {id}.jpg that would stick forever."""
    import os as _os

    from services import thumbnailer as T

    thumbs = Thumbnailer(tmp_path / "thumbs")
    assert thumbs.ensure(str(video_dir), video_id=5) is True
    good = thumbs.path_for(5).read_bytes()

    def boom_replace(src, dst):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(_os, "replace", boom_replace)
    assert T._extract_frame(str(video_dir), thumbs.path_for(5)) is False
    assert thumbs.path_for(5).read_bytes() == good, "old thumb must survive a failed regen"
    assert not list((tmp_path / "thumbs").glob("*.jpg.tmp")), "tmp must be swept on failure"

    # and with no prior thumb, a failed generation leaves neither file
    monkeypatch.setattr(_os, "replace", boom_replace)
    assert T._extract_frame(str(video_dir), thumbs.path_for(8)) is False
    assert not thumbs.path_for(8).exists()
    assert not list((tmp_path / "thumbs").glob("*.jpg.tmp"))


def test_ensure_dedups_across_instances(video_dir, tmp_path, monkeypatch):
    """The UI builds a fresh Thumbnailer per background task; the in-flight
    guard must therefore be global, or two tasks for the same video id would
    open the same {id}.jpg for writing concurrently and corrupt it."""
    import threading

    from services import thumbnailer as T

    started = threading.Event()
    release = threading.Event()
    real_extract = T._extract_frame

    def slow_extract(filepath, thumb):
        started.set()
        assert release.wait(5)
        return real_extract(filepath, thumb)

    monkeypatch.setattr(T, "_extract_frame", slow_extract)
    t = threading.Thread(
        target=lambda: T.Thumbnailer(tmp_path / "a").ensure(str(video_dir), 42)
    )
    t.start()
    assert started.wait(5)
    try:
        second = T.Thumbnailer(tmp_path / "b").ensure(str(video_dir), 42)
        assert second is False, "an in-flight id must be rejected across instances"
    finally:
        release.set()
        t.join(5)
    assert not t.is_alive()
    assert T.Thumbnailer(tmp_path / "a").path_for(42).exists()


def test_thumbnail_seek_fraction_within_range(video_dir, tmp_path, monkeypatch):
    """The sampled frame position must lie inside [10%, 90%] of the video."""
    from services import thumbnailer as T

    calls = []

    def fake_uniform(lo, hi):
        calls.append((lo, hi))
        return 0.5

    monkeypatch.setattr(T.random, "uniform", fake_uniform)
    thumbs = Thumbnailer(tmp_path / "thumbs")
    assert thumbs.ensure(str(video_dir), video_id=9) is True
    assert calls, "random.uniform must be consulted for the seek position"
    lo, hi = calls[0]
    assert lo == T.THUMB_POS_MIN
    assert hi == T.THUMB_POS_MAX
    assert thumbs.path_for(9).exists()


def test_thumbnail_no_repeat_work(video_dir, tmp_path):
    thumbs = Thumbnailer(tmp_path / "thumbs")
    assert thumbs.ensure(str(video_dir), 1) is True
    assert thumbs.ensure(str(video_dir), 1) is True


def test_thumbnail_zero_byte_regenerates(video_dir, tmp_path):
    thumbs = Thumbnailer(tmp_path / "thumbs")
    assert thumbs.ensure(str(video_dir), 1) is True
    thumbs.path_for(1).write_bytes(b"")  # corrupt it
    assert thumbs.ensure(str(video_dir), 1) is True, "zero-byte thumbs must regenerate"
    assert thumbs.path_for(1).stat().st_size > 0


def test_cleanup_orphans_removes_zero_byte_and_orphans(tmp_path):
    from services.thumbnailer import Thumbnailer

    d = tmp_path / "thumbs"
    d.mkdir()
    (d / "1.jpg").write_bytes(b"\xff\xd8data")
    (d / "2.jpg").write_bytes(b"")  # zero-byte: corrupt, must go
    (d / "3.jpg").write_bytes(b"x")
    (d / "junk.txt").write_text("keep")
    (d / "abc.jpg").write_bytes(b"x")  # non-numeric stem: kept

    removed = Thumbnailer.cleanup_orphans(d, valid_ids={1, 3})
    assert removed == 1, "only the zero-byte file is removed (2 and 3 are valid)"
    assert (d / "1.jpg").exists()
    assert not (d / "2.jpg").exists()
    assert (d / "3.jpg").exists()
    assert (d / "junk.txt").exists()
    assert (d / "abc.jpg").exists()

    removed = Thumbnailer.cleanup_orphans(d, valid_ids={1})
    assert removed == 1, "orphan 3.jpg must be removed now"

    (d / "9.jpg.tmp").write_bytes(b"partial")  # leftover from interrupted write
    removed = Thumbnailer.cleanup_orphans(d, valid_ids={1})
    assert removed == 1, "stale .tmp files must be swept"
    assert not (d / "9.jpg.tmp").exists()


def test_delete_for_removes_only_target_thumbs(tmp_path):
    thumbs = Thumbnailer(tmp_path / "thumbs")
    for vid in (1, 2):
        thumbs.path_for(vid).write_bytes(b"x")
    (tmp_path / "thumbs" / "junk.txt").write_text("keep me")
    assert thumbs.delete_for([1, 999]) == 1  # 999 has no file
    assert not thumbs.path_for(1).exists()
    assert thumbs.path_for(2).exists()
    assert (tmp_path / "thumbs" / "junk.txt").exists()


def test_thumb_not_reused_after_data_removed(video_dir, tmp_path):
    """Regression: deleting a root's data must delete its thumbnails, since
    SQLite reuses row ids and a stale {id}.jpg would show on a new video."""
    repo = Repository(tmp_path / "db.sqlite")
    repo.upsert_videos([build_video(str(video_dir))])
    old = repo.get_by_path(str(video_dir))
    thumbs = Thumbnailer(tmp_path / "thumbs")
    assert thumbs.ensure(str(video_dir), old.id) is True

    deleted = repo.remove_by_filepaths([str(video_dir)])
    assert deleted == [old.id]
    thumbs.delete_for(deleted)
    assert not thumbs.path_for(old.id).exists()

    repo.upsert_videos([build_video(str(video_dir))])
    new = repo.get_by_path(str(video_dir))
    assert new.id == old.id, "SQLite reuses row ids - stale thumbs would be hit"
    assert not thumbs.path_for(new.id).exists()
    repo.close()


def test_scan_worker_completes_and_cleans_stale(tmp_path):
    from ui.scan_worker import ScanWorker

    repo = Repository(tmp_path / "db.sqlite")
    root = tmp_path / "root"
    root.mkdir()
    stale_file = make_test_video(root / "gone" / "old.mp4")
    repo.upsert_videos([build_video(str(stale_file))])
    stale_file.unlink()
    keep_file = make_test_video(tmp_path / "other" / "keep.mp4")
    repo.upsert_videos([build_video(str(keep_file))])
    for i in range(2):
        make_test_video(root / f"v{i}.mp4")
    worker = ScanWorker(str(root), repo)
    worker.start()
    assert wait_for(lambda: worker.isFinished()), "scan worker did not finish"
    assert repo.get_by_path(str(stale_file)) is None, "stale under root must be removed"
    assert repo.get_by_path(str(keep_file)) is not None, "other root must be preserved"
    assert repo.count() == 3
    repo.close()


def test_scan_worker_cancel_keeps_stale(tmp_path, monkeypatch):
    import time

    from ui.scan_worker import ScanWorker

    repo = Repository(tmp_path / "db.sqlite")
    stale_file = make_test_video(tmp_path / "old" / "old.mp4")
    repo.upsert_videos([build_video(str(stale_file))])
    root = tmp_path / "root"
    root.mkdir()
    for i in range(20):
        make_test_video(root / f"v{i}.mp4")

    def slow_build(fp):
        time.sleep(0.1)
        return build_video(fp)

    monkeypatch.setattr("services.library.build_video", slow_build)
    worker = ScanWorker(str(root), repo)
    worker.start()
    time.sleep(0.3)
    worker.cancel()
    assert wait_for(lambda: worker.isFinished()), "canceled worker did not stop"
    assert repo.get_by_path(str(stale_file)) is not None, "cancel must not remove stale rows"
    assert repo.count() < 21, "cancel did not stop processing"
    repo.close()


def test_scan_worker_keeps_other_root_data(tmp_path):
    """Scanning root B must not wipe root A's videos, favorites or categories."""
    import os

    from ui.scan_worker import ScanWorker

    repo = Repository(tmp_path / "db.sqlite")
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    root_a.mkdir()
    root_b.mkdir()
    va = make_test_video(root_a / "va.mp4")
    repo.upsert_videos([build_video(str(va))])
    a = repo.get_by_path(str(va))
    lst = repo.create_favorite_list("收藏夹_测试")
    repo.add_favorite(a.id, lst.id)
    cat = repo.add_category("我的分类", root=str(root_a))
    repo.assign_category(a.id, cat.id)

    vb = make_test_video(root_b / "vb.mp4")
    worker = ScanWorker(str(root_b), repo)
    worker.start()
    assert wait_for(lambda: worker.isFinished()), "scan worker did not finish"
    assert repo.get_by_path(str(va)) is not None, "other root's video was wiped"
    assert repo.is_favorite(a.id, lst.id), "favorite was wiped"
    assert [c.id for c in repo.categories_of_video(a.id)] == [cat.id], "category was wiped"
    assert repo.get_by_path(str(vb)) is not None
    assert repo.get_categories(str(root_b)) == [], "root B should start with no categories"
    assert {v.filename for v in repo.videos_in_root(str(root_a))} == {"va.mp4"}
    assert {v.filename for v in repo.videos_in_root(str(root_b))} == {"vb.mp4"}
    assert os.path.normpath(str(root_b)) in repo.get_scan_roots()
    repo.close()


def test_scan_worker_incremental_skips_unchanged(tmp_path, monkeypatch):
    import os

    from ui.scan_worker import ScanWorker

    repo = Repository(tmp_path / "db.sqlite")
    root = tmp_path / "root"
    root.mkdir()
    f1 = make_test_video(root / "v1.mp4")
    f2 = make_test_video(root / "v2.mp4")
    probe_count = {"n": 0}
    real_build = build_video

    def counting_build(fp):
        probe_count["n"] += 1
        return real_build(fp)

    monkeypatch.setattr("services.library.build_video", counting_build)

    worker = ScanWorker(str(root), repo)
    worker.start()
    assert wait_for(lambda: worker.isFinished())
    assert probe_count["n"] == 2, "first scan must probe everything"

    worker = ScanWorker(str(root), repo)
    worker.start()
    assert wait_for(lambda: worker.isFinished())
    assert probe_count["n"] == 2, "unchanged files must be skipped"

    st = f2.stat()
    os.utime(f2, (st.st_atime, st.st_mtime + 5.0))
    worker = ScanWorker(str(root), repo)
    worker.start()
    assert wait_for(lambda: worker.isFinished())
    assert probe_count["n"] == 3, "only the modified file must be re-probed"
    repo.close()


def test_scan_worker_empty_dir_not_registered(tmp_path):
    """Scanning a directory with no video files must not touch the DB."""
    from ui.scan_worker import ScanWorker

    repo = Repository(tmp_path / "db.sqlite")
    root = tmp_path / "empty"
    root.mkdir()
    (root / "notes.txt").write_text("not a video")
    keep_file = make_test_video(tmp_path / "other" / "keep.mp4")
    repo.upsert_videos([build_video(str(keep_file))])

    worker = ScanWorker(str(root), repo)
    worker.start()
    assert wait_for(lambda: worker.isFinished()), "scan worker did not finish"
    assert repo.get_scan_roots() == [], "empty dir must not be registered"
    assert repo.get_by_path(str(keep_file)) is not None, "existing data must be untouched"
    assert repo.count() == 1
    repo.close()
