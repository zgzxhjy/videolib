import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTreeWidgetItemIterator


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def cat_env(tmp_path):
    from domain.repository import Repository

    repo = Repository(tmp_path / "db.sqlite")
    yield repo
    repo.close()


def _tree_items(tree):
    """[(text, user_role, root_role, parent_text)] in DFS order."""
    out = []
    it = QTreeWidgetItemIterator(tree)
    while it.value():
        item = it.value()
        parent = item.parent()
        out.append((
            item.text(0),
            item.data(0, Qt.ItemDataRole.UserRole),
            item.data(0, int(Qt.ItemDataRole.UserRole) + 1),
            parent.text(0) if parent is not None else None,
        ))
        it += 1
    return out


def _fill(repo):
    from ui.category_tree import CategoryTree

    a1 = repo.add_category("甲", root=r"D:\a")
    repo.add_category("甲子", parent_id=a1.id, root=r"D:\a")
    repo.add_category("乙", root=r"D:\b")
    return CategoryTree(repo)


def _iter_items(tree):
    """Yield QTreeWidgetItems in DFS order (QTreeWidgetItemIterator is not iterable)."""
    it = QTreeWidgetItemIterator(tree)
    while it.value():
        yield it.value()
        it += 1


def test_all_dirs_view_groups_categories_by_root(qapp, cat_env):
    tree = _fill(cat_env)
    tree.reload("")

    items = _tree_items(tree)
    assert items[0][0] == "全部视频"
    # group nodes exist, named after the root's basename
    groups = {t for t, role, _rr, _p in items if isinstance(role, str)}
    assert groups == {"a", "b"}
    # categories hang under their root's group
    by = {t: (role, parent) for t, role, _rr, parent in items}
    assert isinstance(by["甲"][0], int)
    assert by["甲"][1] == "a"
    assert by["甲子"][1] == "甲"
    assert by["乙"][1] == "b"
    # each category item carries its own root for future inserts
    roots = {t: _rr for t, _r, _rr, _p in items if _rr is not None}
    assert roots["甲"] == r"D:\a"
    assert roots["甲子"] == r"D:\a"
    assert roots["乙"] == r"D:\b"


def test_single_root_view_hangs_categories_off_root_item(qapp, cat_env):
    tree = _fill(cat_env)
    tree.reload(r"D:\a")

    items = _tree_items(tree)
    assert items[0][0] == "a", "root item is named after the root"
    texts = [t for t, _r, _rr, _p in items]
    assert texts == ["a", "甲", "甲子"], "no grouping node in single-root view"


def test_group_node_click_emits_nothing(qapp, cat_env):
    tree = _fill(cat_env)
    tree.reload("")

    emitted = []
    tree.category_selected.connect(emitted.append)

    group = next(
        i for i in _iter_items(tree)
        if isinstance(i.data(0, Qt.ItemDataRole.UserRole), str)
    )
    tree._on_click(group, 0)
    assert emitted == [], "clicking a group node must not filter"

    category = next(
        i for i in _iter_items(tree)
        if isinstance(i.data(0, Qt.ItemDataRole.UserRole), int)
    )
    tree._on_click(category, 0)
    assert emitted == [category.data(0, Qt.ItemDataRole.UserRole)]


def test_group_node_has_no_category_id_for_menu_and_drop(qapp, cat_env):
    tree = _fill(cat_env)
    tree.reload("")

    tree.setCurrentItem(next(
        i for i in _iter_items(tree)
        if isinstance(i.data(0, Qt.ItemDataRole.UserRole), str)
    ))
    item, cid = tree._selected_category()
    assert item is not None and cid is None
