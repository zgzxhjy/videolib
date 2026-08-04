import os
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import config
from domain.repository import Repository
from ui.category_tree import CategoryTree
from ui.scan_worker import ScanWorker
from ui.search_bar import SearchBar
from ui.video_list import VideoTableModel


class MainWindow(QMainWindow):
    def __init__(self, repo: Repository):
        super().__init__()
        self._repo = repo
        self.setWindowTitle(f"{config.APP_NAME} - 视频管理")
        self.resize(1100, 700)

        self._build_toolbar()
        self._build_body()
        self.statusBar().showMessage("就绪")

        self.model = VideoTableModel(repo, config.THUMBS_DIR)
        self.table.setModel(self.model)
        self.model.refresh()

    # ---------- UI construction ----------

    def _build_toolbar(self) -> None:
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.addAction("扫描目录", self._pick_and_scan)
        tb.addAction("刷新", self._refresh_all)
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

    # ---------- actions ----------

    def _pick_and_scan(self) -> None:
        root = QFileDialog.getExistingDirectory(self, "选择要扫描的视频目录")
        if not root:
            return
        self._start_scan(root)

    def _start_scan(self, root: str) -> None:
        self.statusBar().showMessage(f"正在扫描 {root} ...")
        worker = ScanWorker(root, self._repo)
        worker.message.connect(self.statusBar().showMessage)
        worker.done.connect(self._on_scan_done)
        worker.start()

    def _on_scan_done(self) -> None:
        self._refresh_all()
        self.statusBar().showMessage("扫描完成")

    def _refresh_all(self) -> None:
        self.tree.reload()
        self.model.refresh(self.search.text())

    def _on_search(self, text: str) -> None:
        self.model.refresh(text)

    def _on_category_selected(self, category_id: int | None) -> None:
        self.search.clear()
        self.model.refresh(category_id=category_id)
        if category_id is None:
            self.statusBar().showMessage(f"全部视频: {self.model.rowCount()} 个")
        else:
            self.statusBar().showMessage(f"分类内视频: {self.model.rowCount()} 个")
