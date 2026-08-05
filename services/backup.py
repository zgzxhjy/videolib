"""Database snapshots: one per day at startup, one before every destructive
operation, keeping the newest BACKUP_KEEP files."""

import re
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


def _rotate(dir: Path, keep: int | None = None) -> None:
    keep = keep or config.BACKUP_KEEP
    files = sorted(
        (f for f in dir.glob("videolib-*.db") if _PATTERN.match(f.name)),
        key=lambda f: f.name,
    )
    for f in files[:-keep]:
        try:
            f.unlink()
        except OSError:
            pass
