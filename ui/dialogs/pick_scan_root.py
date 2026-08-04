from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from domain.repository import Repository
from services.thumbnailer import Thumbnailer


class PickScanRootDialog(QDialog):
    """List scanned roots and delete the selected one.

    仅移除记录: forget the root, keep its videos.
    删除并清除数据: also delete all videos under the root (favorites/categories cascade).
    """

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self._repo = repo
        self.deleted_roots: list[str] = []
        self.setWindowTitle("删除历史目录")
        self.setMinimumWidth(420)

        self.list = QListWidget()
        self.btn_only = QPushButton("仅移除记录")
        self.btn_only.clicked.connect(self._delete_only)
        self.btn_full = QPushButton("删除并清除数据")
        self.btn_full.clicked.connect(self._delete_with_data)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(lambda _: self.close())

        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_only)
        bottom.addWidget(self.btn_full)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addLayout(bottom)

        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        roots = self._repo.get_scan_roots()
        if not roots:
            self.list.addItem("(暂无历史目录)")
        for r in roots:
            self.list.addItem(r)

    def _selected_root(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            QMessageBox.warning(self, "删除历史目录", "请先选择一个目录")
            return None
        return item.text()

    def _delete_only(self) -> None:
        root = self._selected_root()
        if root is None:
            return
        reply = QMessageBox.question(
            self,
            "删除历史目录",
            f"确定仅移除「{root}」的历史记录？\n该目录下的视频仍保留在库中。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._repo.remove_scan_root(root)
        self.deleted_roots.append(root)
        self._reload()

    def _delete_with_data(self) -> None:
        root = self._selected_root()
        if root is None:
            return
        reply = QMessageBox.question(
            self,
            "删除历史目录",
            f"确定删除「{root}」及其全部数据？\n"
            f"该目录下所有视频条目、收藏和分类关联都将被清除，且无法恢复！",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted_ids = self._repo.remove_videos_under(root)
        Thumbnailer().delete_for(deleted_ids)
        self._repo.remove_scan_root(root)
        self.deleted_roots.append(root)
        self._reload()
