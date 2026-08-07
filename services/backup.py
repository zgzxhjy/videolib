"""Database snapshots: one per day at startup, one before every destructive
operation, keeping the newest BACKUP_KEEP files. `restore_backup` rewinds the
live DB to a chosen snapshot (after snapshotting the pre-restore state)."""

import re
import shutil
from datetime import datetime
from pathlib import Path

import config
from domain.repository import Repository

_PATTERN = re.compile(r"^videolib-(\d{8})-(\d{6})\.db$")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_db(repo: Repository, force: bool = False) -> Path | None:
    """Write a snapshot of the DB. On a plain startup call (`force=False`) a
    backup is only taken once per day; destructive operations pass
    force=True. Returns the backup path, or None when skipped."""
    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    if not force:
        for f in config.BACKUPS_DIR.glob("videolib-*.db"):
            m = _PATTERN.match(f.name)
            if m and m.group(1) == today:
                return None
    dest = config.BACKUPS_DIR / f"videolib-{_stamp()}.db"
    repo.backup_to(dest)
    _rotate(config.BACKUPS_DIR)
    return dest


def list_backups(dir: str | Path | None = None) -> list[Path]:
    """Snapshot files, newest first (name embeds a sortable timestamp)."""
    dir = Path(dir or config.BACKUPS_DIR)
    return sorted(
        (f for f in dir.glob("videolib-*.db") if _PATTERN.match(f.name)),
        key=lambda f: f.name,
        reverse=True,
    )


def restore_backup(repo: Repository, backup_path: str | Path) -> Path:
    """Rewind the live DB to a snapshot.

    1. snapshot the pre-restore state (so the restore stays reversible);
    2. drop WAL side-cars so SQLite cannot replay stale frames over the copy;
    3. wipe thumbnails — ids get reused by older libs, so a kept {id}.jpg
       could otherwise show another video's frame (the ids rule);
    4. close the repo and copy the backup over the live DB.

    Callers must relaunch the app afterwards (the repo is closed here) and
    stop scans/watcher/cleanup threads first. Returns the live DB path."""
    backup_path = Path(backup_path)
    backup_db(repo, force=True)
    db = Path(config.DB_PATH)
    for side in (db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        try:
            if side.exists():
                side.unlink()
        except OSError:
            pass
    for f in config.THUMBS_DIR.glob("*.jpg"):
        try:
            f.unlink()
        except OSError:
            pass
    repo.close()
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, db)
    return db


def _rotate(dir: Path, keep: int | None = None) -> None:
    """Prune old snapshots, keeping the newest `keep` (settings
    'backup_keep' when not passed; the config default is 5)."""
    if keep is None:
        keep = int(config.load_settings().get("backup_keep", config.BACKUP_KEEP))
    files = sorted(
        (f for f in dir.glob("videolib-*.db") if _PATTERN.match(f.name)),
        key=lambda f: f.name,
    )
    for f in files[:-keep]:
        try:
            f.unlink()
        except OSError:
            pass
