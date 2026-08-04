from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from domain.repository import Repository

FAVORITE_PREFIX = "收藏夹_"


def normalize_favorite_name(name: str) -> str:
    name = name.strip()
    if not name.startswith(FAVORITE_PREFIX):
        return FAVORITE_PREFIX + name
    return name


class PickFavoriteListDialog(QDialog):
    """Choose one favorite list. Can create a new one inline.

    When video_ids is given, only lists containing those videos are listed
    (for remove-from-favorites flows).
    """

    def __init__(
        self,
        repo: Repository,
        title: str,
        video_ids: list[int] | None = None,
        parent=None,
        delete_mode: bool = False,
    ):
        super().__init__(parent)
        self._repo = repo
        self._video_ids = video_ids
        self._selected: int | None = None
        self.deleted_ids: list[int] = []
        self._delete_mode = delete_mode
        self.setWindowTitle(title)
        self.setMinimumWidth(320)

        self.list = QListWidget()
        self.btn_new = QPushButton("＋ 新建收藏夹")
        self.btn_new.clicked.connect(self._create_list)
        self.btn_delete = QPushButton("删除选中")
        self.btn_delete.clicked.connect(self._delete_selected)
        if delete_mode:
            self.btn_new.hide()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        bottom = QHBoxLayout()
        if delete_mode:
            bottom.addWidget(self.btn_delete)
        else:
            bottom.addWidget(self.btn_new)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addLayout(bottom)

        self._reload()

    def _delete_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            QMessageBox.warning(self, "删除收藏夹", "请先选择一个收藏夹")
            return
        list_id = item.data(Qt.ItemDataRole.UserRole)
        name = item.text().split(" (")[0]
        reply = QMessageBox.question(
            self,
            "删除收藏夹",
            f"确定删除收藏夹「{name}」？\n收藏记录将被清除，视频文件不受影响。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._repo.delete_favorite_list(list_id)
        self.deleted_ids.append(list_id)
        self._reload()

    # ---------- population ----------

    def _reload(self) -> None:
        self.list.clear()
        lists = self._repo.get_favorite_lists()
        if self._video_ids:
            own_ids: set[int] = set()
            for vid in self._video_ids:
                own_ids.update(l.id for l in self._repo.lists_of_video(vid))
            lists = [l for l in lists if l.id in own_ids]
            if not lists:
                self.list.addItem("(所选视频不在任何收藏夹中)")
                return
        for l in lists:
            count = self._repo.count_favorites(l.id)
            self.list.addItem(f"{l.name} ({count})")
            self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, l.id)

    def _create_list(self) -> None:
        name, ok = QInputDialog.getText(
            self, "新建收藏夹", f"收藏夹名称（自动补前缀 {FAVORITE_PREFIX}）:"
        )
        if not ok or not name.strip():
            return
        full = normalize_favorite_name(name)
        try:
            created = self._repo.create_favorite_list(full)
        except ValueError as e:
            QMessageBox.warning(self, "新建收藏夹", str(e))
            return
        self._reload()
        if not self._video_ids:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) == created.id:
                    self.list.setCurrentRow(i)
                    break

    def _on_ok(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_list_id(self) -> int | None:
        return self._selected
