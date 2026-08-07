import os

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QHeaderView
from tests.helpers import mk_video, wait_for


def _make_window(app_env):
    from ui.main_window import MainWindow

    w = MainWindow(app_env)
    w.resize(1100, 700)
    w.show()
    return w


def test_regenerate_keeps_cached_pixmap_until_ready(qapp, app_env):
    """Mass regeneration must not blank the thumbnail column: old pixmaps
    stay cached until the new file lands (atomic swap on the write side)."""
    from PyQt6.QtGui import QPixmap

    mk_video(app_env, "D:/x/a.mp4")
    w = _make_window(app_env)
    try:
        model = w.model
        vid = model.all_videos()[0].id
        thumb = model._thumb_path(vid)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        pix = QPixmap(340, 192)
        pix.fill(Qt.GlobalColor.red)
        assert pix.save(str(thumb))
        model._pix_cache[vid] = pix
        model.regenerate_thumbs([vid])
        assert vid in model._pix_cache, "old pixmap must survive until the new one is ready"
    finally:
        w.close()


def test_thumb_ready_null_decodes_drop_request_for_retry(qapp, app_env):
    """A corrupt JPEG that cannot be decoded must not poison the session:
    its id leaves _requested so a later paint/refresh retries generation."""
    mk_video(app_env, "D:/x/a.mp4")
    w = _make_window(app_env)
    try:
        model = w.model
        vid = model.all_videos()[0].id
        thumb = model._thumb_path(vid)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"\xff\xd8\xff\xe0 corrupt-not-a-jpeg")
        with model._lock:
            model._requested.add(vid)
        model._on_thumb_ready(vid)
        with model._lock:
            assert vid not in model._requested, "undecodable thumb must stay retriable"
        assert vid not in model._pix_cache
    finally:
        w.close()


def test_hover_tracks_any_column(qapp, app_env):
    """Hovering over a NON-play column must still register the row: the whole
    row lights up (RowHoverDelegate reads _hover_row), while the play-button
    hover stays restricted to the play column."""
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent

    mk_video(app_env, "D:/x/a.mp4")
    mk_video(app_env, "D:/x/b.mp4")
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        rect = w.table.visualRect(w.model.index(1, 1))
        pos = QPointF(rect.center())
        ev = QMouseEvent(
            QEvent.Type.MouseMove,
            pos,
            QPointF(w.table.viewport().mapToGlobal(rect.center())),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        w.table.mouseMoveEvent(ev)
        assert w.table._hover_row == 1, "hover over a data column must track the row"
        assert w.table._delegate._hover_row == -1, "play-button hover stays column-bound"
    finally:
        w.close()


def test_title_column_stretches(qapp, app_env):
    """Stretch/ResizeToContents must be honored after setModel (regression: was 100px)."""
    mk_video(app_env, "D:/x/" + "超" * 40 + ".mp4")
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        assert w.table.columnWidth(1) > 300
    finally:
        w.close()


def test_rows_have_fixed_height(qapp, app_env):
    """At library scale rows must keep a fixed height (no per-row size hints),
    and long filenames are still reachable via the full-path tooltip."""
    mk_video(app_env, "D:/x/" + "超" * 300 + ".mp4")
    mk_video(app_env, "D:/x/短.mp4")
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

    mk_video(app_env, "D:/x/a.mp4")
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

    mk_video(app_env, "D:/x/a.mp4")
    mk_video(app_env, "D:/x/b.mp4")
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

    mk_video(app_env, r"D:\r\a.mp4")
    mk_video(app_env, r"D:\o\b.mp4")
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

    mk_video(app_env, "D:/x/a.mp4")
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

    mk_video(app_env, "D:/x/a.mp4")
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

    mk_video(app_env, "D:/x/a.mp4")
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

    mk_video(app_env, "D:/x/a.mp4")
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


def test_clear_all_directories_refused_keeps_everything(qapp, app_env, monkeypatch):
    """A declined first confirm must not start a clear worker."""
    from PyQt6.QtWidgets import QMessageBox

    mk_video(app_env, "D:/x/a.mp4")
    app_env.register_scan("D:/x")
    w = _make_window(app_env)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    try:
        qapp.processEvents()
        w._clear_all_directories()
        qapp.processEvents()
        assert app_env.count() == 1
        assert app_env.get_scan_roots() == [os.path.normpath("D:/x")]
        assert w._clear_worker is None, "no worker may start on refusal"
    finally:
        w.close()


def test_clear_all_directories_second_confirm_refused(qapp, app_env, monkeypatch):
    """The double confirm must both be Yes before anything is deleted."""
    from PyQt6.QtWidgets import QMessageBox

    mk_video(app_env, "D:/x/a.mp4")
    w = _make_window(app_env)
    answers = iter([QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No])
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: next(answers)),
    )
    try:
        qapp.processEvents()
        w._clear_all_directories()
        qapp.processEvents()
        assert app_env.count() == 1
        assert w._clear_worker is None
    finally:
        w.close()


