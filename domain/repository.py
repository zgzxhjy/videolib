import os
import sqlite3
import threading
import time
from pathlib import Path

from domain.models import Category, FavoriteList, PlayRecord, Video

DEFAULT_FAVORITE_LIST = "收藏夹_默认"

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    filepath TEXT UNIQUE NOT NULL,
    file_size INTEGER DEFAULT 0,
    file_mtime REAL,
    duration REAL,
    resolution TEXT,
    codec TEXT,
    probe_retry_at REAL,
    thumb_path TEXT,
    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
    filename, filepath, content='videos', content_rowid='id'
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    root TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS video_categories (
    video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (video_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_video_categories_category
    ON video_categories(category_id, video_id);

CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY,
    video_id INTEGER UNIQUE REFERENCES videos(id) ON DELETE CASCADE,
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    position REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS favorite_lists (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS favorite_items (
    list_id INTEGER REFERENCES favorite_lists(id) ON DELETE CASCADE,
    video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (list_id, video_id)
);

CREATE TABLE IF NOT EXISTS scan_roots (
    id INTEGER PRIMARY KEY,
    root TEXT UNIQUE NOT NULL,
    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _row_to_video(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"],
        filename=row["filename"],
        filepath=row["filepath"],
        file_size=row["file_size"],
        file_mtime=row["file_mtime"],
        duration=row["duration"],
        resolution=row["resolution"],
        codec=row["codec"],
        thumb_path=row["thumb_path"],
        scanned_at=row["scanned_at"],
    )


CHUNK_SIZE = 500


def _prefix_bounds(prefix: str) -> tuple[str, str]:
    """BINARY range that covers exactly the paths starting with `prefix`.

    `substr(filepath, 1, length(?)) = ?` scans the whole table; a range
    predicate on the unique `filepath` column can use its index instead.
    The upper bound is the last character bumped by one, so `D:\\v\\` yields
    [`D:\\v\\`, `D:\\v]`) — every child starts with the separator (0x5C < 0x5D)
    and sibling names like `D:\\vx\\...` are excluded.
    """
    lo = prefix
    hi = prefix[:-1] + chr(ord(prefix[-1]) + 1)
    return lo, hi


class Repository:
    """Single point of access to the SQLite database. Thread-safe."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._videos_dirty = False
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA cache_size=-20000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._heal_fts()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add columns to databases created before multi-root support."""
        video_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(videos)")}
        if "file_mtime" not in video_cols:
            self._conn.execute("ALTER TABLE videos ADD COLUMN file_mtime REAL")
        if "probe_retry_at" not in video_cols:
            self._conn.execute("ALTER TABLE videos ADD COLUMN probe_retry_at REAL")
        cat_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(categories)")}
        if "root" not in cat_cols:
            self._conn.execute("ALTER TABLE categories ADD COLUMN root TEXT NOT NULL DEFAULT ''")
        legacy_fts_triggers = {
            r["name"]
            for r in self._conn.execute(
                """SELECT name FROM sqlite_master WHERE type = 'trigger'
                   AND name IN ('videos_ai', 'videos_au', 'videos_ad')"""
            )
        }
        if legacy_fts_triggers:
            for t in legacy_fts_triggers:
                self._conn.execute(f"DROP TRIGGER IF EXISTS {t}")
            self._sync_fts()
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_video_categories_category
               ON video_categories(category_id, video_id)"""
        )
        if self._has_table("favorites"):
            n = self._conn.execute("SELECT COUNT(*) AS c FROM favorites").fetchone()["c"]
            empty_lists = (
                self._conn.execute("SELECT COUNT(*) AS c FROM favorite_lists").fetchone()["c"]
            )
            if n > 0 and empty_lists == 0:
                cur = self._conn.execute(
                    "INSERT INTO favorite_lists (name) VALUES (?)", (DEFAULT_FAVORITE_LIST,)
                )
                self._conn.execute(
                    """INSERT INTO favorite_items (list_id, video_id, added_at)
                       SELECT ?, video_id, added_at FROM favorites""",
                    (cur.lastrowid,),
                )
            self._conn.execute("DROP TABLE favorites")
        if not self._play_history_deduped():
            self._dedupe_play_history()

    def _heal_fts(self) -> None:
        """Rebuild the FTS index if it drifted from the videos table.

        A kill during a destructive write can leave the index stale while
        the rows are already gone, which would keep deleted videos visible
        in search forever. Both COUNTs are cheap; rebuild happens only on
        mismatch.
        """
        try:
            videos = self._conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()["c"]
            indexed = self._conn.execute(
                "SELECT COUNT(*) AS c FROM videos_fts"
            ).fetchone()["c"]
        except sqlite3.Error:
            return
        if videos != indexed:
            self._sync_fts()

    def _play_history_deduped(self) -> bool:
        rows = self._conn.execute("PRAGMA index_list('play_history')").fetchall()
        return any(r["unique"] and r["origin"] == "u" for r in rows)

    def _dedupe_play_history(self) -> None:
        """One row per video: keep the latest play (MAX id) for each video."""
        self._conn.executescript(
            """CREATE TABLE play_history_new (
                id INTEGER PRIMARY KEY,
                video_id INTEGER UNIQUE REFERENCES videos(id) ON DELETE CASCADE,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                position REAL DEFAULT 0
            );
            INSERT INTO play_history_new (id, video_id, played_at, position)
                SELECT id, video_id, played_at, position FROM play_history
                WHERE id IN (SELECT MAX(id) FROM play_history GROUP BY video_id);
            DROP TABLE play_history;
            ALTER TABLE play_history_new RENAME TO play_history;"""
        )

    def _has_table(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return row is not None

    def _sync_fts(self) -> None:
        """Rebuild the FTS index from the videos table.

        FTS is kept trigger-free so bulk scans do not pay one FTS write per
        row; every write path rebuilds once instead (cheap up to ~100k rows).
        Callers must already hold self._lock.
        """
        self._conn.execute("INSERT INTO videos_fts(videos_fts) VALUES ('rebuild')")

    def _write(self, fn):
        """Run fn under the lock and commit."""
        with self._lock:
            result = fn()
            self._conn.commit()
            return result

    def _write_videos(self, fn):
        """Like _write, but marks the FTS index dirty for _finish_videos_write.

        Rebuild happens once per batch in _finish_videos_write, never per
        chunk, so bulk writes do not pay N FTS rebuilds.
        """
        with self._lock:
            result = fn()
            self._conn.commit()
            self._videos_dirty = True
            return result

    def _finish_videos_write(self) -> None:
        """Rebuild the FTS index once, after a batch of videos-table writes.

        The invariant lives here: any write path that changes videos rows
        must end with this call so search never goes stale.
        """
        if not self._videos_dirty:
            return
        with self._lock:
            self._sync_fts()
            self._conn.commit()
            self._videos_dirty = False

    def backup_to(self, dest: str | Path) -> None:
        """Snapshot the database (WAL-aware) to `dest` via the backup API."""
        dest = str(dest)
        with self._lock:
            target = sqlite3.connect(dest)
            try:
                self._conn.backup(target)
            finally:
                target.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- videos ----------

    def upsert_videos(self, videos: list[Video]) -> int:
        """Insert or update by filepath. Returns number of rows changed.

        Rows are written in bounded chunks so the lock is never held for the
        whole batch; the UI thread keeps responsive during large scans.
        """
        if not videos:
            return 0
        total = 0
        for start in range(0, len(videos), CHUNK_SIZE):
            chunk = videos[start : start + CHUNK_SIZE]
            cur = self._write_videos(
                lambda: self._conn.executemany(
                    """INSERT INTO videos (filename, filepath, file_size, file_mtime, duration, resolution, codec)
                       VALUES (:filename, :filepath, :file_size, :file_mtime, :duration, :resolution, :codec)
                       ON CONFLICT(filepath) DO UPDATE SET
                           filename=excluded.filename,
                           file_size=excluded.file_size,
                           file_mtime=excluded.file_mtime,
                           duration=excluded.duration,
                           resolution=excluded.resolution,
                           codec=excluded.codec,
                           scanned_at=CURRENT_TIMESTAMP""",
                    [
                        {
                            "filename": v.filename,
                            "filepath": v.filepath,
                            "file_size": v.file_size,
                            "file_mtime": v.file_mtime,
                            "duration": v.duration,
                            "resolution": v.resolution,
                            "codec": v.codec,
                        }
                        for v in chunk
                    ],
                )
            )
            total += cur.rowcount
        self._finish_videos_write()
        return total

    def all_filepaths(self) -> set[str]:
        with self._lock:
            return {r["filepath"] for r in self._conn.execute("SELECT filepath FROM videos")}

    def existing_under(self, root: str) -> dict[str, tuple[int, float | None]]:
        """Known videos under root: filepath -> (file_size, file_mtime)."""
        prefix = os.path.normpath(root) + os.sep
        lo, hi = _prefix_bounds(prefix)
        with self._lock:
            rows = self._conn.execute(
                """SELECT filepath, file_size, file_mtime FROM videos
                   WHERE filepath >= ? AND filepath < ?""",
                (lo, hi),
            ).fetchall()
            return {r["filepath"]: (r["file_size"], r["file_mtime"]) for r in rows}

    def missing_metadata_under(self, root: str) -> set[str]:
        """Filepaths under root whose probe produced no metadata at all
        (duration/resolution/codec all NULL); scans re-probe them regardless
        of mtime. Rows with partial metadata were probed successfully once
        and must not be clobbered by a re-probe."""
        prefix = os.path.normpath(root) + os.sep
        lo, hi = _prefix_bounds(prefix)
        with self._lock:
            rows = self._conn.execute(
                """SELECT filepath FROM videos
                   WHERE filepath >= ? AND filepath < ?
                     AND duration IS NULL AND resolution IS NULL AND codec IS NULL""",
                (lo, hi),
            ).fetchall()
            return {r["filepath"] for r in rows}

    def missing_metadata_files(self, cutoff: float | None = None) -> list[Video]:
        """Videos whose probe produced no metadata at all (all three fields
        NULL), optionally only those not retried since `cutoff` (epoch
        seconds). Used by the startup repair pass; a probe failure stamps
        probe_retry_at so permanently broken files are retried at most once
        per cooldown. Partial rows (any field set) are left alone: they were
        probed successfully and a re-probe of a now-unreachable file would
        only wipe the good data."""
        where = "duration IS NULL AND resolution IS NULL AND codec IS NULL"
        params: tuple = ()
        if cutoff is not None:
            where += " AND (probe_retry_at IS NULL OR probe_retry_at < ?)"
            params = (cutoff,)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM videos WHERE {where} ORDER BY filepath", params
            ).fetchall()
            return [_row_to_video(r) for r in rows]

    def mark_probe_failed(self, paths: list[str]) -> None:
        """Stamp probe_retry_at=now for failed probe paths (retry cooldown).

        Only touches probe_retry_at, which is not part of the FTS index
        (filename/filepath are), so no FTS rebuild is needed here."""
        if not paths:
            return
        now = time.time()
        with self._lock:
            self._conn.executemany(
                "UPDATE videos SET probe_retry_at = ? WHERE filepath = ?",
                [(now, p) for p in paths],
            )
            self._conn.commit()

    def remove_by_filepaths(self, paths: list[str]) -> list[int]:
        """Delete videos by filepath, returning the deleted video ids."""
        if not paths:
            return []
        deleted: list[int] = []
        for start in range(0, len(paths), CHUNK_SIZE):
            chunk = paths[start : start + CHUNK_SIZE]
            deleted.extend(
                self._write_videos(
                    lambda: self._delete_chunk(chunk)
                )
            )
        self._finish_videos_write()
        return deleted

    def _delete_chunk(self, chunk: list[str]) -> list[int]:
        """Delete one chunk of filepaths, returning the deleted video ids."""
        ids = [
            r["id"]
            for r in self._conn.execute(
                f"SELECT id FROM videos WHERE filepath IN ({','.join('?' * len(chunk))})",
                chunk,
            )
        ]
        if ids:
            self._conn.executemany(
                "DELETE FROM videos WHERE filepath = ?", [(p,) for p in chunk]
            )
        return ids

    def get_video(self, video_id: int) -> Video | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM videos WHERE id = ?", (video_id,)
            ).fetchone()
            return _row_to_video(row) if row else None

    def get_by_path(self, filepath: str) -> Video | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM videos WHERE filepath = ?", (filepath,)
            ).fetchone()
            return _row_to_video(row) if row else None

    def set_thumb(self, video_id: int, thumb_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE videos SET thumb_path = ? WHERE id = ?", (thumb_path, video_id)
            )
            self._conn.commit()

    def set_duration(self, video_id: int, duration: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE videos SET duration = ? WHERE id = ?", (duration, video_id)
            )
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()["c"]

    def all_videos(self, limit: int | None = None) -> list[Video]:
        with self._lock:
            if limit is None:
                rows = self._conn.execute(
                    "SELECT * FROM videos ORDER BY filename"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM videos ORDER BY filename LIMIT ?", (limit,)
                ).fetchall()
            return [_row_to_video(r) for r in rows]

    def all_video_ids(self) -> set[int]:
        with self._lock:
            return {r["id"] for r in self._conn.execute("SELECT id FROM videos")}

    def find_duplicates(self, tolerance: float = 2.0) -> list[list[Video]]:
        """Group videos that are likely copies: same file size and a duration
        within `tolerance` seconds. Videos without size/duration are skipped."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM videos
                   WHERE file_size > 0 AND duration IS NOT NULL
                   ORDER BY file_size, duration"""
            ).fetchall()
        videos = [_row_to_video(r) for r in rows]
        groups: dict[int, list[Video]] = {}
        for v in videos:
            groups.setdefault(v.file_size, []).append(v)
        dups: list[list[Video]] = []
        for size_group in groups.values():
            if len(size_group) < 2:
                continue
            size_group.sort(key=lambda v: v.duration or 0.0)
            cluster: list[Video] = []
            for v in size_group:
                if cluster and (v.duration or 0.0) - (cluster[-1].duration or 0.0) > tolerance:
                    if len(cluster) > 1:
                        dups.append(cluster)
                    cluster = []
                cluster.append(v)
            if len(cluster) > 1:
                dups.append(cluster)
        return dups

    def videos_in_root(self, root: str | None, limit: int | None = None) -> list[Video]:
        """Videos under a scan root. root=None falls back to the whole library."""
        if root is None:
            return self.all_videos(limit)
        prefix = os.path.normpath(root) + os.sep
        lo, hi = _prefix_bounds(prefix)
        with self._lock:
            if limit is None:
                rows = self._conn.execute(
                    """SELECT * FROM videos
                       WHERE filepath >= ? AND filepath < ?
                       ORDER BY filename""",
                    (lo, hi),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM videos
                       WHERE filepath >= ? AND filepath < ?
                       ORDER BY filename LIMIT ?""",
                    (lo, hi, limit),
                ).fetchall()
            return [_row_to_video(r) for r in rows]

    # ---------- scan roots ----------

    def register_scan(self, root: str) -> None:
        """Register a scan root, keeping parent/child roots mutually exclusive.

        If an ancestor root is already registered the child is redundant (its
        files are covered by the ancestor's scan) and is skipped. If the new
        root is an ancestor of registered roots, those child records are
        dropped before registering it.
        """
        root = os.path.normpath(root)
        nc_root = os.path.normcase(root)
        with self._lock:
            existing = [r["root"] for r in self._conn.execute("SELECT root FROM scan_roots")]
            if any(nc_root.startswith(os.path.normcase(r) + os.sep) for r in existing):
                return
            children = [r for r in existing if os.path.normcase(r).startswith(nc_root + os.sep)]
            if children:
                self._conn.executemany(
                    "DELETE FROM scan_roots WHERE root = ?", [(c,) for c in children]
                )
            self._conn.execute(
                """INSERT INTO scan_roots (root, scanned_at)
                   VALUES (?, CURRENT_TIMESTAMP)
                   ON CONFLICT(root) DO UPDATE SET scanned_at = CURRENT_TIMESTAMP""",
                (root,),
            )
            self._conn.commit()

    def get_scan_roots(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT root FROM scan_roots ORDER BY scanned_at DESC"
            ).fetchall()
            return [r["root"] for r in rows]

    def remove_scan_root(self, root: str) -> None:
        """Forget a scan root and any sub-roots registered under it.

        Videos are kept. Case-insensitive on Windows (normcase).
        """
        root = os.path.normpath(root)
        nc_root = os.path.normcase(root)
        with self._lock:
            existing = self._conn.execute("SELECT root FROM scan_roots").fetchall()
            doomed = [
                r["root"]
                for r in existing
                if os.path.normcase(r["root"]) == nc_root
                or os.path.normcase(r["root"]).startswith(nc_root + os.sep)
            ]
            if doomed:
                self._conn.executemany(
                    "DELETE FROM scan_roots WHERE root = ?", [(r,) for r in doomed]
                )
            self._conn.commit()

    def remove_videos_under(self, root: str) -> list[int]:
        """Delete all videos under a scan root, returning the deleted video ids.

        Categories belonging to the root (or any sub-root) are removed too —
        they are scoped to a scan root, so a deleted root must not leave
        orphan categories behind; children cascade via parent_id.
        Favorites links cascade. Callers must also remove the thumbnails for
        the returned ids (files would be reused by new rows).
        """
        root = os.path.normpath(root)
        nc_root = os.path.normcase(root)
        prefix = root + os.sep
        ids = self._write_videos(
            lambda: self._remove_under(prefix)
        )
        self._write(lambda: self._remove_categories_under(nc_root))
        self._finish_videos_write()
        return ids

    def clear_all_videos(self) -> list[int]:
        """Delete every video row, returning the deleted video ids.

        Play history / favorites / category links cascade via foreign keys.
        Scan roots and the category tree are kept (a root's categories are
        scoped to it, but an empty library must stay re-scanable from its
        history). Callers must also remove the thumbnails for the returned
        ids.
        """
        ids = self._write_videos(lambda: self._clear_all_under())
        self._finish_videos_write()
        return ids

    def _clear_all_under(self) -> list[int]:
        ids = [
            r["id"] for r in self._conn.execute("SELECT id FROM videos").fetchall()
        ]
        if ids:
            self._conn.execute("DELETE FROM videos")
        return ids

    def _remove_categories_under(self, nc_root: str) -> None:
        rows = self._conn.execute("SELECT id, root FROM categories").fetchall()
        doomed = [
            r["id"]
            for r in rows
            if os.path.normcase(r["root"]) == nc_root
            or os.path.normcase(r["root"]).startswith(nc_root + os.sep)
        ]
        for cid in doomed:
            self._conn.execute("DELETE FROM categories WHERE id = ?", (cid,))

    def _remove_under(self, prefix: str) -> list[int]:
        lo, hi = _prefix_bounds(prefix)
        ids = [
            r["id"]
            for r in self._conn.execute(
                "SELECT id FROM videos WHERE filepath >= ? AND filepath < ?",
                (lo, hi),
            )
        ]
        if ids:
            self._conn.execute(
                "DELETE FROM videos WHERE filepath >= ? AND filepath < ?",
                (lo, hi),
            )
        return ids

    # ---------- search ----------

    def search(self, query: str, limit: int = 500) -> list[Video]:
        """FTS5 fast path with LIKE fallback for CJK substring matches."""
        q = query.strip()
        if not q:
            return []
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        fts_terms = " ".join(f'"{t}"' for t in q.split() if t)
        with self._lock:
            if fts_terms:
                rows = self._conn.execute(
                    """SELECT v.* FROM videos v
                       WHERE v.filename LIKE ? ESCAPE '\\' COLLATE NOCASE
                          OR v.filepath LIKE ? ESCAPE '\\' COLLATE NOCASE
                          OR v.id IN (SELECT rowid FROM videos_fts WHERE videos_fts MATCH ?)
                       ORDER BY v.filename
                       LIMIT ?""",
                    (like, like, fts_terms, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM videos
                       WHERE filename LIKE ? ESCAPE '\\' COLLATE NOCASE
                          OR filepath LIKE ? ESCAPE '\\' COLLATE NOCASE
                       ORDER BY filename
                       LIMIT ?""",
                    (like, like, limit),
                ).fetchall()
            return [_row_to_video(r) for r in rows]

    # ---------- categories ----------

    def add_category(
        self, name: str, parent_id: int | None = None, root: str = ""
    ) -> Category:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO categories (name, parent_id, root) VALUES (?, ?, ?)",
                (name, parent_id, root),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return Category(
                id=row["id"], name=row["name"], parent_id=row["parent_id"], root=row["root"]
            )

    def rename_category(self, category_id: int, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE categories SET name = ? WHERE id = ?", (name, category_id)
            )
            self._conn.commit()

    def move_category(self, category_id: int, new_parent_id: int | None) -> None:
        if category_id == new_parent_id:
            return
        with self._lock:
            if new_parent_id is not None:
                # reject moving a category into its own subtree
                desc = self._category_descendants(category_id)
                if new_parent_id in desc:
                    raise ValueError("cannot move category into its own subtree")
            self._conn.execute(
                "UPDATE categories SET parent_id = ? WHERE id = ?",
                (new_parent_id, category_id),
            )
            self._conn.commit()

    def _category_descendants(self, category_id: int) -> set[int]:
        return {
            r["id"]
            for r in self._conn.execute(
                """WITH RECURSIVE sub(id) AS (
                       SELECT id FROM categories WHERE parent_id = ?
                       UNION ALL
                       SELECT c.id FROM categories c JOIN sub ON c.parent_id = sub.id
                   ) SELECT id FROM sub""",
                (category_id,),
            )
        }

    def delete_category(self, category_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            self._conn.commit()

    def get_categories(self, root: str | None = None) -> list[Category]:
        """All categories, or only those belonging to a scan root."""
        with self._lock:
            if root is None:
                rows = self._conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM categories WHERE root = ? ORDER BY name", (root,)
                ).fetchall()
            return [
                Category(
                    id=r["id"], name=r["name"], parent_id=r["parent_id"], root=r["root"]
                )
                for r in rows
            ]

    def adopt_legacy_categories(self, root: str) -> int:
        """Bind pre-multi-root categories (root='') to the given root. Returns count."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE categories SET root = ? WHERE root = ''", (root,)
            )
            self._conn.commit()
            return cur.rowcount

    def assign_category(self, video_id: int, category_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO video_categories (video_id, category_id) VALUES (?, ?)",
                (video_id, category_id),
            )
            self._conn.commit()

    def unassign_category(self, video_id: int, category_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM video_categories WHERE video_id = ? AND category_id = ?",
                (video_id, category_id),
            )
            self._conn.commit()

    def assign_batch(self, video_ids: list[int], category_id: int) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO video_categories (video_id, category_id) VALUES (?, ?)",
                [(vid, category_id) for vid in video_ids],
            )
            self._conn.commit()

    def unassign_batch(self, video_ids: list[int], category_id: int) -> None:
        with self._lock:
            self._conn.executemany(
                "DELETE FROM video_categories WHERE video_id = ? AND category_id = ?",
                [(vid, category_id) for vid in video_ids],
            )
            self._conn.commit()

    def videos_in_category(self, category_id: int, include_descendants: bool = True) -> list[Video]:
        """All videos tagged with category (optionally including its subtree)."""
        if include_descendants:
            ids = {category_id} | self._category_descendants(category_id)
        else:
            ids = {category_id}
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT DISTINCT v.* FROM videos v
                    JOIN video_categories vc ON vc.video_id = v.id
                    WHERE vc.category_id IN ({placeholders})
                    ORDER BY v.filename""",
                list(ids),
            ).fetchall()
            return [_row_to_video(r) for r in rows]

    def categories_of_video(self, video_id: int) -> list[Category]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT c.* FROM categories c
                   JOIN video_categories vc ON vc.category_id = c.id
                   WHERE vc.video_id = ? ORDER BY c.name""",
                (video_id,),
            ).fetchall()
            return [
                Category(id=r["id"], name=r["name"], parent_id=r["parent_id"], root=r["root"])
                for r in rows
            ]

    # ---------- favorites ----------

    def create_favorite_list(self, name: str) -> FavoriteList:
        """Raise ValueError on duplicate name."""
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO favorite_lists (name) VALUES (?)", (name,)
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                raise ValueError(f"收藏夹「{name}」已存在") from None
            return self._favorite_list(cur.lastrowid)

    def rename_favorite_list(self, list_id: int, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE favorite_lists SET name = ? WHERE id = ?", (name, list_id)
            )
            self._conn.commit()

    def delete_favorite_list(self, list_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM favorite_lists WHERE id = ?", (list_id,))
            self._conn.commit()

    def get_favorite_lists(self) -> list[FavoriteList]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM favorite_lists ORDER BY created_at, id"
            ).fetchall()
            return [self._favorite_list(r["id"]) for r in rows]

    def _favorite_list(self, list_id: int) -> FavoriteList:
        row = self._conn.execute(
            "SELECT * FROM favorite_lists WHERE id = ?", (list_id,)
        ).fetchone()
        return FavoriteList(id=row["id"], name=row["name"], created_at=row["created_at"])

    def count_favorites(self, list_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM favorite_items WHERE list_id = ?", (list_id,)
            ).fetchone()
            return row["c"]

    def add_favorite(self, video_id: int, list_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO favorite_items (list_id, video_id) VALUES (?, ?)",
                (list_id, video_id),
            )
            self._conn.commit()

    def remove_favorite(self, video_id: int, list_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM favorite_items WHERE list_id = ? AND video_id = ?",
                (list_id, video_id),
            )
            self._conn.commit()

    def is_favorite(self, video_id: int, list_id: int) -> bool:
        with self._lock:
            return (
                self._conn.execute(
                    "SELECT 1 FROM favorite_items WHERE list_id = ? AND video_id = ?",
                    (list_id, video_id),
                ).fetchone()
                is not None
            )

    def lists_of_video(self, video_id: int) -> list[FavoriteList]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT l.* FROM favorite_lists l
                   JOIN favorite_items fi ON fi.list_id = l.id
                   WHERE fi.video_id = ? ORDER BY l.created_at, l.id""",
                (video_id,),
            ).fetchall()
            return [FavoriteList(id=r["id"], name=r["name"], created_at=r["created_at"]) for r in rows]

    def get_favorites(self, list_id: int) -> list[Video]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT v.* FROM videos v
                   JOIN favorite_items fi ON fi.video_id = v.id
                   WHERE fi.list_id = ? ORDER BY fi.added_at DESC""",
                (list_id,),
            ).fetchall()
            return [_row_to_video(r) for r in rows]

    # ---------- play history ----------

    def record_play(self, video_id: int, position: float = 0.0) -> None:
        """Replaying a video updates its row (played_at + position) instead of
        appending a duplicate, so the recent list stays one entry per video.
        The rowid is bumped so same-second ties still order by latest play."""
        with self._lock:
            cur = self._conn.execute(
                """UPDATE play_history
                   SET id = (SELECT COALESCE(MAX(id), 0) + 1 FROM play_history),
                       played_at = CURRENT_TIMESTAMP, position = ?
                   WHERE video_id = ?""",
                (position, video_id),
            )
            if cur.rowcount == 0:
                self._conn.execute(
                    "INSERT INTO play_history (video_id, position) VALUES (?, ?)",
                    (video_id, position),
                )
            self._conn.commit()

    def stats(self) -> dict:
        """Library overview: totals, per-root counts, per-category counts."""
        with self._lock:
            totals = self._conn.execute(
                """SELECT COUNT(*) AS count,
                          COALESCE(SUM(duration), 0) AS duration,
                          COALESCE(SUM(file_size), 0) AS size
                   FROM videos"""
            ).fetchone()
            cats = self._conn.execute(
                """SELECT c.name, COUNT(vc.video_id) AS count
                   FROM categories c
                   LEFT JOIN video_categories vc ON vc.category_id = c.id
                   GROUP BY c.id ORDER BY c.name"""
            ).fetchall()
            roots = self.get_scan_roots()
            per_root = {}
            if roots:
                per = " UNION ALL ".join(
                    "SELECT ? AS root, COUNT(*) AS c FROM videos "
                    "WHERE filepath >= ? AND filepath < ?"
                    for _ in roots
                )
                args: list[str] = []
                for r in roots:
                    lo, hi = _prefix_bounds(os.path.normpath(r) + os.sep)
                    args += [r, lo, hi]
                per_root = {
                    row["root"]: row["c"]
                    for row in self._conn.execute(per, args).fetchall()
                }
        return {
            "count": totals["count"],
            "duration": totals["duration"],
            "size": totals["size"],
            "roots": [(r, per_root.get(r, 0)) for r in roots],
            "categories": [(r["name"], r["count"]) for r in cats],
        }

    def count_in_root(self, root: str) -> int:
        prefix = os.path.normpath(root) + os.sep
        lo, hi = _prefix_bounds(prefix)
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS c FROM videos
                   WHERE filepath >= ? AND filepath < ?""",
                (lo, hi),
            ).fetchone()
            return row["c"]

    def clear_play_position(self, video_id: int) -> None:
        """Forget the resume point; the recent-play entry stays."""
        with self._lock:
            self._conn.execute(
                "UPDATE play_history SET position = 0 WHERE video_id = ?",
                (video_id,),
            )
            self._conn.commit()

    def clear_play_history(self) -> None:
        """Wipe all play history; also drops every resume position."""
        with self._lock:
            self._conn.execute("DELETE FROM play_history")
            self._conn.commit()

    def last_position(self, video_id: int) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT position FROM play_history WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            return row["position"] if row else 0.0

    def last_positions(self, video_ids: list[int]) -> dict[int, float]:
        """Batch resume positions for many videos (id IN ..., chunked so huge
        libraries never hit SQLite's variable limit)."""
        if not video_ids:
            return {}
        result: dict[int, float] = {}
        with self._lock:
            for i in range(0, len(video_ids), CHUNK_SIZE):
                chunk = video_ids[i : i + CHUNK_SIZE]
                rows = self._conn.execute(
                    f"""SELECT video_id, position
                        FROM play_history
                        WHERE video_id IN ({",".join("?" * len(chunk))})""",
                    chunk,
                ).fetchall()
                result.update({r["video_id"]: r["position"] for r in rows})
            return result

    def recent_plays(self, limit: int = 50) -> list[tuple[PlayRecord, Video]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT h.*, v.id AS v_id, v.filename, v.filepath, v.file_size,
                          v.duration, v.resolution, v.codec, v.thumb_path, v.scanned_at
                   FROM play_history h JOIN videos v ON v.id = h.video_id
                   ORDER BY h.played_at DESC, h.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                rec = PlayRecord(
                    id=r["id"],
                    video_id=r["video_id"],
                    played_at=r["played_at"],
                    position=r["position"],
                )
                video = Video(
                    id=r["v_id"],
                    filename=r["filename"],
                    filepath=r["filepath"],
                    file_size=r["file_size"],
                    duration=r["duration"],
                    resolution=r["resolution"],
                    codec=r["codec"],
                    thumb_path=r["thumb_path"],
                    scanned_at=r["scanned_at"],
                )
                result.append((rec, video))
            return result
