import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QInputDialog,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
)

from domain.repository import Repository
from ui.video_list import MIME_VIDEO_IDS

_ROOT_ROLE = int(Qt.ItemDataRole.UserRole) + 1  # holds the scan root of a category item


class CategoryTree(QTreeWidget):
    """Hierarchical category sidebar for one scan root. Emits categorySelected(None) for root view."""

    category_selected = pyqtSignal(object)  # int | None

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self._repo = repo
        self._root = ""
        self.setHeaderHidden(True)
        self.setMinimumWidth(180)
        self.itemClicked.connect(self._on_click)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemExpanded.connect(lambda _: self._cache_expanded())
        self.itemCollapsed.connect(lambda _: self._cache_expanded())
        self.setAcceptDrops(True)
        self._expanded: set[int] = set()
        self.reload(self._root)

    # ---------- population ----------

    def reload(self, root: str | None = None) -> None:
        if root is not None:
            self._root = root
        self.blockSignals(True)
        self.clear()
        label = os.path.basename(os.path.normpath(self._root)) if self._root else "全部视频"
        root_item = QTreeWidgetItem([label])
        root_item.setData(0, Qt.ItemDataRole.UserRole, None)
        root_item.setExpanded(True)
        self.addTopLevelItem(root_item)

        cats = self._repo.get_categories(self._root or None)
        by_parent: dict[tuple[str, int | None], list] = {}
        for c in cats:
            by_parent.setdefault((c.root, c.parent_id), []).append(c)

        if self._root:
            # single scan root: its categories hang directly off the root item
            self._add_children(root_item, by_parent, None, self._root)
        else:
            # all-dirs view: group categories under one node per scan root
            for group_root in sorted({r for r, _p in by_parent}):
                group = QTreeWidgetItem([os.path.basename(os.path.normpath(group_root)) or group_root])
                group.setData(0, Qt.ItemDataRole.UserRole, group_root)
                group.setData(0, _ROOT_ROLE, group_root)
                group.setToolTip(0, group_root)
                root_item.addChild(group)
                self._add_children(group, by_parent, None, group_root)

        self.blockSignals(False)

    def _add_children(
        self, parent_item: QTreeWidgetItem, by_parent: dict, parent_id: int | None, root: str
    ) -> None:
        for c in sorted(by_parent.get((root, parent_id), []), key=lambda c: c.name):
            item = QTreeWidgetItem([c.name])
            item.setData(0, Qt.ItemDataRole.UserRole, c.id)
            item.setData(0, _ROOT_ROLE, c.root)
            parent_item.addChild(item)
            if c.id in self._expanded:
                item.setExpanded(True)
            self._add_children(item, by_parent, c.id, root)

    def _cache_expanded(self) -> None:
        self._expanded = set()
        it = QTreeWidgetItemIterator(self)
        while it.value():
            item = it.value()
            cid = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(cid, int) and item.isExpanded():
                self._expanded.add(cid)
            it += 1

    # ---------- interaction ----------

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None or isinstance(data, int):
            self.category_selected.emit(data)

    def _selected_category(self) -> tuple[QTreeWidgetItem, int | None]:
        item = self.currentItem()
        if item is None:
            return None, None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return item, data if isinstance(data, int) else None

    def _show_menu(self, pos) -> None:
        item = self.currentItem()
        menu = QMenu(self)
        act_add = menu.addAction("新建子分类")
        act_rename = menu.addAction("重命名")
        act_del = menu.addAction("删除")
        cid = None
        if item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, int):
                cid = data
        if cid is None:
            act_rename.setEnabled(False)
            act_del.setEnabled(False)
        act = menu.exec(self.viewport().mapToGlobal(pos))
        if act == act_add:
            self._add_child(item, cid)
        elif act == act_rename and cid is not None:
            self._rename(item, cid)
        elif act == act_del and cid is not None:
            self._delete(cid)

    def _add_child(self, parent_item: QTreeWidgetItem, parent_id: int | None) -> None:
        name, ok = QInputDialog.getText(self, "新建分类", "分类名称:")
        if not ok or not name.strip():
            return
        root = self._root
        if parent_item is not None:
            root = parent_item.data(0, _ROOT_ROLE) or self._root
        self._repo.add_category(name.strip(), parent_id, root=root)
        self.reload()

    def _rename(self, item: QTreeWidgetItem, cid: int) -> None:
        name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=item.text(0))
        if ok and name.strip():
            self._repo.rename_category(cid, name.strip())
            self.reload()

    def _delete(self, cid: int) -> None:
        reply = QMessageBox.question(self, "删除分类", "确定删除该分类及其子分类？(视频文件不受影响)")
        if reply == QMessageBox.StandardButton.Yes:
            self._repo.delete_category(cid)
            self.reload()
            self.category_selected.emit(None)

    # ---------- drop target for rows dragged from the video list ----------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_VIDEO_IDS):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_VIDEO_IDS):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat(MIME_VIDEO_IDS):
            super().dropEvent(event)
            return
        ids = [
            int(x) for x in bytes(event.mimeData().data(MIME_VIDEO_IDS)).decode().split(",") if x
        ]
        item = self.itemAt(event.position().toPoint())
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        cid = data if isinstance(data, int) else None
        if not ids or cid is None:
            event.ignore()
            return
        self._assign_dropped(cid, ids)
        event.acceptProposedAction()

    def _assign_dropped(self, category_id: int, video_ids: list[int]) -> None:
        self._repo.assign_batch(video_ids, category_id)
        self.reload()
        self.category_selected.emit(category_id)
