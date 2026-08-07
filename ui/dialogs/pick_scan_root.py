import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from domain.repository import Repository
from ui.delete_worker import DeleteWorker


class PickScanRootDialog(QDialog):
    """List scanned roots and delete the selected one.

    仅移除记录: forget the root, keep its videos.
    删除并清除数据: also delete all videos under the root (favorites/categories
    cascade) — runs in a background worker with a progress dialog; the root
    entry is removed only after the data is gone, so an interrupted run can
    be retried from this same list.
    """

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self._repo = repo
        self.deleted_roots: list[str] = []
        self._worker: DeleteWorker | None = None
        self.setWindowTitle("移除扫描数据")
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
            self.list.addItem("(暂无已扫描目录)")
        for r in roots:
            self.list.addItem(r)

    def _selected_root(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            QMessageBox.warning(self, "移除扫描数据", "请先选择一个目录")
            return None
        return item.text()

    def closeEvent(self, event) -> None:
        if self._worker is not None and not self._worker.isFinished():
            event.ignore()
            return
        super().closeEvent(event)

    def _delete_only(self) -> None:
        root = self._selected_root()
        if root is None:
            return
        reply = QMessageBox.question(
            self,
            "移除扫描数据",
            f"确定仅移除「{root}」的扫描记录？\n该目录下的视频仍保留在库中。",
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
            "移除扫描数据",
            f"确定删除「{root}」及其全部数据？\n"
            f"该目录下所有视频条目、收藏和分类关联都将被清除，且无法恢复！",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_delete(root)

    def _start_delete(self, root: str) -> None:
        dialog = QProgressDialog(
            f"正在删除 {root} 的数据...", "取消删除", 0, 0, self
        )
        dialog.setWindowTitle("删除进度")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self._cancel_delete)
        dialog.show()

        worker = DeleteWorker(root, self._repo, self)
        worker.progress.connect(
            lambda done, total, fp: self._on_delete_progress(dialog, done, total, fp)
        )
        worker.error.connect(
            lambda msg: QMessageBox.warning(self, "移除扫描数据", f"删除出错:\n{msg}")
        )
        worker.done.connect(
            lambda deleted, root_removed: self._on_delete_done(
                root, dialog, deleted, root_removed
            )
        )
        self._worker = worker
        self._delete_dialog = dialog
        self.btn_only.setEnabled(False)
        self.btn_full.setEnabled(False)
        worker.start()

    def _cancel_delete(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _on_delete_progress(
        self, dialog: QProgressDialog, done: int, total: int, fp: str
    ) -> None:
        dialog.setRange(0, total)
        dialog.setValue(done)
        dialog.setLabelText(f"正在清理缩略图 {done}/{total}:\n{os.path.basename(fp)}")

    def _on_delete_done(
        self, root: str, dialog: QProgressDialog, deleted: int, root_removed: bool
    ) -> None:
        dialog.close()
        dialog.deleteLater()
        self._worker = None
        self.btn_only.setEnabled(True)
        self.btn_full.setEnabled(True)
        if root_removed:
            self.deleted_roots.append(root)
        self._reload()
