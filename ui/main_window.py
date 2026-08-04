import os
import subprocess

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
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
from ui.player import PlayerWindow
from ui.scan_worker import ScanWorker
from ui.search_bar import SearchBar
from ui.video_list import COL_PLAY, COL_THUMB, PlayTableView, VideoTableModel

VIEW_CURRENT = "current"
VIEW_ALL = "all"
VIEW_FAVORITES = "favorites"
VIEW_RECENT = "recent"


def _under_root(path: str, root: str) -> bool:
    p = os.path.normcase(os.path.normpath(path))
    r = os.path.normcase(os.path.normpath(root))
    return p == r or p.startswith(r + os.sep)


class MainWindow(QMainWindow):
    def __init__(self, repo: Repository):
        super().__init__()
        self._repo = repo
        self._view = VIEW_CURRENT
        self._current_root: str | None = None
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
        self.table.verticalHeader().setDefaultSectionSize(THUMB_HEIGHT)
        self.model.refresh()
        self.table.selectionModel().selectionChanged.connect(
            lambda _sel, _desel: self.play_action.setEnabled(bool(self._selected_videos()))
        )
        self._setup_shortcuts()

        Thumbnailer.cleanup_orphans(
            config.THUMBS_DIR, {v.id for v in self._repo.all_videos(10**6)}
        )

        root = config.load_settings().get("watch_root")
        if root and os.path.isdir(root):
            self._activate_root(root)
            self._start_watcher(root, resume=True)

    def _activate_root(self, root: str) -> None:
        """Set the current scan root, adopt legacy categories, switch the tree."""
        self._current_root = root
        self._repo.adopt_legacy_categories(root)
        self.tree.reload(root)

    def closeEvent(self, event) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher.wait(3000)
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
        tb.addAction("收藏夹", lambda: self._set_view(VIEW_FAVORITES))
        tb.addAction("所有目录", lambda: self._set_view(VIEW_ALL))
        self.addToolBar(tb)
        self._rebuild_history_menu()

    def _rebuild_history_menu(self) -> None:
        self._history_menu.clear()
        roots = self._repo.get_scan_roots()
        self._history_action.setEnabled(bool(roots))
        for r in roots:
            self._history_menu.addAction(r, lambda r=r: self._start_scan(r))

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
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(THUMB_HEIGHT)
        self.table.setColumnWidth(COL_THUMB, THUMB_WIDTH)
        self.table.setColumnWidth(1, 320)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_PLAY, QHeaderView.ResizeMode.ResizeToContents)
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

    def _set_view(self, view: str) -> None:
        self._view = view
        self.search.clear()
        self.tree.clearSelection()
        if view == VIEW_FAVORITES:
            self.model.refresh_favorites()
        elif view == VIEW_RECENT:
            self.model.refresh_recent()
        elif view == VIEW_ALL:
            self.model.refresh()
        else:
            self.model.refresh(root=self._current_root)

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
        worker.done.connect(lambda completed: self._on_scan_done(root, dialog, completed))
        self._scanner = worker
        worker.start()

    def _cancel_scan(self) -> None:
        if self._scanner is not None:
            self._scanner.cancel()

    def _on_scan_progress(self, dialog: QProgressDialog, done: int, total: int, fp: str) -> None:
        dialog.setRange(0, total)
        dialog.setValue(done)
        dialog.setLabelText(f"已提取元数据 {done}/{total}:\n{os.path.basename(fp)}")

    def _on_scan_done(self, root: str, dialog: QProgressDialog, completed: bool) -> None:
        self._scanner = None
        dialog.close()
        dialog.deleteLater()
        self._refresh_all()
        if completed:
            self._rebuild_history_menu()
            config.save_setting("watch_root", root)
            self._start_watcher(root)
            self.statusBar().showMessage("扫描完成，已开启增量监控")
        else:
            self.statusBar().showMessage("扫描取消/出错，未开启增量监控")

    def _start_watcher(self, root: str, resume: bool = False) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher.wait(3000)
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
        if self._view == VIEW_CURRENT:
            self.model.refresh(root=self._current_root)
        elif self._view == VIEW_ALL:
            self.model.refresh()
        elif self._view == VIEW_FAVORITES:
            self.model.refresh_favorites()
        elif self._view == VIEW_RECENT:
            self.model.refresh_recent()

    def _on_search(self, text: str) -> None:
        self._view = VIEW_ALL
        self.tree.clearSelection()
        self.model.refresh(text)

    def _on_category_selected(self, category_id: int | None) -> None:
        self._view = VIEW_CURRENT
        self.search.clear()
        self.model.refresh(category_id=category_id, root=self._current_root)
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
            self.model.refresh_recent()

    # ---------- context menu ----------

    def _show_table_menu(self, pos) -> None:
        videos = self._selected_videos()
        if not videos:
            return
        menu = QMenu(self)
        menu.addAction("▶ 播放", lambda: self._play(videos[0]))
        if any(not self._repo.is_favorite(v.id) for v in videos):
            menu.addAction("☆ 收藏", lambda: self._toggle_favorite(videos, True))
        if any(self._repo.is_favorite(v.id) for v in videos):
            menu.addAction("★ 取消收藏", lambda: self._toggle_favorite(videos, False))
        menu.addSeparator()
        menu.addAction("添加到分类...", lambda: self._assign_category(videos))
        menu.addAction("从分类移除...", lambda: self._unassign_category(videos))
        menu.addSeparator()
        menu.addAction("打开所在文件夹", lambda: self._reveal(videos[0]))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _toggle_favorite(self, videos: list[Video], favorite: bool) -> None:
        for v in videos:
            if favorite:
                self._repo.add_favorite(v.id)
            else:
                self._repo.remove_favorite(v.id)
        if self._view == VIEW_FAVORITES:
            self.model.refresh_favorites()

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
