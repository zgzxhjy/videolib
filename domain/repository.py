import sqlite3
import threading
from pathlib import Path

from domain.models import Category, PlayRecord, Video

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    filepath TEXT UNIQUE NOT NULL,
    file_size INTEGER DEFAULT 0,
    duration REAL,
    resolution TEXT,
    codec TEXT,
    thumb_path TEXT,
    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
    filename, filepath, content='videos', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS videos_ai AFTER INSERT ON videos BEGIN
    INSERT INTO videos_fts(rowid, filename, filepath) VALUES (new.id, new.filename, new.filepath);
END;
CREATE TRIGGER IF NOT EXISTS videos_ad AFTER DELETE ON videos BEGIN
    INSERT INTO videos_fts(videos_fts, rowid, filename, filepath) VALUES ('delete', old.id, old.filename, old.filepath);
END;
CREATE TRIGGER IF NOT EXISTS videos_au AFTER UPDATE OF filename, filepath ON videos BEGIN
    INSERT INTO videos_fts(videos_fts, rowid, filename, filepath) VALUES ('delete', old.id, old.filename, old.filepath);
    INSERT INTO videos_fts(rowid, filename, filepath) VALUES (new.id, new.filename, new.filepath);
END;

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS video_categories (
    video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (video_id, category_id)
);

CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY,
    video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    position REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS favorites (
    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _row_to_video(row: sqlite3.Row) -> Video:
    return Video(
        id=row["id"],
        filename=row["filename"],
        filepath=row["filepath"],
        file_size=row["file_size"],
        duration=row["duration"],
        resolution=row["resolution"],
        codec=row["codec"],
        thumb_path=row["thumb_path"],
        scanned_at=row["scanned_at"],
    )


class Repository:
    """Single point of access to the SQLite database. Thread-safe."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- videos ----------

    def upsert_videos(self, videos: list[Video]) -> int:
        """Insert or update by filepath. Returns number of rows changed."""
        if not videos:
            return 0
        with self._lock:
            cur = self._conn.executemany(
                """INSERT INTO videos (filename, filepath, file_size, duration, resolution, codec)
                   VALUES (:filename, :filepath, :file_size, :duration, :resolution, :codec)
                   ON CONFLICT(filepath) DO UPDATE SET
                       filename=excluded.filename,
                       file_size=excluded.file_size,
                       duration=excluded.duration,
                       resolution=excluded.resolution,
                       codec=excluded.codec,
                       scanned_at=CURRENT_TIMESTAMP""",
                [
                    {
                        "filename": v.filename,
                        "filepath": v.filepath,
                        "file_size": v.file_size,
                        "duration": v.duration,
                        "resolution": v.resolution,
                        "codec": v.codec,
                    }
                    for v in videos
                ],
            )
            self._conn.commit()
            return cur.rowcount

    def all_filepaths(self) -> set[str]:
        with self._lock:
            return {r["filepath"] for r in self._conn.execute("SELECT filepath FROM videos")}

    def remove_by_filepaths(self, paths: list[str]) -> int:
        if not paths:
            return 0
        with self._lock:
            cur = self._conn.executemany("DELETE FROM videos WHERE filepath = ?", [(p,) for p in paths])
            self._conn.commit()
            return cur.rowcount

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

    def all_videos(self, limit: int = 500) -> list[Video]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM videos ORDER BY filename LIMIT ?", (limit,)
            ).fetchall()
            return [_row_to_video(r) for r in rows]

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

    def add_category(self, name: str, parent_id: int | None = None) -> Category:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO categories (name, parent_id) VALUES (?, ?)",
                (name, parent_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return Category(id=row["id"], name=row["name"], parent_id=row["parent_id"])

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

    def get_categories(self) -> list[Category]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
            return [
                Category(id=r["id"], name=r["name"], parent_id=r["parent_id"])
                for r in rows
            ]

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
            return [Category(id=r["id"], name=r["name"], parent_id=r["parent_id"]) for r in rows]

    # ---------- favorites ----------

    def add_favorite(self, video_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO favorites (video_id) VALUES (?)", (video_id,)
            )
            self._conn.commit()

    def remove_favorite(self, video_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM favorites WHERE video_id = ?", (video_id,))
            self._conn.commit()

    def is_favorite(self, video_id: int) -> bool:
        with self._lock:
            return (
                self._conn.execute(
                    "SELECT 1 FROM favorites WHERE video_id = ?", (video_id,)
                ).fetchone()
                is not None
            )

    def get_favorites(self) -> list[Video]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT v.* FROM videos v JOIN favorites f ON f.video_id = v.id
                   ORDER BY f.added_at DESC"""
            ).fetchall()
            return [_row_to_video(r) for r in rows]

    # ---------- play history ----------

    def record_play(self, video_id: int, position: float = 0.0) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO play_history (video_id, position) VALUES (?, ?)",
                (video_id, position),
            )
            self._conn.commit()

    def last_position(self, video_id: int) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT position FROM play_history WHERE video_id = ? ORDER BY id DESC LIMIT 1",
                (video_id,),
            ).fetchone()
            return row["position"] if row else 0.0

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