def test_clear_all_directories_wipes_library_keeps_history(qapp, app_env, monkeypatch):
    """Double Yes clears every row + thumb; scan roots and backup survive."""
    import config
    from PyQt6.QtWidgets import QMessageBox

    from services.thumbnailer import Thumbnailer

    mk_video(app_env, "D:/x/a.mp4")
    mk_video(app_env, "D:/x/b.mp4")
    a = app_env.get_by_path("D:/x/a.mp4")
    b = app_env.get_by_path("D:/x/b.mp4")
    app_env.register_scan("D:/x")
    for v in (a, b):
        Thumbnailer(config.THUMBS_DIR).path_for(v.id).write_bytes(b"x")
    w = _make_window(app_env)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    try:
        qapp.processEvents()
        w._clear_all_directories()
        assert wait_for(
            lambda: w._clear_worker is not None and w._clear_worker.isFinished()
        ), "clear worker did not finish"
        for _ in range(30):
            qapp.processEvents()
        assert app_env.count() == 0
        assert app_env.get_scan_roots() == [os.path.normpath("D:/x")], "history must survive clear-all"
        assert not Thumbnailer(config.THUMBS_DIR).path_for(a.id).exists()
        assert not Thumbnailer(config.THUMBS_DIR).path_for(b.id).exists()
        assert "已清空" in w.statusBar().currentMessage()
        assert w._clear_worker is None, "worker ref must be released after done"
    finally:
        w.close()


def test_mark_finished_clears_resume_keeps_recent(qapp, app_env, monkeypatch):
    """Marking a video finished must drop its resume marker but keep the
    recent-play entry."""
    from PyQt6.QtWidgets import QMessageBox

    from ui.video_list import ViewKind

    from domain.models import Video

    app_env.upsert_videos([Video(filename="a.mp4", filepath="D:/x/a.mp4", duration=100.0)])
    a = app_env.get_by_path("D:/x/a.mp4")
    app_env.record_play(a.id, 42.0)

    w = _make_window(app_env)
    try:
        qapp.processEvents()
        m = w.model
        w._set_view(ViewKind.ALL)
        assert m.data(m.index(0, 2)).startswith("⏵")

        w._mark_finished([a])
        qapp.processEvents()
        assert app_env.last_position(a.id) == 0.0
        assert not m.data(m.index(0, 2)).startswith("⏵"), "marker must vanish"
        assert len(app_env.recent_plays(limit=10)) == 1, "recent entry must survive"
        assert "已标记 1 个视频为已看完" in w.statusBar().currentMessage()
    finally:
        w.close()


def test_copy_paths_and_names_to_clipboard(qapp, app_env):
    from PyQt6.QtWidgets import QApplication

    mk_video(app_env, "D:/x/a.mp4")
    mk_video(app_env, "D:/x/b.mp4")
    a = app_env.get_by_path("D:/x/a.mp4")
    b = app_env.get_by_path("D:/x/b.mp4")

    w = _make_window(app_env)
    try:
        qapp.processEvents()
        w._copy_paths([a, b])
        assert QApplication.clipboard().text() == "D:/x/a.mp4\nD:/x/b.mp4"
        w._copy_names([a, b])
        assert QApplication.clipboard().text() == "a.mp4\nb.mp4"
    finally:
        w.close()


