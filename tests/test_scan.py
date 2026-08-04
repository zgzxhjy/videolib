import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.models import Video
from domain.repository import Repository
from services.metadata import build_video, probe
from services.scanner import scan_directory
from services.thumbnailer import Thumbnailer


def _make_test_video(path: Path, seconds: int = 1) -> Path:
    """Generate a tiny real video file using PyAV (mpeg4, 64x48)."""
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


@pytest.fixture()
def video_dir(tmp_path):
    return _make_test_video(tmp_path / "sample" / "movie.mp4", seconds=1)


def test_scan_directory(video_dir, tmp_path):
    files = scan_directory(str(tmp_path))
    assert len(files) == 1
    assert files[0].endswith("movie.mp4")


def test_scan_directory_ignores_non_video(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    assert scan_directory(str(tmp_path)) == []


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
    from services.thumbnailer import THUMB_HEIGHT, THUMB_WIDTH

    thumbs = Thumbnailer(tmp_path / "thumbs")
    ok = thumbs.ensure(str(video_dir), video_id=1)
    assert ok
    assert thumbs.path_for(1).exists()
    with open(thumbs.path_for(1), "rb") as f:
        assert f.read(2) == b"\xff\xd8"  # JPEG magic
    import av

    with av.open(str(thumbs.path_for(1))) as c:
        stream = c.streams.video[0]
        assert stream.width == THUMB_WIDTH
        assert stream.height == THUMB_HEIGHT


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


def test_thumbnail_no_repeat_work(video_dir, tmp_path):
    thumbs = Thumbnailer(tmp_path / "thumbs")
    assert thumbs.ensure(str(video_dir), 1) is True
    assert thumbs.ensure(str(video_dir), 1) is True


def _wait_for(condition, timeout=20.0, interval=0.05):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def test_scan_worker_completes_and_cleans_stale(tmp_path):
    from ui.scan_worker import ScanWorker

    repo = Repository(tmp_path / "db.sqlite")
    stale_file = _make_test_video(tmp_path / "old" / "old.mp4")
    repo.upsert_videos([build_video(str(stale_file))])
    root = tmp_path / "root"
    root.mkdir()
    for i in range(2):
        _make_test_video(root / f"v{i}.mp4")
    worker = ScanWorker(str(root), repo)
    worker.start()
    assert _wait_for(lambda: worker.isFinished()), "scan worker did not finish"
    assert repo.count() == 2
    assert repo.get_by_path(str(stale_file)) is None
    repo.close()


def test_scan_worker_cancel_keeps_stale(tmp_path, monkeypatch):
    import time

    from ui.scan_worker import ScanWorker

    repo = Repository(tmp_path / "db.sqlite")
    stale_file = _make_test_video(tmp_path / "old" / "old.mp4")
    repo.upsert_videos([build_video(str(stale_file))])
    root = tmp_path / "root"
    root.mkdir()
    for i in range(20):
        _make_test_video(root / f"v{i}.mp4")

    def slow_build(fp):
        time.sleep(0.1)
        return build_video(fp)

    monkeypatch.setattr("ui.scan_worker.build_video", slow_build)
    worker = ScanWorker(str(root), repo)
    worker.start()
    time.sleep(0.3)
    worker.cancel()
    assert _wait_for(lambda: worker.isFinished()), "canceled worker did not stop"
    assert repo.get_by_path(str(stale_file)) is not None, "cancel must not remove stale rows"
    assert repo.count() < 21, "cancel did not stop processing"
    repo.close()
