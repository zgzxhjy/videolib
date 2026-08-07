import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidgetItemIterator


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


def test_all_dirs_view_groups_categories_by_root(qapp, repo):
    tree = _fill(repo)
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


def test_single_root_view_hangs_categories_off_root_item(qapp, repo):
    tree = _fill(repo)
    tree.reload(r"D:\a")

    items = _tree_items(tree)
    assert items[0][0] == "a", "root item is named after the root"
    texts = [t for t, _r, _rr, _p in items]
    assert texts == ["a", "甲", "甲子"], "no grouping node in single-root view"


def test_group_node_click_emits_nothing(qapp, repo):
    tree = _fill(repo)
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


def test_group_node_has_no_category_id_for_menu_and_drop(qapp, repo):
    tree = _fill(repo)
    tree.reload("")

    tree.setCurrentItem(next(
        i for i in _iter_items(tree)
        if isinstance(i.data(0, Qt.ItemDataRole.UserRole), str)
    ))
    item, cid = tree._selected_category()
    assert item is not None and cid is None


@pytest.fixture()
def _named_input(monkeypatch):
    """New-category dialogs answer with a fixed name."""
    from ui.category_tree import QInputDialog

    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *a, **k: ("新分类", True)),
    )
    return QInputDialog


def test_all_view_root_item_insert_creates_global_category(qapp, repo, _named_input):
    """Right-clicking the 全部视频 root item must create a global category."""
    from ui.category_tree import ALL_CATEGORIES_ROOT, CategoryTree

    tree = CategoryTree(repo)
    tree.reload("")
    tree._add_child(tree.topLevelItem(0), None)

    cats = repo.get_categories(None)
    assert len(cats) == 1 and cats[0].root == ALL_CATEGORIES_ROOT

    items = _tree_items(tree)
    assert items[0][0] == "全部视频"
    assert items[1][0] == "新分类"
    assert items[1][3] == "全部视频", "global category hangs directly under the root item"


def test_all_view_blank_insert_creates_global_category(qapp, repo, _named_input):
    """A blank-area insert in the all view is the global scope too."""
    from ui.category_tree import ALL_CATEGORIES_ROOT, CategoryTree

    tree = CategoryTree(repo)
    tree.reload("")
    tree._add_child(None, None)

    cats = repo.get_categories(None)
    assert len(cats) == 1 and cats[0].root == ALL_CATEGORIES_ROOT


def test_single_root_view_root_item_insert_binds_current_root(qapp, repo, _named_input):
    """Single-root view: root item/blank inserts stay in that root."""
    from ui.category_tree import CategoryTree

    tree = CategoryTree(repo)
    tree.reload(r"D:\a")
    tree._add_child(tree.topLevelItem(0), None)
    tree._add_child(None, None)

    roots = [c.root for c in repo.get_categories(None)]
    assert roots == [r"D:\a", r"D:\a"]


def test_group_and_category_inserts_still_bind_their_root(qapp, repo, _named_input):
    """Group/category nodes keep binding to their own scan root (regression)."""
    from ui.category_tree import CategoryTree

    tree = _fill(repo)
    tree.reload("")
    group = next(
        i for i in _iter_items(tree)
        if isinstance(i.data(0, Qt.ItemDataRole.UserRole), str)
    )
    tree._add_child(group, None)
    category = next(
        i for i in _iter_items(tree)
        if isinstance(i.data(0, Qt.ItemDataRole.UserRole), int)
    )
    tree._add_child(category, category.data(0, Qt.ItemDataRole.UserRole))

    new_roots = [c.root for c in repo.get_categories(None) if c.name == "新分类"]
    assert new_roots == [r"D:\a", r"D:\a"]


def test_legacy_empty_root_categories_render_under_all_root_item(qapp, repo):
    """Legacy root='' categories must not become an empty-label group."""
    from ui.category_tree import CategoryTree

    repo.add_category("遗留", root="")
    tree = CategoryTree(repo)
    tree.reload("")

    items = _tree_items(tree)
    assert items[1][0] == "遗留"
    assert items[1][3] == "全部视频"
    assert all(not (isinstance(r, str) and t == "") for t, r, _rr, _p in items)


def test_drop_on_global_category_accepts_cross_root_videos(qapp, repo):
    """Dragging rows from any scan root onto a global category must assign."""
    from PyQt6.QtCore import QMimeData, QPointF, Qt
    from PyQt6.QtGui import QDropEvent

    from tests.helpers import mk_video
    from ui.category_tree import ALL_CATEGORIES_ROOT, CategoryTree
    from ui.video_list import MIME_VIDEO_IDS

    repo.register_scan(r"D:\a")
    repo.register_scan(r"D:\b")
    mk_video(repo, "D:/a/one.mp4")
    mk_video(repo, "D:/b/two.mp4")
    a = repo.get_by_path("D:/a/one.mp4")
    b = repo.get_by_path("D:/b/two.mp4")
    cat = repo.add_category("跨目录", root=ALL_CATEGORIES_ROOT)

    tree = CategoryTree(repo)
    tree.reload("")
    cat_item = next(
        i for i in _iter_items(tree)
        if i.data(0, Qt.ItemDataRole.UserRole) == cat.id
    )
    tree.itemAt = lambda _pos: cat_item

    md = QMimeData()
    md.setData(MIME_VIDEO_IDS, f"{a.id},{b.id}".encode())
    event = QDropEvent(
        QPointF(10, 10), Qt.DropAction.CopyAction, md,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    tree.dropEvent(event)
    assert {v.id for v in repo.videos_in_category(cat.id)} == {a.id, b.id}
