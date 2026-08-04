import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.models import Category, Video
from domain.repository import Repository


@pytest.fixture()
def repo(tmp_path):
    r = Repository(tmp_path / "test.db")
    yield r
    r.close()


def _mk(filename: str, filepath: str, **kw) -> Video:
    defaults = dict(filename=filename, filepath=filepath, file_size=1024)
    defaults.update(kw)
    return Video(**defaults)


def test_init_creates_tables(repo):
    assert repo.count() == 0


def test_upsert_and_fetch(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4", duration=10.5, codec="h264")])
    v = repo.get_by_path(r"D:\v\a.mp4")
    assert v.filename == "a.mp4"
    assert v.duration == 10.5
    assert repo.count() == 1


def test_upsert_updates_existing(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4")])
    repo.upsert_videos([_mk("a-renamed.mp4", r"D:\v\a.mp4", file_size=2048)])
    v = repo.get_by_path(r"D:\v\a.mp4")
    assert v.filename == "a-renamed.mp4"
    assert v.file_size == 2048
    assert repo.count() == 1


def test_remove_by_paths(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4"), _mk("b.mp4", r"D:\v\b.mp4")])
    assert repo.remove_by_filepaths([r"D:\v\a.mp4"]) == 1
    assert repo.count() == 1
    assert repo.get_by_path(r"D:\v\b.mp4") is not None


def test_search_filename(repo):
    repo.upsert_videos([
        _mk("Matrix.mp4", r"D:\v\Matrix.mp4"),
        _mk("The Matrix Reloaded.mp4", r"D:\v\mr.mp4"),
        _mk("Interstellar.mp4", r"D:\v\interstellar.mp4"),
    ])
    result = repo.search("matrix")
    assert {v.filename for v in result} == {"Matrix.mp4", "The Matrix Reloaded.mp4"}


def test_search_chinese_partial(repo):
    repo.upsert_videos([_mk("电影天堂.mp4", r"D:\v\电影天堂.mp4")])
    result = repo.search("电影")
    assert len(result) == 1
    result = repo.search("天堂")
    assert len(result) == 1
    result = repo.search("不存在的")
    assert result == []


def test_search_limit(repo):
    repo.upsert_videos([_mk(f"f{i}.mp4", rf"D:\v\f{i}.mp4") for i in range(20)])
    assert len(repo.search("f", limit=5)) == 5


def test_fts_sync_after_delete(repo):
    repo.upsert_videos([_mk("zzz.mp4", r"D:\v\zzz.mp4")])
    repo.remove_by_filepaths([r"D:\v\zzz.mp4"])
    assert repo.search("zzz") == []


def test_category_crud(repo):
    root = repo.add_category("电影")
    sub = repo.add_category("动作", parent_id=root.id)
    assert len(repo.get_categories()) == 2
    repo.rename_category(root.id, "影片")
    by_id = {c.id: c for c in repo.get_categories()}
    assert by_id[root.id].name == "影片"
    repo.delete_category(sub.id)
    assert len(repo.get_categories()) == 1


def test_category_move_into_subtree_rejected(repo):
    root = repo.add_category("root")
    sub = repo.add_category("sub", parent_id=root.id)
    leaf = repo.add_category("leaf", parent_id=sub.id)
    with pytest.raises(ValueError):
        repo.move_category(root.id, leaf.id)


def test_assign_and_query_with_descendants(repo):
    root = repo.add_category("root")
    sub = repo.add_category("sub", parent_id=root.id)
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4"), _mk("b.mp4", r"D:\v\b.mp4")])
    a = repo.get_by_path(r"D:\v\a.mp4")
    b = repo.get_by_path(r"D:\v\b.mp4")
    repo.assign_category(a.id, sub.id)
    repo.assign_category(b.id, root.id)
    assert len(repo.videos_in_category(root.id, include_descendants=True)) == 2
    assert len(repo.videos_in_category(root.id, include_descendants=False)) == 1
    cats = repo.categories_of_video(a.id)
    assert [c.id for c in cats] == [sub.id]


def test_assign_batch(repo):
    repo.upsert_videos([_mk(f"f{i}.mp4", rf"D:\v\f{i}.mp4") for i in range(5)])
    cat = repo.add_category("batch")
    ids = [repo.get_by_path(rf"D:\v\f{i}.mp4").id for i in range(5)]
    repo.assign_batch(ids, cat.id)
    assert len(repo.videos_in_category(cat.id)) == 5
    repo.unassign_batch(ids[:2], cat.id)
    assert len(repo.videos_in_category(cat.id)) == 3


def test_favorites(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4")])
    a = repo.get_by_path(r"D:\v\a.mp4")
    assert not repo.is_favorite(a.id)
    repo.add_favorite(a.id)
    assert repo.is_favorite(a.id)
    assert [v.id for v in repo.get_favorites()] == [a.id]
    repo.remove_favorite(a.id)
    assert not repo.is_favorite(a.id)


def test_play_history_and_resume(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4", duration=100.0)])
    a = repo.get_by_path(r"D:\v\a.mp4")
    repo.record_play(a.id, 42.0)
    assert repo.last_position(a.id) == 42.0
    repo.record_play(a.id, 5.0)
    assert repo.last_position(a.id) == 5.0
    recent = repo.recent_plays(limit=10)
    assert len(recent) == 2
    assert recent[0][0].position == 5.0
