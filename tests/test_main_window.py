import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Isolate config paths so MainWindow never touches the real ~/.videolib."""
    import config

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "videolib.db")
    monkeypatch.setattr(config, "THUMBS_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")

    from domain.repository import Repository

    repo = Repository(tmp_path / "videolib.db")
    yield repo
    repo.close()


def _mk_video(repo, filename: str, filepath: str) -> None:
    from domain.models import Video

    repo.upsert_videos([Video(filename=filename, filepath=filepath)])


def _make_window(app_env):
    from ui.main_window import MainWindow

    w = MainWindow(app_env)
    w.resize(1100, 700)
    w.show()
    return w


def test_title_column_stretches(qapp, app_env):
    """Stretch/ResizeToContents must be honored after setModel (regression: was 100px)."""
    _mk_video(app_env, "超" * 40 + ".mp4", "D:/x/" + "超" * 40 + ".mp4")
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        assert w.table.columnWidth(1) > 300
    finally:
        w.close()


def test_long_title_row_grows(qapp, app_env):
    """Rows must grow beyond the thumbnail height for very long wrapped titles."""
    _mk_video(app_env, "超" * 300 + ".mp4", "D:/x/" + "超" * 300 + ".mp4")
    _mk_video(app_env, "短.mp4", "D:/x/短.mp4")
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        qapp.processEvents()
        videos = w.model.all_videos()  # ordered by filename: 短 < 超
        long_row = next(i for i, v in enumerate(videos) if v.filename.startswith("超"))
        short_row = next(i for i, v in enumerate(videos) if v.filename.startswith("短"))
        assert w.table.rowHeight(long_row) > 96, "long title must wrap into a taller row"
        assert w.table.rowHeight(short_row) <= 100, "short title keeps thumbnail row height"
    finally:
        w.close()


def test_play_column_fits_button(qapp, app_env):
    """ResizeToContents must not collapse the self-drawn play column (regression: 28px)."""
    _mk_video(app_env, "测试.mp4", "D:/x/测试.mp4")
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        assert w.table.columnWidth(6) >= 60
    finally:
        w.close()


def test_play_button_delegate_sizehint(qapp):
    from PyQt6.QtCore import QAbstractTableModel, QRect
    from PyQt6.QtWidgets import QStyleOptionViewItem

    from ui.video_list import PlayButtonDelegate

    class M(QAbstractTableModel):
        def rowCount(self, parent=None):
            return 1

        def columnCount(self, parent=None):
            return 7

        def data(self, i, role=0):
            return None

    d = PlayButtonDelegate()
    opt = QStyleOptionViewItem()
    opt.font = qapp.font()
    opt.rect = QRect(0, 0, 60, 96)
    size = d.sizeHint(opt, M().index(0, 6))
    fm = QFontMetrics(qapp.font())
    assert size.width() >= fm.horizontalAdvance(d.BUTTON_TEXT) + 12


def test_title_wrap_delegate_sizehint(qapp):
    from PyQt6.QtCore import QAbstractTableModel, QRect
    from PyQt6.QtWidgets import QStyleOptionViewItem

    from ui.video_list import TitleWrapDelegate

    class M(QAbstractTableModel):
        def rowCount(self, parent=None):
            return 2

        def columnCount(self, parent=None):
            return 2

        def data(self, i, role=0):
            if role == 0 and i.column() == 1:
                return ("超" * 300) if i.row() == 0 else "短.mp4"
            return None

    model = M()
    d = TitleWrapDelegate()
    opt = QStyleOptionViewItem()
    opt.font = qapp.font()
    opt.rect = QRect(0, 0, 100, 96)
    tall = d.sizeHint(opt, model.index(0, 1))
    short = d.sizeHint(opt, model.index(1, 1))
    assert tall.height() > 96, "CJK titles wrap at every char, must be tall"
    assert short.height() <= 96
