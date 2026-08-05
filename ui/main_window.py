import os
import subprocess

from PyQt6.QtCore import QSize, Qt, QThread, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import config
from domain.models import Video
from domain.repository import Repository
from services.thumbnailer import THUMB_HEIGHT, THUMB_WIDTH, Thumbnailer
from services.watcher import WatcherThread
from ui.category_tree import CategoryTree
from ui.dialogs.pick_category import PickCategoryDialog
from ui.dialogs.pick_favorite_list import PickFavoriteListDialog, normalize_favorite_name
from ui.dialogs.pick_scan_root import PickScanRootDialog
from ui.player import PlayerWindow
from ui.scan_worker import ScanWorker
from ui.search_bar import SearchBar
from ui.video_list import COL_PLAY, COL_THUMB, PlayTableView, VideoTableModel, ViewKind

# View constants kept for callers/tests; the mapping view→query lives in
# VideoTableModel.show().
VIEW_CURRENT = ViewKind.CURRENT
VIEW_ALL = ViewKind.ALL
VIEW_FAVORITES = ViewKind.FAVORITES
VIEW_RECENT = ViewKind.RECENT


def _under_root(path: str, root: str) -> bool:
    p = os.path.normcase(os.path.normpath(path))
    r = os.path.normcase(os.path.normpath(root))
    return p == r or p.startswith(r + os.sep)


class _OrphanCleanupThread(QThread):
    """Deletes thumbnails whose video rows no longer exist, off the UI thread."""

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self._repo = repo
        self.removed = 0

    def run(self) -> None:
        valid = self._repo.all_video_ids()
        self.removed = Thumbnailer.cleanup_orphans(config.THUMBS_DIR, valid)


