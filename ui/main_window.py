import os
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import config
from domain.models import Video
from domain.repository import Repository
from services.watcher import WatcherThread
from ui.category_tree import CategoryTree
from ui.dialogs.pick_category import PickCategoryDialog
from ui.player import PlayerWindow
from ui.scan_worker import ScanWorker
from ui.search_bar import SearchBar
from ui.video_list import VideoTableModel

VIEW_ALL = "all"
VIEW_FAVORITES = "favorites"
VIEW_RECENT = "recent"


class MainWindow(QMainWindow):
    def __init__(self, repo: Repository):
        super().__init__()
        self._repo = repo
        self._view = VIEW_ALL
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
        self.model.refresh()

        root = config.load_settings().get("watch_root")
        if root and os.path.isdir(root):
            self._start_watcher(root, resume=True)

    def closeEvent(self, event) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher.wait(3000)
        super().closeEvent(event)

    # ---------- UI construction ----------

    def _build_toolbar(self) -> None:
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.addAction("扫描目录", self._pick_and_scan)
        tb.addAction("刷新", self._refresh_all)
        tb.addSeparator()
        tb.addAction("全部视频", lambda: self._set_view(VIEW_ALL))
        tb.addAction("最近播放", lambda: self._set_view(VIEW_RECENT))
        tb.addAction("收藏夹", lambda: self._set_view(VIEW_FAVORITES))
        self.addToolBar(tb)

    def _build_body(self) -> None:
        self.tree = CategoryTree(self._repo)
        self.tree.category_selected.connect(self._on_category_selected)

        self.search = SearchBar()
        self.search.search_submitted.connect(self._on_search)

        self.table = QTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(64)
        self.table.setColumnWidth(0, 100)
        self.table.setColumnWidth(1, 320)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda idx: self._play(self._video_at(idx)))
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
        else:
            self.model.refresh()

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
        self.statusBar().showMessage(f"正在扫描 {root} ...")
        worker = ScanWorker(root, self._repo, self)
        worker.message.connect(self.statusBar().showMessage)
        worker.done.connect(lambda: self._on_scan_done(root))
        self._scanner = worker
        worker.start()

    def _on_scan_done(self, root: str) -> None:
        self._scanner = None
        self._refresh_all()
        config.save_setting("watch_root", root)
        self._start_watcher(root)
        self.statusBar().showMessage("扫描完成，已开启增量监控")

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
        if self._view == VIEW_ALL and not self.search.text():
            self.model.refresh()

    def _on_search(self, text: str) -> None:
        self._view = VIEW_ALL
        self.tree.clearSelection()
        self.model.refresh(text)

    def _on_category_selected(self, category_id: int | None) -> None:
        self._view = VIEW_ALL
        self.search.clear()
        self.model.refresh(category_id=category_id)
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
        dialog = PickCategoryDialog(self._repo, "添加到分类", self)
        if dialog.exec() and dialog.selected_category_id() is not None:
            self._repo.assign_batch([v.id for v in videos], dialog.selected_category_id())
            self.statusBar().showMessage(f"已将 {len(videos)} 个视频添加到分类")

    def _unassign_category(self, videos: list[Video]) -> None:
        dialog = PickCategoryDialog(self._repo, "从分类移除", self)
        if dialog.exec() and dialog.selected_category_id() is not None:
            self._repo.unassign_batch([v.id for v in videos], dialog.selected_category_id())
            self.statusBar().showMessage(f"已从分类移除 {len(videos)} 个视频")
        self._refresh_all()

    def _reveal(self, video: Video) -> None:
        subprocess.Popen(["explorer", "/select,", video.filepath])
