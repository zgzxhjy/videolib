import pytest
from PyQt6.QtCore import Qt
from tests.helpers import mk_video, wait_for


def test_pick_favorite_list_lists_all(qapp, app_env):
    from ui.dialogs.pick_favorite_list import PickFavoriteListDialog

    l1 = app_env.create_favorite_list("收藏夹_一")
    l2 = app_env.create_favorite_list("收藏夹_二")
    mk_video(app_env, r"D:\v\a.mp4")
    a = app_env.get_by_path(r"D:\v\a.mp4")
    app_env.add_favorite(a.id, l1.id)

    dialog = PickFavoriteListDialog(app_env, "添加到收藏夹")
    assert dialog.list.count() == 2
    ids = {
        dialog.list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(dialog.list.count())
    }
    assert ids == {l1.id, l2.id}
    dialog.list.setCurrentRow(0)
    dialog._on_ok()
    assert dialog.selected_list_id() in ids


def test_pick_favorite_list_filters_by_video(qapp, app_env):
    from ui.dialogs.pick_favorite_list import PickFavoriteListDialog

    l1 = app_env.create_favorite_list("收藏夹_一")
    l2 = app_env.create_favorite_list("收藏夹_二")
    mk_video(app_env, r"D:\v\a.mp4")
    a = app_env.get_by_path(r"D:\v\a.mp4")
    app_env.add_favorite(a.id, l1.id)

    dialog = PickFavoriteListDialog(app_env, "从收藏夹移除", video_ids=[a.id])
    assert dialog.list.count() == 1
    assert dialog.list.item(0).data(Qt.ItemDataRole.UserRole) == l1.id
    assert dialog.list.item(0).text() == "收藏夹_一 (1)"


def test_pick_favorite_list_create_inline(qapp, app_env, monkeypatch):
    from ui.dialogs.pick_favorite_list import PickFavoriteListDialog, normalize_favorite_name

    assert normalize_favorite_name("动作片") == "收藏夹_动作片"
    assert normalize_favorite_name("收藏夹_动作片") == "收藏夹_动作片"

    dialog = PickFavoriteListDialog(app_env, "添加到收藏夹")
    monkeypatch.setattr(
        "ui.dialogs.pick_favorite_list.QInputDialog.getText",
        lambda *a, **k: ("恐怖片", True),
    )
    dialog._create_list()
    names = [l.name for l in app_env.get_favorite_lists()]
    assert "收藏夹_恐怖片" in names


def test_pick_favorite_list_delete_mode(qapp, app_env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from ui.dialogs.pick_favorite_list import PickFavoriteListDialog

    l1 = app_env.create_favorite_list("收藏夹_一")
    l2 = app_env.create_favorite_list("收藏夹_二")
    dialog = PickFavoriteListDialog(app_env, "删除收藏夹", delete_mode=True)
    assert not dialog.btn_new.isVisible()
    monkeypatch.setattr(
        "ui.dialogs.pick_favorite_list.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    dialog.list.setCurrentRow(0)
    dialog._delete_selected()
    assert dialog.deleted_ids == [l1.id]
    remaining = [l.id for l in app_env.get_favorite_lists()]
    assert remaining == [l2.id]
    assert dialog.list.count() == 1
    dialog.list.setCurrentRow(0)
    dialog._delete_selected()
    assert dialog.deleted_ids == [l1.id, l2.id]
    assert app_env.get_favorite_lists() == []


def test_category_tree_drop_assigns_videos(qapp, app_env):
    """Dropping video ids on a category must assign them."""
    from ui.category_tree import CategoryTree

    mk_video(app_env, r"D:\v\a.mp4")
    mk_video(app_env, r"D:\v\b.mp4")
    a = app_env.get_by_path(r"D:\v\a.mp4")
    b = app_env.get_by_path(r"D:\v\b.mp4")
    cat = app_env.add_category("动作", root=r"D:\v")

    tree = CategoryTree(app_env)
    try:
        tree._assign_dropped(cat.id, [a.id, b.id])
        got = {v.id for v in app_env.videos_in_category(cat.id)}
        assert got == {a.id, b.id}
        direct = {v.id for v in app_env.videos_in_category(cat.id, include_descendants=False)}
        assert direct == got
    finally:
        tree.close()


def test_pick_scan_root_dialog(qapp, app_env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from ui.dialogs.pick_scan_root import PickScanRootDialog

    app_env.register_scan(r"D:\a")
    app_env.register_scan(r"D:\b")
    mk_video(app_env, r"D:\a\a.mp4")

    dialog = PickScanRootDialog(app_env)
    assert dialog.list.count() == 2
    monkeypatch.setattr(
        "ui.dialogs.pick_scan_root.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    # find row for D:\b and delete only the record
    for i in range(dialog.list.count()):
        if dialog.list.item(i).text() == r"D:\b":
            dialog.list.setCurrentRow(i)
            break
    dialog._delete_only()
    assert dialog.deleted_roots == [r"D:\b"]
    assert r"D:\b" not in app_env.get_scan_roots()
    assert app_env.get_by_path(r"D:\a\a.mp4") is not None

    # delete D:\a together with its data (background worker)
    dialog.list.setCurrentRow(0)
    dialog._delete_with_data()
    assert wait_for(
        lambda: dialog._worker is not None and dialog._worker.isFinished()
    ), "delete worker did not finish"
    for _ in range(20):
        qapp.processEvents()
    assert dialog.deleted_roots == [r"D:\b", r"D:\a"]
    assert app_env.get_scan_roots() == []
    assert app_env.get_by_path(r"D:\a\a.mp4") is None
