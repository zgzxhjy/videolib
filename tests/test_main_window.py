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


def test_remove_favorite_direct_from_current_list(qapp, app_env, monkeypatch):
    """In the favorites view, removing must act on the current list directly,
    without asking which list to remove from."""
    from PyQt6.QtWidgets import QDialog

    from ui.main_window import VIEW_ALL, VIEW_FAVORITES

    _mk_video(app_env, "a.mp4", "D:/x/a.mp4")
    lst = app_env.create_favorite_list("收藏夹_默认")
    a = app_env.get_by_path("D:/x/a.mp4")
    app_env.add_favorite(a.id, lst.id)

    calls: list[bool] = []

    class FakeDialog(QDialog):
        def __init__(self, *a, **k):
            super().__init__()
            calls.append(True)

        def exec(self):
            return 0  # rejected: dialog path must not remove anything

    monkeypatch.setattr("ui.main_window.PickFavoriteListDialog", FakeDialog)

    w = _make_window(app_env)
    try:
        w._favorite_list_id = lst.id
        w._set_view(VIEW_FAVORITES)
        qapp.processEvents()
        assert w.model.rowCount() == 1

        w._remove_from_favorite([a], {lst.id})
        qapp.processEvents()
        assert not calls, "favorites view must remove without a dialog"
        assert app_env.lists_of_video(a.id) == []
        assert app_env.get_favorites(lst.id) == []
        assert w.model.rowCount() == 0

        app_env.add_favorite(a.id, lst.id)
        w._set_view(VIEW_ALL)
        w._remove_from_favorite([a], {lst.id})
        assert calls, "other views must still open the picker"
        assert app_env.lists_of_video(a.id), "rejected dialog must not remove"
    finally:
        w.close()


def test_count_label_follows_view(qapp, app_env):
    """The status bar must show the video count of the current view."""
    from ui.main_window import VIEW_ALL, VIEW_FAVORITES

    _mk_video(app_env, "a.mp4", "D:/x/a.mp4")
    _mk_video(app_env, "b.mp4", "D:/x/b.mp4")
    lst = app_env.create_favorite_list("收藏夹_默认")
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        assert w._count_label.text() == "共 2 个视频"

        w._favorite_list_id = lst.id
        w._set_view(VIEW_FAVORITES)
        qapp.processEvents()
        assert w._count_label.text() == "共 0 个视频"

        w._set_view(VIEW_ALL)
        qapp.processEvents()
        assert w._count_label.text() == "共 2 个视频"
    finally:
        w.close()


def test_model_show_maps_views(qapp, app_env):
    """The view→query mapping must live in VideoTableModel.show()."""
    from ui.video_list import ViewKind

    _mk_video(app_env, "a.mp4", r"D:\r\a.mp4")
    _mk_video(app_env, "b.mp4", r"D:\o\b.mp4")
    lst = app_env.create_favorite_list("收藏夹_默认")
    a = app_env.get_by_path(r"D:\r\a.mp4")
    app_env.add_favorite(a.id, lst.id)
    app_env.record_play(a.id, 30.0)
    cat = app_env.add_category("动作", root=r"D:\r")
    app_env.assign_category(a.id, cat.id)

    w = _make_window(app_env)
    try:
        m = w.model
        m.show(ViewKind.ALL)
        assert m.rowCount() == 2
        m.show(ViewKind.FAVORITES, favorite_list_id=lst.id)
        assert m.rowCount() == 1 and m.video_at(0).id == a.id
        m.show(ViewKind.RECENT)
        assert m.rowCount() == 1
        m.show(ViewKind.CURRENT, root=r"D:\r")
        assert m.rowCount() == 1
        m.show(ViewKind.ALL, search_text="b")
        assert m.rowCount() == 1 and m.video_at(0).filename == "b.mp4"
        m.show(ViewKind.CURRENT, category_id=cat.id, root=r"D:\r")
        assert m.rowCount() == 1
        qapp.processEvents()  # let the startup orphan-cleanup timer fire while alive
    finally:
        w.close()


def test_remove_from_library_drops_row_and_thumb(qapp, app_env, monkeypatch):
    """Deleting from the library must drop rows, thumbs and cascade links,
    while keeping the file on disk (the Library invariant)."""
    from PyQt6.QtWidgets import QMessageBox

    import config
    from services.thumbnailer import Thumbnailer

    _mk_video(app_env, "a.mp4", "D:/x/a.mp4")
    v = app_env.get_by_path("D:/x/a.mp4")
    lst = app_env.create_favorite_list("收藏夹_默认")
    app_env.add_favorite(v.id, lst.id)
    thumb = Thumbnailer(config.THUMBS_DIR).path_for(v.id)
    thumb.write_bytes(b"jpeg")

    w = _make_window(app_env)
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    try:
        qapp.processEvents()
        w._remove_from_library([v])
        qapp.processEvents()
        assert app_env.get_by_path("D:/x/a.mp4") is None
        assert not thumb.exists(), "thumb must be deleted with the row"
        assert w.model.rowCount() == 0, "list must refresh after removal"
        assert "已从库中移除 1 个视频" in w.statusBar().currentMessage()
    finally:
        w.close()


