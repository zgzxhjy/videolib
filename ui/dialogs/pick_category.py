from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QVBoxLayout

from domain.repository import Repository


class PickCategoryDialog(QDialog):
    """Choose one category from the whole hierarchy (shown as path)."""

    def __init__(self, repo: Repository, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(300)
        self._selected: int | None = None

        self.list = QListWidget()
        self.list.addItem("(不选择)")
        children: dict[int | None, list] = {}
        for c in repo.get_categories():
            children.setdefault(c.parent_id, []).append(c)
        by_id = {c.id: c for c in repo.get_categories()}

        def walk(parent_id: int | None, prefix: str) -> None:
            for c in sorted(children.get(parent_id, []), key=lambda x: x.name):
                label = f"{prefix}{c.name}"
                self.list.addItem(label)
                self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, c.id)
                walk(c.id, prefix + "  ")

        walk(None, "")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def _on_ok(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_category_id(self) -> int | None:
        return self._selected
