import pytest

from domain.models import Category, Video
from domain.repository import Repository


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


def test_fts_stays_in_sync_across_write_paths(repo):
    """Every videos-table write path must leave the FTS index searchable
    (invariant owned by _finish_videos_write; a forgotten rebuild makes
    search silently stale)."""
    repo.upsert_videos([_mk("alpha.mp4", r"D:\v\clip1.mp4")])
    assert repo.search("alpha"), "upsert must rebuild FTS"

    repo.upsert_videos([_mk("beta.mp4", r"D:\v\clip1.mp4")])  # same row, renamed
    assert repo.search("beta"), "upsert-update must rebuild FTS"
    assert repo.search("alpha") == [], "a stale FTS would still match the old token"

    repo.remove_by_filepaths([r"D:\v\clip1.mp4"])
    assert repo.search("beta") == [], "remove_by_filepaths must rebuild FTS"

    repo.upsert_videos([_mk("gamma.mp4", r"D:\root\clip2.mp4")])
    repo.remove_videos_under(r"D:\root")
    assert repo.search("gamma") == [], "remove_videos_under must rebuild FTS"


def test_upsert_updates_existing(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4")])
    repo.upsert_videos([_mk("a-renamed.mp4", r"D:\v\a.mp4", file_size=2048)])
    v = repo.get_by_path(r"D:\v\a.mp4")
    assert v.filename == "a-renamed.mp4"
    assert v.file_size == 2048
    assert repo.count() == 1


def test_remove_by_paths(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4"), _mk("b.mp4", r"D:\v\b.mp4")])
    a_id = repo.get_by_path(r"D:\v\a.mp4").id
    assert repo.remove_by_filepaths([r"D:\v\a.mp4"]) == [a_id]
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


def test_favorite_lists_crud(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4")])
    a = repo.get_by_path(r"D:\v\a.mp4")
    lst = repo.create_favorite_list("收藏夹_动作")
    assert lst.id > 0
    with pytest.raises(ValueError):
        repo.create_favorite_list("收藏夹_动作")
    repo.rename_favorite_list(lst.id, "收藏夹_动作片")
    assert repo.get_favorite_lists()[0].name == "收藏夹_动作片"

    assert not repo.is_favorite(a.id, lst.id)
    repo.add_favorite(a.id, lst.id)
    assert repo.is_favorite(a.id, lst.id)
    assert [v.id for v in repo.get_favorites(lst.id)] == [a.id]
    assert repo.count_favorites(lst.id) == 1
    assert [l.id for l in repo.lists_of_video(a.id)] == [lst.id]

    repo.remove_favorite(a.id, lst.id)
    assert not repo.is_favorite(a.id, lst.id)
    assert repo.get_favorites(lst.id) == []

    repo.add_favorite(a.id, lst.id)
    repo.delete_favorite_list(lst.id)
    assert repo.get_favorite_lists() == []
    assert repo.lists_of_video(a.id) == []


def test_favorite_lists_isolated(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4")])
    a = repo.get_by_path(r"D:\v\a.mp4")
    l1 = repo.create_favorite_list("收藏夹_一")
    l2 = repo.create_favorite_list("收藏夹_二")
    repo.add_favorite(a.id, l1.id)
    assert repo.is_favorite(a.id, l1.id)
    assert not repo.is_favorite(a.id, l2.id)
    assert repo.count_favorites(l2.id) == 0


def test_favorite_list_migration(tmp_path):
    """Legacy single favorites table must migrate into 收藏夹_默认."""
    import sqlite3

    db = tmp_path / "old_fav.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY, filename TEXT NOT NULL,
            filepath TEXT UNIQUE NOT NULL, file_size INTEGER DEFAULT 0,
            duration REAL, resolution TEXT, codec TEXT, thumb_path TEXT,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE favorites (
            video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    """)
    conn.execute(
        "INSERT INTO videos (id, filename, filepath) VALUES (7, 'a.mp4', 'D:/v/a.mp4')"
    )
    conn.execute("INSERT INTO favorites (video_id, added_at) VALUES (7, '2026-01-01')")
    conn.commit()
    conn.close()

    repo = Repository(db)
    try:
        lists = repo.get_favorite_lists()
        assert [l.name for l in lists] == ["收藏夹_默认"]
        assert repo.count_favorites(lists[0].id) == 1
        assert not repo._has_table("favorites")
    finally:
        repo.close()


def test_play_history_and_resume(repo):
    repo.upsert_videos([
        _mk("a.mp4", r"D:\v\a.mp4", duration=100.0),
        _mk("b.mp4", r"D:\v\b.mp4", duration=100.0),
    ])
    a = repo.get_by_path(r"D:\v\a.mp4")
    b = repo.get_by_path(r"D:\v\b.mp4")
    repo.record_play(a.id, 42.0)
    assert repo.last_position(a.id) == 42.0
    repo.record_play(a.id, 5.0)
    assert repo.last_position(a.id) == 5.0
    recent = repo.recent_plays(limit=10)
    assert len(recent) == 1, "replaying must not duplicate the entry"
    assert recent[0][0].position == 5.0
    assert recent[0][1].id == a.id
    repo.record_play(b.id, 10.0)
    repo.record_play(a.id, 90.0)
    recent = repo.recent_plays(limit=10)
    assert [r[1].id for r in recent] == [a.id, b.id], "replay must bump the video to the top"


def test_play_history_migration_dedupes(tmp_path):
    """Legacy play_history (one row per play event) must collapse to one row
    per video, keeping the latest play."""
    import sqlite3

    db = tmp_path / "old_history.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY, filename TEXT NOT NULL,
            filepath TEXT UNIQUE NOT NULL, file_size INTEGER DEFAULT 0,
            duration REAL, resolution TEXT, codec TEXT, thumb_path TEXT,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE play_history (
            id INTEGER PRIMARY KEY,
            video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            position REAL DEFAULT 0);
    """)
    conn.execute("INSERT INTO videos (id, filename, filepath) VALUES (1, 'a.mp4', 'D:/v/a.mp4')")
    conn.execute("INSERT INTO videos (id, filename, filepath) VALUES (2, 'b.mp4', 'D:/v/b.mp4')")
    conn.executemany(
        "INSERT INTO play_history (id, video_id, played_at, position) VALUES (?, ?, ?, ?)",
        [
            (1, 1, "2026-01-01 10:00:00", 20.0),
            (2, 2, "2026-01-01 10:00:10", 5.0),
            (3, 1, "2026-01-01 10:00:20", 70.0),
        ],
    )
    conn.commit()
    conn.close()

    repo = Repository(db)
    try:
        assert repo.last_position(1) == 70.0, "keep the latest play's resume point"
        recent = repo.recent_plays(limit=10)
        assert [r[1].id for r in recent] == [1, 2], "one entry per video, latest play first"
        repo.record_play(2, 99.0)
        assert [r[1].id for r in repo.recent_plays(limit=10)] == [2, 1]
        repo.record_play(1, 1.0)
        assert [r[1].id for r in repo.recent_plays(limit=10)] == [1, 2]
    finally:
        repo.close()


def test_clear_play_position_keeps_recent_entry(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4", duration=100.0)])
    a = repo.get_by_path(r"D:\v\a.mp4")
    repo.record_play(a.id, 42.0)
    repo.clear_play_position(a.id)
    assert repo.last_position(a.id) == 0.0, "resume point must be forgotten"
    assert len(repo.recent_plays(limit=10)) == 1, "recent entry must stay"


def test_stats_aggregates(repo):
    repo.upsert_videos([
        _mk("a.mp4", r"D:\x\a.mp4", duration=60.0, file_size=1000),
        _mk("b.mp4", r"D:\x\sub\b.mp4", duration=30.0, file_size=2000),
        _mk("c.mp4", r"D:\y\c.mp4", duration=10.0, file_size=4000),
    ])
    a = repo.get_by_path(r"D:\x\a.mp4")
    cat = repo.add_category("动作", root=r"D:\x")
    repo.assign_category(a.id, cat.id)
    repo.register_scan(r"D:\x")
    repo.register_scan(r"D:\y")

    s = repo.stats()
    assert s["count"] == 3
    assert s["duration"] == 100.0
    assert s["size"] == 7000
    assert dict(s["roots"]) == {r"D:\x": 2, r"D:\y": 1}
    assert s["categories"] == [("动作", 1)]


def test_upsert_persists_mtime(repo):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4", file_mtime=1234.5)])
    assert repo.get_by_path(r"D:\v\a.mp4").file_mtime == 1234.5
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4", file_mtime=9999.0)])
    assert repo.get_by_path(r"D:\v\a.mp4").file_mtime == 9999.0


def test_existing_under_scopes_by_root(repo):
    repo.upsert_videos([
        _mk("a.mp4", r"D:\x\a.mp4", file_size=1, file_mtime=1.0),
        _mk("b.mp4", r"D:\x\sub\b.mp4", file_size=2, file_mtime=2.0),
        _mk("c.mp4", r"D:\y\c.mp4", file_size=3, file_mtime=3.0),
    ])
    under = repo.existing_under(r"D:\x")
    assert set(under) == {r"D:\x\a.mp4", r"D:\x\sub\b.mp4"}
    assert under[r"D:\x\a.mp4"] == (1, 1.0)


def test_videos_in_root(repo):
    repo.upsert_videos([
        _mk("a.mp4", r"D:\x\a.mp4"),
        _mk("b.mp4", r"D:\x\sub\b.mp4"),
        _mk("c.mp4", r"D:\y\c.mp4"),
    ])
    assert {v.filename for v in repo.videos_in_root(r"D:\x")} == {"a.mp4", "b.mp4"}
    assert {v.filename for v in repo.videos_in_root(r"D:\y")} == {"c.mp4"}
    assert len(repo.videos_in_root(None)) == 3


def test_prefix_queries_exclude_sibling_dirs(repo):
    r"""Range predicates must match exactly the root's subtree: D:\v must not
    leak D:\vx\..., and D:\x\sub must not leak D:\x\sub2\..."""
    repo.upsert_videos([
        _mk("a.mp4", r"D:\v\a.mp4"),
        _mk("sibling.mp4", r"D:\vx\sibling.mp4"),
        _mk("sub.mp4", r"D:\x\sub\sub.mp4"),
        _mk("sub2.mp4", r"D:\x\sub2\sub2.mp4"),
    ])
    assert {v.filename for v in repo.videos_in_root(r"D:\v")} == {"a.mp4"}
    assert {v.filename for v in repo.videos_in_root(r"D:\x\sub")} == {"sub.mp4"}
    assert set(repo.existing_under(r"D:\v")) == {r"D:\v\a.mp4"}
    assert repo.count_in_root(r"D:\v") == 1
    removed = repo.remove_videos_under(r"D:\v")
    assert len(removed) == 1
    assert repo.get_by_path(r"D:\vx\sibling.mp4") is not None


def test_prefix_bounds_helper():
    from domain.repository import _prefix_bounds

    lo, hi = _prefix_bounds(r"D:\v" + "\\")
    assert lo == r"D:\v" + "\\"
    assert lo < r"D:\v\a.mp4" < hi
    assert not (lo <= r"D:\vx\sibling.mp4" < hi), "sibling must fall outside"
    lo, hi = _prefix_bounds("D:\\")
    assert lo < "D:\\a.mp4" < hi


def test_scan_roots_register_and_list(repo):
    repo.register_scan(r"D:\a")
    repo.register_scan(r"D:\b")
    repo.register_scan(r"D:\a")
    roots = repo.get_scan_roots()
    assert roots[0] == r"D:\a"
    assert set(roots) == {r"D:\a", r"D:\b"}


def test_remove_scan_root_keeps_videos(repo):
    repo.register_scan(r"D:\a")
    repo.upsert_videos([_mk("a.mp4", r"D:\a\a.mp4")])
    repo.remove_scan_root(r"D:\a")
    assert repo.get_scan_roots() == []
    assert repo.get_by_path(r"D:\a\a.mp4") is not None


def test_register_scan_child_skipped_when_parent_exists(repo):
    repo.register_scan(r"D:\a")
    repo.register_scan(r"D:\a\b")
    assert repo.get_scan_roots() == [r"D:\a"]


def test_register_scan_parent_removes_children(repo):
    repo.register_scan(r"D:\a\b")
    repo.register_scan(r"D:\a\b\c")
    repo.register_scan(r"D:\a")
    assert repo.get_scan_roots() == [r"D:\a"]


def test_remove_scan_root_cascades_children(repo):
    repo.register_scan(r"D:\a")
    repo.register_scan(r"D:\a\b")
    repo.register_scan(r"D:\b")
    repo.remove_scan_root(r"D:\a")
    assert repo.get_scan_roots() == [r"D:\b"]

    repo.register_scan(r"D:\a\b")
    repo.remove_scan_root(r"D:\a\b")
    assert repo.get_scan_roots() == [r"D:\b"]


def test_remove_videos_under(repo):
    repo.upsert_videos([
        _mk("a.mp4", r"D:\a\a.mp4"),
        _mk("b.mp4", r"D:\a\sub\b.mp4"),
        _mk("c.mp4", r"D:\b\c.mp4"),
    ])
    a = repo.get_by_path(r"D:\a\a.mp4")
    b = repo.get_by_path(r"D:\a\sub\b.mp4")
    lst = repo.create_favorite_list("收藏夹_默认")
    repo.add_favorite(a.id, lst.id)
    deleted = repo.remove_videos_under(r"D:\a")
    assert sorted(deleted) == sorted([a.id, b.id])
    assert repo.get_by_path(r"D:\a\a.mp4") is None
    assert repo.get_by_path(r"D:\b\c.mp4") is not None
    assert repo.count_favorites(lst.id) == 0, "favorite links must cascade"


def test_remove_videos_under_removes_category_tree(repo):
    parent = repo.add_category("甲", root=r"D:\a")
    repo.add_category("子", parent_id=parent.id, root=r"D:\a")
    repo.add_category("乙", root=r"D:\a\b")
    repo.add_category("丙", root=r"D:\b")
    repo.add_category("未绑定")

    repo.upsert_videos([_mk("a.mp4", r"D:\a\a.mp4")])
    repo.remove_videos_under(r"D:\a")

    names = [c.name for c in repo.get_categories(None)]
    assert names == ["丙", "未绑定"], "root's tree and sub-root categories must go"
    stats_cats = [n for n, _c in repo.stats()["categories"]]
    assert stats_cats == ["丙", "未绑定"], "stats must not show orphan categories"


def test_category_root_scoping(repo):
    repo.add_category("甲", root=r"D:\a")
    repo.add_category("乙", root=r"D:\b")
    repo.add_category("丙")
    assert [c.name for c in repo.get_categories(r"D:\a")] == ["甲"]
    assert [c.name for c in repo.get_categories(r"D:\b")] == ["乙"]
    assert len(repo.get_categories()) == 3
    roots = {c.name: c.root for c in repo.get_categories(None)}
    assert roots == {"甲": r"D:\a", "乙": r"D:\b", "丙": ""}, "Category must carry its root"


def test_adopt_legacy_categories(repo):
    repo.add_category("旧")
    assert repo.adopt_legacy_categories(r"D:\a") == 1
    assert [c.name for c in repo.get_categories(r"D:\a")] == ["旧"]
    assert repo.adopt_legacy_categories(r"D:\b") == 0


def test_migration_adds_columns(tmp_path):
    """A DB created before multi-root support must be upgraded in place."""
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY, filename TEXT NOT NULL,
            filepath TEXT UNIQUE NOT NULL, file_size INTEGER DEFAULT 0,
            duration REAL, resolution TEXT, codec TEXT, thumb_path TEXT,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    """)
    conn.execute("INSERT INTO categories (name) VALUES ('旧分类')")
    conn.commit()
    conn.close()

    repo = Repository(db)
    try:
        vcols = {r["name"] for r in repo._conn.execute("PRAGMA table_info(videos)")}
        assert "file_mtime" in vcols
        ccols = {r["name"] for r in repo._conn.execute("PRAGMA table_info(categories)")}
        assert "root" in ccols
        assert len(repo.get_categories()) == 1
    finally:
        repo.close()


def test_backup_to_creates_usable_snapshot(repo, tmp_path):
    repo.upsert_videos([_mk("a.mp4", r"D:\v\a.mp4", duration=5.0)])
    dest = tmp_path / "snapshot.db"
    repo.backup_to(dest)

    import sqlite3

    conn = sqlite3.connect(str(dest))
    try:
        rows = conn.execute("SELECT filepath, duration FROM videos").fetchall()
        assert rows == [(r"D:\v\a.mp4", 5.0)]
    finally:
        conn.close()


def test_last_positions_chunked_across_boundary(repo):
    """A view bigger than one chunk must still resolve all resume positions."""
    videos = [_mk(f"v{i}.mp4", rf"D:\v\v{i}.mp4") for i in range(505)]
    repo.upsert_videos(videos)
    ids = [repo.get_by_path(rf"D:\v\v{i}.mp4").id for i in range(505)]
    repo.record_play(ids[0], 12.0)
    repo.record_play(ids[503], 34.0)

    positions = repo.last_positions(ids)
    assert positions[ids[0]] == 12.0
    assert positions[ids[503]] == 34.0
    assert len(positions) == 2


def test_find_duplicates_groups_same_size_and_duration(repo):
    repo.upsert_videos([
        _mk("a.mp4", r"D:\v\a.mp4", file_size=1000, duration=60.0),
        _mk("copy_a.mp4", r"D:\v\copy_a.mp4", file_size=1000, duration=59.8),
        _mk("b.mp4", r"D:\v\b.mp4", file_size=1000, duration=120.0),
        _mk("c.mp4", r"D:\v\c.mp4", file_size=2000, duration=60.0),
        _mk("d.mp4", r"D:\v\d.mp4", file_size=1000, duration=70.0),
        _mk("e.mp4", r"D:\v\e.mp4", file_size=0, duration=60.0),
    ])

    dups = repo.find_duplicates()
    assert len(dups) == 1
    group = sorted(v.filename for v in dups[0])
    assert group == ["a.mp4", "copy_a.mp4"], (
        "same size + durations within 2s group; different size/duration stay apart"
    )


def test_find_duplicates_respects_tolerance(repo):
    repo.upsert_videos([
        _mk("a.mp4", r"D:\v\a.mp4", file_size=1000, duration=60.0),
        _mk("far.mp4", r"D:\v\far.mp4", file_size=1000, duration=70.0),
    ])
    assert repo.find_duplicates(tolerance=2.0) == []
    assert len(repo.find_duplicates(tolerance=20.0)) == 1
