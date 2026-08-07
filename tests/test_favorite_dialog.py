import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def fav_env(tmp_path):
    from domain.repository import Repository

    repo = Repository(tmp_path / "db.sqlite")
    yield repo
    repo.close()


def _mk_video(repo, filepath):
    from domain.models import Video

    repo.upsert_videos([Video(filename=Path(filepath).name, filepath=filepath)])


def _wait_for(predicate, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_pick_favorite_list_lists_all(qapp, fav_env):
    from ui.dialogs.pick_favorite_list import PickFavoriteListDialog

    l1 = fav_env.create_favorite_list("收藏夹_一")
    l2 = fav_env.create_favorite_list("收藏夹_二")
    _mk_video(fav_env, r"D:\v\a.mp4")
    a = fav_env.get_by_path(r"D:\v\a.mp4")
    fav_env.add_favorite(a.id, l1.id)

    dialog = PickFavoriteListDialog(fav_env, "添加到收藏夹")
    assert dialog.list.count() == 2
    ids = {
        dialog.list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(dialog.list.count())
    }
    assert ids == {l1.id, l2.id}
    dialog.list.setCurrentRow(0)
    dialog._on_ok()
    assert dialog.selected_list_id() in ids


def test_pick_favorite_list_filters_by_video(qapp, fav_env):
    from ui.dialogs.pick_favorite_list import PickFavoriteListDialog

    l1 = fav_env.create_favorite_list("收藏夹_一")
    l2 = fav_env.create_favorite_list("收藏夹_二")
    _mk_video(fav_env, r"D:\v\a.mp4")
    a = fav_env.get_by_path(r"D:\v\a.mp4")
    fav_env.add_favorite(a.id, l1.id)

    dialog = PickFavoriteListDialog(fav_env, "从收藏夹移除", video_ids=[a.id])
    assert dialog.list.count() == 1
    assert dialog.list.item(0).data(Qt.ItemDataRole.UserRole) == l1.id
    assert dialog.list.item(0).text() == "收藏夹_一 (1)"


def test_pick_favorite_list_create_inline(qapp, fav_env, monkeypatch):
    from ui.dialogs.pick_favorite_list import PickFavoriteListDialog, normalize_favorite_name

    assert normalize_favorite_name("动作片") == "收藏夹_动作片"
    assert normalize_favorite_name("收藏夹_动作片") == "收藏夹_动作片"

    dialog = PickFavoriteListDialog(fav_env, "添加到收藏夹")
    monkeypatch.setattr(
        "ui.dialogs.pick_favorite_list.QInputDialog.getText",
        lambda *a, **k: ("恐怖片", True),
    )
    dialog._create_list()
    names = [l.name for l in fav_env.get_favorite_lists()]
    assert "收藏夹_恐怖片" in names


def test_pick_favorite_list_delete_mode(qapp, fav_env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from ui.dialogs.pick_favorite_list import PickFavoriteListDialog

    l1 = fav_env.create_favorite_list("收藏夹_一")
    l2 = fav_env.create_favorite_list("收藏夹_二")
    dialog = PickFavoriteListDialog(fav_env, "删除收藏夹", delete_mode=True)
    assert not dialog.btn_new.isVisible()
    monkeypatch.setattr(
        "ui.dialogs.pick_favorite_list.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    dialog.list.setCurrentRow(0)
    dialog._delete_selected()
    assert dialog.deleted_ids == [l1.id]
    remaining = [l.id for l in fav_env.get_favorite_lists()]
    assert remaining == [l2.id]
    assert dialog.list.count() == 1
    dialog.list.setCurrentRow(0)
    dialog._delete_selected()
    assert dialog.deleted_ids == [l1.id, l2.id]
    assert fav_env.get_favorite_lists() == []


def test_category_tree_drop_assigns_videos(qapp, fav_env):
    """Dropping video ids on a category must assign them."""
    from ui.category_tree import CategoryTree

    _mk_video(fav_env, r"D:\v\a.mp4")
    _mk_video(fav_env, r"D:\v\b.mp4")
    a = fav_env.get_by_path(r"D:\v\a.mp4")
    b = fav_env.get_by_path(r"D:\v\b.mp4")
    cat = fav_env.add_category("动作", root=r"D:\v")

    tree = CategoryTree(fav_env)
    try:
        tree._assign_dropped(cat.id, [a.id, b.id])
        got = {v.id for v in fav_env.videos_in_category(cat.id)}
        assert got == {a.id, b.id}
        direct = {v.id for v in fav_env.videos_in_category(cat.id, include_descendants=False)}
        assert direct == got
    finally:
        tree.close()


def test_pick_scan_root_dialog(qapp, fav_env, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from ui.dialogs.pick_scan_root import PickScanRootDialog

    fav_env.register_scan(r"D:\a")
    fav_env.register_scan(r"D:\b")
    _mk_video(fav_env, r"D:\a\a.mp4")

    dialog = PickScanRootDialog(fav_env)
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
    assert r"D:\b" not in fav_env.get_scan_roots()
    assert fav_env.get_by_path(r"D:\a\a.mp4") is not None

    # delete D:\a together with its data (background worker)
    dialog.list.setCurrentRow(0)
    dialog._delete_with_data()
    assert _wait_for(
        lambda: dialog._worker is not None and dialog._worker.isFinished()
    ), "delete worker did not finish"
    for _ in range(20):
        qapp.processEvents()
    assert dialog.deleted_roots == [r"D:\b", r"D:\a"]
    assert fav_env.get_scan_roots() == []
    assert fav_env.get_by_path(r"D:\a\a.mp4") is None
