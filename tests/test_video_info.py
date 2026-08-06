import os
import re
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtWidgets import QApplication

from domain.models import Video


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def repo(tmp_path):
    from domain.repository import Repository

    r = Repository(tmp_path / "db.sqlite")
    yield r
    r.close()


def _mk_video(repo, **kw):
    v = Video(filename="a.mp4", filepath=r"D:\v\a.mp4", **kw)
    repo.upsert_videos([v])
    return repo.get_by_path(v.filepath)


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
