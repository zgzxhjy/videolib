import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.repository import Repository
from services.library import Library
from services.metadata import build_video
from services.scanner import diff_scan, scan_directory
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


def _make_test_bin_video(path: Path) -> Path:
    """A real video container carrying the ambiguous .bin name (content
    sniffing must look past the extension)."""
    return _make_test_video(path.with_suffix(".mp4")).rename(path)


@pytest.fixture()
def repo(tmp_path):
    r = Repository(tmp_path / "db.sqlite")
    yield r
    r.close()


def _thumb(thumbs_dir: Path, video_id: int) -> Path:
    return Thumbnailer(thumbs_dir).path_for(video_id)


def test_apply_sync_probes_new_files_and_reports_counts(tmp_path, repo):
    root = tmp_path / "root"
    root.mkdir()
    f = _make_test_video(root / "a.mp4")
    lib = Library(repo, tmp_path / "thumbs")
    progress: list[tuple] = []

    result = lib.apply_sync([str(f)], [], progress=lambda d, t, fp: progress.append((d, t, fp)))

    assert result.probed == 1
    assert result.changed == 1
    assert result.removed == 0
    assert not result.canceled
    assert progress == [(1, 1, str(f))]
    assert repo.get_by_path(str(f)) is not None


def test_apply_sync_skips_probe_when_canceled(tmp_path, repo):
    root = tmp_path / "root"
    root.mkdir()
    stale_file = _make_test_video(root / "old.mp4")
    repo.upsert_videos([build_video(str(stale_file))])
    new_file = _make_test_video(root / "new.mp4")

    lib = Library(repo, tmp_path / "thumbs")
    result = lib.apply_sync(
        [str(new_file)], [str(stale_file)], should_cancel=lambda: True
    )

    assert result.canceled
    assert result.probed == 0
    assert result.removed == 0, "cancel must skip removals"
    assert repo.get_by_path(str(stale_file)) is not None, "stale rows must survive cancel"


def test_apply_sync_cancel_midway_keeps_partial_batch(tmp_path, repo):
    root = tmp_path / "root"
    root.mkdir()
    files = [_make_test_video(root / f"v{i}.mp4") for i in range(3)]
    lib = Library(repo, tmp_path / "thumbs")
    calls = {"n": 0}

    def cancel_after_one():
        calls["n"] += 1
        return calls["n"] >= 2

    result = lib.apply_sync([str(f) for f in files], [], should_cancel=cancel_after_one)

    assert result.canceled
    assert result.probed == 1
    assert repo.count() == 1, "partial batch must still be upserted"


def test_apply_sync_removals_delete_rows_and_thumbs(tmp_path, repo):
    root = tmp_path / "root"
    root.mkdir()
    f = _make_test_video(root / "a.mp4")
    repo.upsert_videos([build_video(str(f))])
    v = repo.get_by_path(str(f))
    thumbs_dir = tmp_path / "thumbs"
    _thumb(thumbs_dir, v.id).write_bytes(b"thumb")

    lib = Library(repo, thumbs_dir)
    result = lib.apply_sync([], [str(f)])

    assert result.removed == 1
    assert repo.get_by_path(str(f)) is None
    assert not _thumb(thumbs_dir, v.id).exists(), "thumb must die with its row"


def test_remove_paths_deletes_rows_and_thumbs(tmp_path, repo):
    root = tmp_path / "root"
    root.mkdir()
    f = _make_test_video(root / "a.mp4")
    repo.upsert_videos([build_video(str(f))])
    v = repo.get_by_path(str(f))
    thumbs_dir = tmp_path / "thumbs"
    _thumb(thumbs_dir, v.id).write_bytes(b"thumb")

    lib = Library(repo, thumbs_dir)
    assert lib.remove_paths([str(f)]) == 1
    assert repo.count() == 0
    assert not _thumb(thumbs_dir, v.id).exists()

    import config

    files = list(config.BACKUPS_DIR.glob("videolib-*.db"))
    assert files, "remove_paths must snapshot the db first"


def test_remove_root_deletes_rows_and_thumbs_under_root(tmp_path, repo):
    root = tmp_path / "root"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    fa = _make_test_video(root / "a.mp4")
    fb = _make_test_video(other / "b.mp4")
    repo.upsert_videos([build_video(str(fa)), build_video(str(fb))])
    va = repo.get_by_path(str(fa))
    vb = repo.get_by_path(str(fb))
    thumbs_dir = tmp_path / "thumbs"
    _thumb(thumbs_dir, va.id).write_bytes(b"thumb")
    _thumb(thumbs_dir, vb.id).write_bytes(b"thumb")

    lib = Library(repo, thumbs_dir)
    assert lib.remove_root(str(root)) == 1
    assert repo.get_by_path(str(fa)) is None
    assert repo.get_by_path(str(fb)) is not None, "other root must survive"
    assert not _thumb(thumbs_dir, va.id).exists()
    assert _thumb(thumbs_dir, vb.id).exists()


def test_end_to_end_scan_through_library(tmp_path, repo):
    """The full scan pipeline (enumerate + diff + sync) as the CLI runs it."""
    root = tmp_path / "root"
    root.mkdir()
    stale_file = _make_test_video(root / "gone" / "old.mp4")
    repo.upsert_videos([build_video(str(stale_file))])
    stale_file.unlink()
    files = [_make_test_video(root / f"v{i}.mp4") for i in range(2)]

    need_probe, stale = diff_scan(
        scan_directory(str(root)), repo.existing_under(str(root))
    )
    result = Library(repo, tmp_path / "thumbs").apply_sync(need_probe, stale)

    assert result.probed == 2
    assert result.removed == 1
    assert not result.canceled
    assert repo.count() == 2


def test_bin_with_real_video_content_is_indexed(tmp_path, repo):
    """A .bin that really carries a video stream must be indexed."""
    f = _make_test_bin_video(tmp_path / "movie.bin")
    lib = Library(repo, tmp_path / "thumbs")

    result = lib.apply_sync([str(f)], [])

    assert result.probed == 1
    v = repo.get_by_path(str(f))
    assert v is not None
    assert v.codec == "mpeg4"


def test_bin_without_video_stream_is_skipped(tmp_path, repo):
    """Garbage and audio-only .bin files must be sniffed out of the library."""
    import av

    garbage = tmp_path / "fake.bin"
    garbage.write_bytes(b"this is definitely not a video")
    audio = tmp_path / "audio.bin"
    with av.open(str(audio), "w", format="mpeg") as container:
        stream = container.add_stream("mp2", rate=44100)
        frame = av.AudioFrame(format="s16", layout="stereo", samples=4410)
        frame.sample_rate = 44100
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    lib = Library(repo, tmp_path / "thumbs")
    result = lib.apply_sync([str(garbage), str(audio)], [])

    assert result.probed == 0
    assert repo.count() == 0
    assert repo.get_by_path(str(garbage)) is None
    assert repo.get_by_path(str(audio)) is None


def test_bin_sniff_does_not_affect_normal_extensions(tmp_path, repo):
    """Corrupt .mp4 keeps pre-existing behavior (indexed with empty metadata)."""
    f = tmp_path / "broken.mp4"
    f.write_bytes(b"this is not a video")
    lib = Library(repo, tmp_path / "thumbs")

    result = lib.apply_sync([str(f)], [])

    assert result.probed == 1
    v = repo.get_by_path(str(f))
    assert v is not None
    assert v.codec is None