class MainWindow(QMainWindow):
    def __init__(self, repo: Repository):
        super().__init__()
        self._repo = repo
        self._view = VIEW_ALL
        self._current_root: str | None = None
        self._favorite_list_id: int | None = None
        self._players: list[PlayerWindow] = []
        self._watcher: WatcherThread | None = None
        self._scanner: ScanWorker | None = None
        self.setWindowTitle(f"{config.APP_NAME} - 视频管理")
        self.resize(1100, 700)

        self._build_toolbar()
        self._build_body()
        self.statusBar().showMessage("就绪")

        self.model = VideoTableModel(repo, config.THUMBS_DIR)
        self.table.setModel(self.model)
        self.table.setIconSize(QSize(THUMB_WIDTH, THUMB_HEIGHT))
        self.table.setColumnWidth(COL_THUMB, THUMB_WIDTH)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_PLAY, QHeaderView.ResizeMode.ResizeToContents)
        self._count_label = QLabel("共 0 个视频")
        self.statusBar().addPermanentWidget(self._count_label)
        self.model.modelReset.connect(self._update_count_label)
        self.model.show(VIEW_ALL)
        self.table.selectionModel().selectionChanged.connect(
            lambda _sel, _desel: self.play_action.setEnabled(bool(self._selected_videos()))
        )
        self._setup_shortcuts()

        # Parented single-shot timer: a bare QTimer.singleShot(0, ...) would
        # still fire after the window is destroyed and call a slot on a dead
        # C++ object, which PyQt6 turns into a hard qFatal crash.
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.setSingleShot(True)
        self._cleanup_timer.timeout.connect(self._start_orphan_cleanup)
        self._cleanup_timer.start(0)

        root = config.load_settings().get("watch_root")
        if root and os.path.isdir(root):
            self._activate_root(root)
            self._start_watcher(root, resume=True)

    def _update_count_label(self) -> None:
        self._count_label.setText(f"共 {self.model.rowCount():,} 个视频")

    def _start_orphan_cleanup(self) -> None:
        self._cleanup_thread = _OrphanCleanupThread(self._repo, self)
        self._cleanup_thread.finished.connect(self._on_orphan_cleanup_done)
        self._cleanup_thread.start()

    def _on_orphan_cleanup_done(self) -> None:
        if self._cleanup_thread.removed:
            self.statusBar().showMessage(
                f"已清理 {self._cleanup_thread.removed} 个孤儿缩略图"
            )

    def _stop_watcher(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher.wait(3000)
            self._watcher = None

    def _activate_root(self, root: str) -> None:
        """Set the current scan root, adopt legacy categories, switch the tree."""
        self._current_root = root
        self._repo.adopt_legacy_categories(root)
        self.tree.reload(root)

    def closeEvent(self, event) -> None:
        self._stop_watcher()
        super().closeEvent(event)

    # ---------- UI construction ----------

    def _build_toolbar(self) -> None:
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        self.play_action = tb.addAction("▶ 播放选中", self._play_selected)
        self.play_action.setEnabled(False)
        tb.addSeparator()
        tb.addAction("扫描目录", self._pick_and_scan)
        self._history_menu = QMenu(self)
        self._history_action = tb.addAction("历史目录")
        self._history_action.setMenu(self._history_menu)
        tb.addAction("刷新", self._refresh_all)
        tb.addSeparator()
        tb.addAction("当前目录", lambda: self._set_view(VIEW_CURRENT))
        tb.addAction("最近播放", lambda: self._set_view(VIEW_RECENT))
        self._favorites_menu = QMenu(self)
        self._favorites_action = tb.addAction("收藏夹")
        self._favorites_action.setMenu(self._favorites_menu)
        tb.addAction("所有目录", lambda: self._set_view(VIEW_ALL))
        self.addToolBar(tb)
        self._rebuild_history_menu()
        self._rebuild_favorites_menu()

    def _rebuild_favorites_menu(self) -> None:
        self._favorites_menu.clear()
        for l in self._repo.get_favorite_lists():
            self._favorites_menu.addAction(
                f"{l.name} ({self._repo.count_favorites(l.id)})",
                lambda l=l: self._show_favorite_list(l.id),
            )
        self._favorites_menu.addSeparator()
        self._favorites_menu.addAction("＋ 新建收藏夹...", self._create_favorite_list)
        if self._repo.get_favorite_lists():
            self._favorites_menu.addAction("删除收藏夹...", self._delete_favorite_list)

    def _show_favorite_list(self, list_id: int) -> None:
        self._favorite_list_id = list_id
        self._set_view(VIEW_FAVORITES)
        name = next(
            (l.name for l in self._repo.get_favorite_lists() if l.id == list_id),
            str(list_id),
        )
        self.statusBar().showMessage(f"收藏夹: {name}（{self.model.rowCount()} 个视频）")

    def _create_favorite_list(self) -> None:
        name, ok = QInputDialog.getText(
            self, "新建收藏夹", "收藏夹名称（自动补前缀「收藏夹_」）:"
        )
        if not ok or not name.strip():
            return
        try:
            created = self._repo.create_favorite_list(normalize_favorite_name(name))
        except ValueError as e:
            QMessageBox.warning(self, "新建收藏夹", str(e))
            return
        self._rebuild_favorites_menu()
        self._show_favorite_list(created.id)

    def _delete_favorite_list(self) -> None:
        dialog = PickFavoriteListDialog(
            self._repo, "删除收藏夹", delete_mode=True, parent=self
        )
        dialog.exec()
        if not dialog.deleted_ids:
            return
        self._rebuild_favorites_menu()
        if self._favorite_list_id in dialog.deleted_ids:
            self._favorite_list_id = None
            self._set_view(VIEW_ALL)
            self.statusBar().showMessage("当前收藏夹已被删除，已切换到所有目录")
        else:
            self.statusBar().showMessage(f"已删除 {len(dialog.deleted_ids)} 个收藏夹")

    def _rebuild_history_menu(self) -> None:
        self._history_menu.clear()
        roots = self._repo.get_scan_roots()
        self._history_action.setEnabled(bool(roots))
        for r in roots:
            self._history_menu.addAction(r, lambda r=r: self._start_scan(r))
        if roots:
            self._history_menu.addSeparator()
            self._history_menu.addAction("删除历史记录...", self._delete_scan_roots)

    def _delete_scan_roots(self) -> None:
        dialog = PickScanRootDialog(self._repo, parent=self)
        dialog.exec()
        deleted = dialog.deleted_roots
        if not deleted:
            return
        self._rebuild_history_menu()
        for root in deleted:
            if root == config.load_settings().get("watch_root"):
                self._stop_watcher()
                config.save_setting("watch_root", None)
        self._refresh_all()
        self.statusBar().showMessage(f"已删除 {len(deleted)} 个历史目录")

    def _build_body(self) -> None:
        self.tree = CategoryTree(self._repo)
        self.tree.category_selected.connect(self._on_category_selected)

        self.search = SearchBar()
        self.search.search_submitted.connect(self._on_search)

        self.table = PlayTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(lambda idx: self._play(self._video_at(idx)))
        self.table.play_clicked.connect(lambda row: self._play(self.model.video_at(row)))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_menu)

        right = QWidget()
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search)
        layout.addWidget(self.table)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    def _setup_shortcuts(self) -> None:
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            shortcut = QShortcut(QKeySequence(key), self.table)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self._play_selected)

    # ---------- helpers ----------

    def _video_at(self, index) -> Video | None:
        v = index.data(Qt.ItemDataRole.UserRole)
        return v if isinstance(v, Video) else None

    def _selected_videos(self) -> list[Video]:
        vids = []
        for idx in self.table.selectionModel().selectedRows():
            v = self._video_at(idx)
            if v is not None:
                vids.append(v)
        return vids

    def _view_ctx(self) -> dict:
        """Current navigation state, passed straight to VideoTableModel.show()."""
        return {
            "root": self._current_root,
            "favorite_list_id": self._favorite_list_id,
        }

    def _set_view(self, view: str) -> None:
        self._view = view
        self.search.clear()
        self.tree.clearSelection()
        self.model.show(view, **self._view_ctx())

    def _refresh_all(self) -> None:
        self.tree.reload()
        self._set_view(self._view)

    # ---------- actions ----------

    def _pick_and_scan(self) -> None:
        root = QFileDialog.getExistingDirectory(self, "选择要扫描的视频目录")
        if not root:
            return
        self._start_scan(root)

    def _start_scan(self, root: str) -> None:
        if self._scanner is not None:
            self.statusBar().showMessage("已有扫描进行中，请稍候")
            return
        if not os.path.isdir(root):
            self.statusBar().showMessage(f"目录不存在或未连接: {root}")
            return
        self._stop_watcher()
        self.model.set_scanning(True)
        self._prev_root = self._current_root
        self._activate_root(root)
        self._set_view(VIEW_CURRENT)
        self.statusBar().showMessage(f"正在扫描 {root} ...")
        dialog = QProgressDialog("正在枚举视频文件...", "取消扫描", 0, 0, self)
        dialog.setWindowTitle("扫描进度")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self._cancel_scan)
        dialog.show()
        worker = ScanWorker(root, self._repo, self)
        worker.message.connect(self.statusBar().showMessage)
        worker.progress.connect(
            lambda done, total, fp: self._on_scan_progress(dialog, done, total, fp)
        )
        worker.done.connect(lambda status: self._on_scan_done(root, dialog, status))
        self._scanner = worker
        worker.start()

    def _cancel_scan(self) -> None:
        if self._scanner is not None:
            self._scanner.cancel()

    def _on_scan_progress(self, dialog: QProgressDialog, done: int, total: int, fp: str) -> None:
        dialog.setRange(0, total)
        dialog.setValue(done)
        dialog.setLabelText(f"已提取元数据 {done}/{total}:\n{os.path.basename(fp)}")

    def _on_scan_done(self, root: str, dialog: QProgressDialog, status: str) -> None:
        self._scanner = None
        dialog.close()
        dialog.deleteLater()
        self.model.set_scanning(False)
        if status == "empty":
            self._on_scan_empty(root)
            return
        self._refresh_all()
        if status == "ok":
            self._rebuild_history_menu()
            config.save_setting("watch_root", root)
            self._start_watcher(root)
            self.statusBar().showMessage("扫描完成，已开启增量监控")
        else:
            self.statusBar().showMessage("扫描取消/出错，未开启增量监控")

    def _on_scan_empty(self, root: str) -> None:
        if self._prev_root is not None:
            self._activate_root(self._prev_root)
        self._set_view(self._view)
        QMessageBox.warning(self, "扫描结果", f"该目录下没有视频文件:\n{root}")

    def _start_watcher(self, root: str, resume: bool = False) -> None:
        self._stop_watcher()
        watcher = WatcherThread(root, self._repo)
        watcher.message.connect(self.statusBar().showMessage)
        watcher.changed.connect(self._on_watch_changed)
        self._watcher = watcher
        watcher.start()
        if resume:
            self.statusBar().showMessage(f"已恢复监控: {root}")

    def _on_watch_changed(self) -> None:
        if self.search.text():
            return
        self.model.show(self._view, **self._view_ctx())

    def _on_search(self, text: str) -> None:
        self._view = VIEW_ALL
        self.tree.clearSelection()
        self.model.show(VIEW_ALL, search_text=text)

    def _on_category_selected(self, category_id: int | None) -> None:
        self._view = VIEW_CURRENT
        self.search.clear()
        self.model.show(VIEW_CURRENT, category_id=category_id, root=self._current_root)
        self.statusBar().showMessage(f"当前列表: {self.model.rowCount()} 个视频")

    def _play(self, video: Video | None) -> None:
        if video is None or not os.path.exists(video.filepath):
            if video is not None:
                QMessageBox.warning(self, "文件不存在", f"文件不存在:\n{video.filepath}")
            return
        player = PlayerWindow(video, self._repo)
        player.finished.connect(self._on_player_closed)
        self._players.append(player)
        player.show()

    def _play_selected(self) -> None:
        videos = self._selected_videos()
        if videos:
            self._play(videos[0])

    def _on_player_closed(self, _video_id: int, _position: float) -> None:
        if self._view == VIEW_RECENT:
            self.model.show(VIEW_RECENT)

    # ---------- context menu ----------

    def _show_table_menu(self, pos) -> None:
        videos = self._selected_videos()
        if not videos:
            return
        menu = QMenu(self)
        menu.addAction("▶ 播放", lambda: self._play(videos[0]))
        menu.addAction("☆ 添加到收藏夹...", lambda: self._add_to_favorite(videos))
        containing = set()
        for v in videos:
            containing.update(l.id for l in self._repo.lists_of_video(v.id))
        if containing:
            if self._view == VIEW_FAVORITES and self._favorite_list_id is not None:
                menu.addAction(
                    "★ 从收藏夹移除",
                    lambda: self._remove_from_favorite(videos, containing),
                )
            else:
                menu.addAction(
                    "★ 从收藏夹移除...",
                    lambda: self._remove_from_favorite(videos, containing),
                )
        menu.addSeparator()
        menu.addAction("添加到分类...", lambda: self._assign_category(videos))
        menu.addAction("从分类移除...", lambda: self._unassign_category(videos))
        menu.addSeparator()
        menu.addAction("打开所在文件夹", lambda: self._reveal(videos[0]))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _add_to_favorite(self, videos: list[Video]) -> None:
        dialog = PickFavoriteListDialog(self._repo, "添加到收藏夹", parent=self)
        if dialog.exec() and dialog.selected_list_id() is not None:
            list_id = dialog.selected_list_id()
            for v in videos:
                self._repo.add_favorite(v.id, list_id)
            self._rebuild_favorites_menu()
            if self._view == VIEW_FAVORITES and self._favorite_list_id == list_id:
                self.model.show(VIEW_FAVORITES, favorite_list_id=list_id)
            self.statusBar().showMessage(f"已将 {len(videos)} 个视频加入收藏夹")

    def _remove_from_favorite(self, videos: list[Video], containing: set[int]) -> None:
        if self._view == VIEW_FAVORITES and self._favorite_list_id is not None:
            list_id = self._favorite_list_id
            for v in videos:
                self._repo.remove_favorite(v.id, list_id)
            self._rebuild_favorites_menu()
            self.model.show(VIEW_FAVORITES, favorite_list_id=list_id)
            self.statusBar().showMessage(f"已将 {len(videos)} 个视频移出收藏夹")
            return
        dialog = PickFavoriteListDialog(
            self._repo, "从收藏夹移除", video_ids=[v.id for v in videos], parent=self
        )
        if dialog.exec() and dialog.selected_list_id() is not None:
            list_id = dialog.selected_list_id()
            for v in videos:
                self._repo.remove_favorite(v.id, list_id)
            self._rebuild_favorites_menu()
            if self._view == VIEW_FAVORITES and self._favorite_list_id == list_id:
                self.model.show(VIEW_FAVORITES, favorite_list_id=list_id)
            self.statusBar().showMessage(f"已将 {len(videos)} 个视频移出收藏夹")

    def _assign_category(self, videos: list[Video]) -> None:
        if self._current_root is None:
            self.statusBar().showMessage("请先扫描一个目录")
            return
        in_root = [v for v in videos if _under_root(v.filepath, self._current_root)]
        if not in_root:
            self.statusBar().showMessage("所选视频都不在当前扫描目录下，无法分配分类")
            return
        dialog = PickCategoryDialog(self._repo, "添加到分类", root=self._current_root, parent=self)
        if dialog.exec() and dialog.selected_category_id() is not None:
            self._repo.assign_batch([v.id for v in in_root], dialog.selected_category_id())
            if len(in_root) < len(videos):
                self.statusBar().showMessage(
                    f"已将 {len(in_root)} 个视频添加到分类（跳过 {len(videos) - len(in_root)} 个其他目录的视频）"
                )
            else:
                self.statusBar().showMessage(f"已将 {len(in_root)} 个视频添加到分类")

    def _unassign_category(self, videos: list[Video]) -> None:
        if self._current_root is None:
            self.statusBar().showMessage("请先扫描一个目录")
            return
        dialog = PickCategoryDialog(
            self._repo, "从分类移除", root=self._current_root, video_ids=[v.id for v in videos], parent=self
        )
        if dialog.exec() and dialog.selected_category_id() is not None:
            self._repo.unassign_batch([v.id for v in videos], dialog.selected_category_id())
            self.statusBar().showMessage(f"已从分类移除 {len(videos)} 个视频")
        self._refresh_all()

    def _reveal(self, video: Video) -> None:
        subprocess.Popen(["explorer", "/select,", video.filepath])
