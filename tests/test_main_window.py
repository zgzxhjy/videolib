import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication, QHeaderView


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


def test_rows_have_fixed_height(qapp, app_env):
    """At library scale rows must keep a fixed height (no per-row size hints),
    and long filenames are still reachable via the full-path tooltip."""
    _mk_video(app_env, "超" * 300 + ".mp4", "D:/x/" + "超" * 300 + ".mp4")
    _mk_video(app_env, "短.mp4", "D:/x/短.mp4")
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        qapp.processEvents()
        assert w.table.verticalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
        for row in range(w.model.rowCount()):
            assert w.table.rowHeight(row) == 96, "rows must be fixed at thumbnail height"
            v = w.model.video_at(row)
            tip = w.model.data(w.model.index(row, 1), Qt.ItemDataRole.ToolTipRole)
            assert tip == v.filepath, "tooltip must expose the full path"
            if len(v.filepath) > 100:
                assert tip.startswith("D:/x/") and tip.endswith(".mp4")
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
