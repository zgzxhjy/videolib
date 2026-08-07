"""Small standalone tests: app icon, av submodule hook guard, startup _MEI
cleanup, and the video info dialog (merged from four one-file suites)."""
import importlib.util
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from domain.models import Video
from services import startup_cleanup

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def av_hook():
    spec = importlib.util.spec_from_file_location(
        "hook_av", ROOT / "build-hooks" / "hook-av.py"
    )
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


def _mk_video(repo, **kw):
    v = Video(filename="a.mp4", filepath=r"D:\v\a.mp4", **kw)
    repo.upsert_videos([v])
    return repo.get_by_path(v.filepath)


def _make_mei(tmp_path, name, age_s):
    d = tmp_path / name
    d.mkdir()
    (d / "python3.dll").write_bytes(b"x")
    old = time.time() - age_s
    os.utime(d, (old, old))
    return d


def _fake_meipass(monkeypatch, tmp_path, current="_MEI100"):
    cur = tmp_path / current
    cur.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(cur), raising=False)
    return cur


def test_app_icon_resolves_and_loads(qapp):
    """app.ico must be reachable in dev (and bundled in the frozen exe)."""
    from PyQt6.QtGui import QIcon

    from main import resolve_icon_path

    p = resolve_icon_path()
    assert p.name == "app.ico"
    assert p.is_file(), "app.ico must sit next to main.py for the dev launcher"
    icon = QIcon(str(p))
    assert not icon.isNull(), "app.ico must decode into a valid QIcon"


def test_av_runtime_submodules_importable(av_hook):
    """Guard for the PyInstaller hook-av.py list.

    The frozen exe failed metadata probes for every video with an embedded
    subtitle track because PyAV lazy-imports av.subtitles.stream (and other
    Cython submodules) at runtime, which the exe's module graph lacked. If an
    av upgrade drops or renames one of these, the hook list must follow —
    failing here before the next build catches it in dev.
    """
    hiddenimports = av_hook.hiddenimports

    assert len(hiddenimports) >= 20
    for name in hiddenimports:
        importlib.import_module(name)


def test_noop_when_running_from_source(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    leftover = _make_mei(tmp_path, "_MEIold", 3600)
    assert startup_cleanup.cleanup_stale_mei() == 0
    assert leftover.exists()


def test_removes_all_leftovers_when_alone(tmp_path, monkeypatch):
    _fake_meipass(monkeypatch, tmp_path)
    stale = _make_mei(tmp_path, "_MEIold", 3600)
    fresh = _make_mei(tmp_path, "_MEIfresh", 5)
    monkeypatch.setattr(startup_cleanup, "_other_instances_running", lambda: False)
    assert startup_cleanup.cleanup_stale_mei() == 2
    assert not stale.exists()
    assert not fresh.exists()
    assert (tmp_path / "_MEI100").exists()


def test_concurrent_instance_protects_recent(tmp_path, monkeypatch):
    _fake_meipass(monkeypatch, tmp_path)
    old = _make_mei(tmp_path, "_MEIold", 3600)
    recent = _make_mei(tmp_path, "_MEIrecent", 5 * 60)
    monkeypatch.setattr(startup_cleanup, "_other_instances_running", lambda: True)
    assert startup_cleanup.cleanup_stale_mei() == 1
    assert not old.exists()
    assert recent.exists()


def test_locked_dir_skipped_silently(tmp_path, monkeypatch):
    _fake_meipass(monkeypatch, tmp_path)
    locked = _make_mei(tmp_path, "_MEIlocked", 3600)
    other = _make_mei(tmp_path, "_MEIother", 3600)
    monkeypatch.setattr(startup_cleanup, "_other_instances_running", lambda: False)
    real_rmtree = shutil.rmtree

    def flaky(path, *args, **kwargs):
        if str(path).endswith("_MEIlocked"):
            raise PermissionError("in use")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", flaky)
    assert startup_cleanup.cleanup_stale_mei() == 1
    assert locked.exists()
    assert not other.exists()


def test_video_info_shows_all_fields(qapp, repo):
    from ui.dialogs.video_info import VideoInfoDialog

    v = _mk_video(
        repo,
        file_size=12345678,
        duration=65.5,
        resolution="1920x1080",
        codec="h264",
        file_mtime=1700000000.0,
        scanned_at=datetime(2026, 8, 5, 12, 0, 0),
    )
    cat = repo.add_category("动作", root=r"D:\v")
    repo.assign_category(v.id, cat.id)
    flist = repo.create_favorite_list("收藏夹_测试")
    repo.add_favorite(v.id, flist.id)
    repo.record_play(v.id, 12.5)

    d = VideoInfoDialog(v, repo)
    try:
        text = "\n".join(l.text() for l in d.labels)
        assert "a.mp4" in text
        assert r"D:\v\a.mp4" in text
        assert "11.8 MB" in text or "12.3 MB" in text  # _fmt_size(12345678)
        assert "01:05" in text  # 65.5s
        assert "1920x1080" in text
        assert "h264" in text
        assert re.search(r"\d{4}-\d{2}-\d{2}", text)  # scanned_at (DB CURRENT_TIMESTAMP)
        assert "2023-11-15" in text  # file_mtime epoch (local tz)
        assert "动作" in text  # categories_of_video
        assert "收藏夹_测试" in text  # lists_of_video
        assert "00:12" in text  # resume position 12.5s
    finally:
        d.close()


def test_video_info_unknowns_for_sparse_row(qapp, repo):
    from ui.dialogs.video_info import VideoInfoDialog

    v = _mk_video(repo)  # nothing but filename/path
    d = VideoInfoDialog(v, repo)
    try:
        text = "\n".join(l.text() for l in d.labels)
        assert "(无)" in text  # no categories / favorite lists
        assert "未知" in text  # no resolution/codec/mtime
    finally:
        d.close()