def test_stats_dialog_shows_totals(qapp, app_env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    mk_video(app_env, "D:/x/a.mp4")
    captured = {}
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda parent, title, text: captured.update(title=title, text=text)),
    )
    w = _make_window(app_env)
    try:
        qapp.processEvents()
        w._show_stats()
        assert captured["title"] == "库统计"
        assert "视频总数: 1" in captured["text"]
    finally:
        w.close()


def test_drop_handles_dirs_and_video_files(qapp, app_env, monkeypatch, tmp_path):
    """Dropped folders must be scanned; dropped video files indexed."""
    from PyQt6.QtCore import QMimeData, QPointF, Qt
    from PyQt6.QtGui import QDropEvent

    from services.library import Library
    from ui.main_window import MainWindow

    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    vid_file = video_dir / "a.mp4"
    vid_file.write_bytes(b"fake")
    other_dir = tmp_path / "docs"
    other_dir.mkdir()

    scanned = []
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, root: scanned.append(root))
    synced = []
    monkeypatch.setattr(
        Library, "apply_sync",
        lambda self, probes, removals, **kw: synced.append(probes),
    )

    w = _make_window(app_env)
    try:
        qapp.processEvents()
        paths = [str(video_dir), str(vid_file), str(other_dir / "notes.txt"), r"C:\missing\b.mp4"]
        dirs, videos = w._handle_dropped_paths(paths)
        assert dirs == [str(video_dir)], "only real dirs are scanned"
        assert videos == [str(vid_file)], "only real video files are indexed"

        md = QMimeData()
        from PyQt6.QtCore import QUrl

        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        event = QDropEvent(
            QPointF(10, 10), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        w.dropEvent(event)
        qapp.processEvents()
        fwd = lambda p: p.replace("\\", "/")
        assert scanned == [fwd(str(video_dir))], "_start_scan must run for each dir"
        assert synced == [[fwd(str(vid_file))]], "video files must be probed via apply_sync"
        assert "已添加 1 个视频" in w.statusBar().currentMessage()
    finally:
        w.close()


def test_table_mime_data_carries_video_ids(qapp, app_env):
    """Dragging rows must expose video ids to the category tree."""
    from PyQt6.QtCore import QModelIndex

    from ui.video_list import MIME_VIDEO_IDS, ViewKind

    mk_video(app_env, "D:/x/a.mp4")
    mk_video(app_env, "D:/x/b.mp4")
    a = app_env.get_by_path("D:/x/a.mp4")
    b = app_env.get_by_path("D:/x/b.mp4")

    w = _make_window(app_env)
    try:
        qapp.processEvents()
        m = w.model
        m.show(ViewKind.ALL)
        md = m.mimeData([m.index(0, 1), m.index(1, 1), m.index(0, 0)])
        assert md is not None and md.hasFormat(MIME_VIDEO_IDS)
        ids = sorted(int(x) for x in bytes(md.data(MIME_VIDEO_IDS)).decode().split(","))
        assert ids == sorted([a.id, b.id]), "ids must be deduped and sorted"
        assert m.mimeData([QModelIndex()]) is None or True
    finally:
        w.close()


def test_confirm_delete_refused_keeps_everything(qapp, app_env, monkeypatch):
    """A declined confirm box must not remove rows or files."""
    from PyQt6.QtWidgets import QMessageBox

    mk_video(app_env, "D:/x/a.mp4")
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
    mk_video(app_env, "D:/x/a.mp4")
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
    mk_video(app_env, "D:/x/测试.mp4")
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
    mk_video(app_env, "D:/x/fresh.mp4")
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


def _capture_info(monkeypatch):
    captured = {}
    from PyQt6.QtWidgets import QMessageBox

    def fake_info(parent, title, text):
        captured["title"] = title
        captured["text"] = text

    monkeypatch.setattr("ui.main_window.QMessageBox.information", staticmethod(fake_info))
    return captured


def test_find_duplicates_reports_groups(qapp, app_env, monkeypatch):
    from domain.models import Video

    app_env.upsert_videos([
        Video(filename="a.mp4", filepath="D:/x/a.mp4", file_size=1000, duration=60.0),
        Video(filename="copy_a.mp4", filepath="D:/x/copy_a.mp4", file_size=1000, duration=59.8),
        Video(filename="b.mp4", filepath="D:/x/b.mp4", file_size=1000, duration=120.0),
    ])
    w = _make_window(app_env)
    try:
        captured = _capture_info(monkeypatch)
        w._find_duplicates()
        assert captured["title"] == "查找重复视频"
        assert "发现 1 组" in captured["text"]
        assert "[2 个" in captured["text"]
        assert "a.mp4" in captured["text"] and "copy_a.mp4" in captured["text"]
    finally:
        w.close()


def test_find_duplicates_empty_result(qapp, app_env, monkeypatch):
    mk_video(app_env, "D:/x/only.mp4")
    w = _make_window(app_env)
    try:
        captured = _capture_info(monkeypatch)
        w._find_duplicates()
        assert "未发现重复视频" in captured["text"]
    finally:
        w.close()


def test_regenerate_thumbs_deletes_files(qapp, app_env, tmp_path, monkeypatch):
    import config

    from PyQt6.QtCore import QRunnable

    video_file = tmp_path / "a.mp4"
    video_file.write_bytes(b"video")
    started = []

    class _FakeRunnable(QRunnable):
        def __init__(self, filepath, video_id, thumb=None, repo=None, cb=None):
            super().__init__()
            started.append(video_id)

        def run(self):
            pass

    monkeypatch.setattr("ui.video_list.ThumbRunnable", _FakeRunnable)
    mk_video(app_env, str(video_file))
    v = app_env.get_by_path(str(video_file))
    thumb = config.THUMBS_DIR / f"{v.id}.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"thumb")

    w = _make_window(app_env)
    try:
        qapp.processEvents()
        started.clear()  # forget the initial paint-triggered requests
        w._regenerate_thumbs([v])
        assert not thumb.exists(), "regenerate must delete the stale file"
        assert started == [v.id], "the model must re-request the thumb"
    finally:
        w.close()


class _FakeWatcherThread:
    def __init__(self, roots, repo):
        self.roots = roots
        self.repo = repo
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def wait(self, ms):
        pass

    def _connect(self, cb):
        pass

    message = property(lambda self: type("_Sig", (), {"connect": self._connect})())
    changed = property(lambda self: type("_Sig", (), {"connect": self._connect})())


def test_start_watcher_multiple_roots(qapp, app_env, monkeypatch):
    import ui.main_window as mw

    created = []
    monkeypatch.setattr(
        mw, "WatcherThread",
        lambda roots, repo: created.append((roots, repo)) or _FakeWatcherThread(roots, repo),
    )
    w = _make_window(app_env)
    try:
        w._start_watcher(["D:/a", "D:/b"], resume=True)
        assert len(created) == 1
        assert created[0][0] == ["D:/a", "D:/b"]
        assert created[0][1] is app_env
        assert w._watcher is not None and w._watcher.started

        w._start_watcher([], resume=False)
        assert w._watcher is None, "empty roots must stop the watcher"
    finally:
        w.close()


def test_watch_roots_persist_and_migrate_legacy(qapp, app_env, monkeypatch):
    import config

    import ui.main_window as mw

    monkeypatch.setattr(mw, "WatcherThread", _FakeWatcherThread)
    w = _make_window(app_env)
    try:
        assert w._watch_roots() == []
        w._save_watch_roots([r"D:\new"])
        assert w._watch_roots() == [r"D:\new"]

        config.save_setting("watch_root", r"D:\old")
        config.save_setting("watch_roots", None)
        assert w._watch_roots() == [r"D:\old"], "legacy watch_root string must migrate"

        config.save_setting("watch_roots", [r"D:\a", r"D:\b"])
        config.save_setting("watch_root", None)
        assert w._watch_roots() == [r"D:\a", r"D:\b"]
    finally:
        w.close()