def test_delete_files_moves_to_trash_then_removes(qapp, app_env, monkeypatch):
    """The recycle-bin path must trash the file and then drop the row."""
    from PyQt6.QtCore import QFile
    from PyQt6.QtWidgets import QMessageBox

    _mk_video(app_env, "a.mp4", "D:/x/a.mp4")
    v = app_env.get_by_path("D:/x/a.mp4")

    trashed = []
    monkeypatch.setattr(QFile, "moveToTrash", staticmethod(lambda p: trashed.append(p) or True))
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )

    w = _make_window(app_env)
    try:
        qapp.processEvents()
        w._delete_files([v])
        qapp.processEvents()
        assert trashed == ["D:/x/a.mp4"]
        assert app_env.get_by_path("D:/x/a.mp4") is None
    finally:
        w.close()


def test_clear_play_history_wipes_recent_and_resume(qapp, app_env, monkeypatch):
    """Clearing play history must empty the recent view and drop resume points."""
    from PyQt6.QtWidgets import QMessageBox

    from ui.video_list import ViewKind

    _mk_video(app_env, "a.mp4", "D:/x/a.mp4")
    a = app_env.get_by_path("D:/x/a.mp4")
    app_env.record_play(a.id, 42.0)

    w = _make_window(app_env)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    try:
        qapp.processEvents()
        m = w.model
        w._set_view(ViewKind.RECENT)
        assert m.rowCount() == 1
        assert app_env.last_position(a.id) == 42.0

        w._clear_play_history()
        qapp.processEvents()
        assert app_env.recent_plays(limit=10) == []
        assert app_env.last_position(a.id) == 0.0
        assert m.rowCount() == 0, "recent view must refresh to empty"
        assert "已清空播放历史" in w.statusBar().currentMessage()

        m.show(ViewKind.ALL)
        assert not m.data(m.index(0, 2)).startswith("⏵"), "resume marker must vanish"
    finally:
        w.close()


def test_clear_play_history_refused_keeps_data(qapp, app_env, monkeypatch):
    """A declined confirm box must not touch play history."""
    from PyQt6.QtWidgets import QMessageBox

    _mk_video(app_env, "a.mp4", "D:/x/a.mp4")
    a = app_env.get_by_path("D:/x/a.mp4")
    app_env.record_play(a.id, 42.0)

    w = _make_window(app_env)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    try:
        qapp.processEvents()
        w._clear_play_history()
        qapp.processEvents()
        assert app_env.last_position(a.id) == 42.0
        assert len(app_env.recent_plays(limit=10)) == 1
    finally:
        w.close()


def test_confirm_delete_refused_keeps_everything(qapp, app_env, monkeypatch):
    """A declined confirm box must not remove rows or files."""
    from PyQt6.QtWidgets import QMessageBox

    _mk_video(app_env, "a.mp4", "D:/x/a.mp4")
    v = app_env.get_by_path("D:/x/a.mp4")
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )

    w = _make_window(app_env)
    try:
        qapp.processEvents()
        w._remove_from_library([v])
        w._delete_files([v])
        qapp.processEvents()
        assert app_env.get_by_path("D:/x/a.mp4") is not None
    finally:
        w.close()


def test_window_state_remembered_across_runs(qapp, app_env):
    """Geometry and column widths must survive a close and reopen."""
    _mk_video(app_env, "a.mp4", "D:/x/a.mp4")
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        w.table.setColumnWidth(0, 220)  # Interactive column: stored width counts
        w.resize(777, 555)
        w.close()  # closeEvent persists the state
    finally:
        w.close()

    w2 = _make_window(app_env)
    try:
        qapp.processEvents()
        assert w2.table.columnWidth(0) == 220, "interactive column width must be restored"
        import config

        assert "window_geometry" in config.load_settings(), "geometry must be persisted"
    finally:
        w2.close()


