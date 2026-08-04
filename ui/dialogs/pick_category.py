from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from domain.repository import Repository


class PickCategoryDialog(QDialog):
    """Choose one category. Can create a new one inline.

    When video_ids is given, only categories those videos belong to
    are listed (for unassign flows).
    """

    def __init__(
        self,
        repo: Repository,
        title: str,
        root: str = "",
        video_ids: list[int] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._repo = repo
        self._root = root
        self._video_ids = video_ids
        self._selected: int | None = None
        self.setWindowTitle(title)
        self.setMinimumWidth(320)

        self.list = QListWidget()
        self.btn_new = QPushButton("＋ 新建分类")
        self.btn_new.clicked.connect(self._create_category)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_new)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addLayout(bottom)

        self._reload()

    # ---------- population ----------

    def _reload(self) -> None:
        self.list.clear()
        cats = self._repo.get_categories(self._root)
        if self._video_ids:
            own_ids = set()
            for vid in self._video_ids:
                own_ids.update(c.id for c in self._repo.categories_of_video(vid))
            cats = [c for c in cats if c.id in own_ids]
            if not cats:
                self.list.addItem("(所选视频未加入任何分类)")
                return
        else:
            self.list.addItem("(不选择)")

        children: dict[int | None, list] = {}
        for c in cats:
            children.setdefault(c.parent_id, []).append(c)

        def walk(parent_id: int | None, prefix: str) -> None:
            for c in sorted(children.get(parent_id, []), key=lambda x: x.name):
                self.list.addItem(f"{prefix}{c.name}")
                self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, c.id)
                walk(c.id, prefix + "  ")

        walk(None, "")

    def _create_category(self) -> None:
        name, ok = QInputDialog.getText(self, "新建分类", "分类名称:")
        if not ok or not name.strip():
            return
        self._repo.add_category(name.strip(), root=self._root)
        self._reload()

    def _on_ok(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_category_id(self) -> int | None:
        return self._selected