def test_model_sort_natural_filename_and_columns(qapp, app_env):
    """Model sort must use natural keys and raw values, and skip the
    non-sortable thumbnail/play columns."""
    from domain.models import Video

    from ui.video_list import ViewKind

    app_env.upsert_videos([
        Video(filename="v10.mp4", filepath="D:/x/v10.mp4", duration=100.0, file_size=200),
        Video(filename="v2.mp4", filepath="D:/x/v2.mp4", duration=2.0, file_size=3000),
        Video(filename="v1.mp4", filepath="D:/x/v1.mp4", duration=30.0, file_size=100),
    ])

    w = _make_window(app_env)
    try:
        m = w.model
        m.show(ViewKind.ALL)
        m.sort(1, Qt.SortOrder.AscendingOrder)
        assert [m.video_at(i).filename for i in range(3)] == [
            "v1.mp4", "v2.mp4", "v10.mp4",
        ], "natural order: v2 before v10"

        m.sort(2, Qt.SortOrder.AscendingOrder)
        assert [m.video_at(i).duration for i in range(3)] == [2.0, 30.0, 100.0]

        m.sort(5, Qt.SortOrder.DescendingOrder)
        assert [m.video_at(i).file_size for i in range(3)] == [3000, 200, 100]

        before = [m.video_at(i).id for i in range(3)]
        m.sort(0, Qt.SortOrder.AscendingOrder)
        assert [m.video_at(i).id for i in range(3)] == before, "thumb column must be ignored"
        m.sort(6, Qt.SortOrder.AscendingOrder)
        assert [m.video_at(i).id for i in range(3)] == before, "play column must be ignored"
    finally:
        w.close()


def test_sort_persisted_across_runs(qapp, app_env):
    """The sort indicator and order must survive a close and reopen."""
    from domain.models import Video

    app_env.upsert_videos([
        Video(filename="b.mp4", filepath="D:/x/b.mp4", duration=1.0),
        Video(filename="a.mp4", filepath="D:/x/a.mp4", duration=99.0),
    ])
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        w.table.horizontalHeader().setSortIndicator(2, Qt.SortOrder.DescendingOrder)
        w.model.sort(2, Qt.SortOrder.DescendingOrder)
        assert w.model.video_at(0).filename == "a.mp4"
        w.close()
    finally:
        w.close()

    w2 = _make_window(app_env)
    try:
        qapp.processEvents()
        assert w2.model.video_at(0).filename == "a.mp4", "sorted order must be restored"
    finally:
        w2.close()


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


def test_resume_marker_shown_when_in_range(qapp, app_env):
    """A play history position in (5s, 90% of duration) marks the duration column."""
    from domain.models import Video

    from ui.video_list import ViewKind

    app_env.upsert_videos([
        Video(filename="mid.mp4", filepath="D:/x/mid.mp4", duration=100.0),
        Video(filename="done.mp4", filepath="D:/x/done.mp4", duration=100.0),
        Video(filename="start.mp4", filepath="D:/x/start.mp4", duration=100.0),
    ])
    mid = app_env.get_by_path("D:/x/mid.mp4")
    done = app_env.get_by_path("D:/x/done.mp4")
    start = app_env.get_by_path("D:/x/start.mp4")
    app_env.record_play(mid.id, 45.0)
    app_env.record_play(done.id, 95.0)
    app_env.record_play(start.id, 3.0)

    w = _make_window(app_env)
    try:
        qapp.processEvents()
        m = w.model
        m.show(ViewKind.ALL)
        texts = {m.video_at(r).filename: m.data(m.index(r, 2)) for r in range(3)}
        assert texts["mid.mp4"].startswith("⏵"), "resume position must mark the row"
        assert not texts["done.mp4"].startswith("⏵"), "95s of 100s is past 90%, no marker"
        assert not texts["start.mp4"].startswith("⏵"), "3s is below the 5s floor, no marker"
        tooltip = m.data(m.index(next(r for r in range(3) if m.video_at(r).filename == "mid.mp4"), 2), Qt.ItemDataRole.ToolTipRole)
        assert "续播位置" in tooltip
    finally:
        w.close()


def test_resume_marker_empty_without_history(qapp, app_env):
    _mk_video(app_env, "fresh.mp4", "D:/x/fresh.mp4")
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        assert "⏵" not in w.model.data(w.model.index(0, 2))
    finally:
        w.close()


def test_resume_marker_tracks_sort(qapp, app_env):
    """Markers must follow their row after sorting (id-keyed lookup)."""
    from domain.models import Video

    from ui.video_list import ViewKind

    app_env.upsert_videos([
        Video(filename="b.mp4", filepath="D:/x/b.mp4", duration=100.0),
        Video(filename="a.mp4", filepath="D:/x/a.mp4", duration=100.0),
    ])
    a = app_env.get_by_path("D:/x/a.mp4")
    app_env.record_play(a.id, 50.0)

    w = _make_window(app_env)
    try:
        qapp.processEvents()
        m = w.model
        m.show(ViewKind.ALL)
        m.sort(1, Qt.SortOrder.AscendingOrder)
        row_a = 0 if m.video_at(0).filename == "a.mp4" else 1
        assert m.data(m.index(row_a, 2)).startswith("⏵")
        assert not m.data(m.index(1 - row_a, 2)).startswith("⏵")
    finally:
        w.close()
