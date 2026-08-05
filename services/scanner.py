import os
from pathlib import Path

from config import VIDEO_EXTENSIONS
from domain.models import Video


def scan_directory(root: str) -> list[str]:
    """Recursively collect all video file paths under root."""
    found: list[str] = []
    root_path = Path(root)
    for dirpath, _dirnames, filenames in os.walk(root_path):
        for name in filenames:
            if Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                found.append(str(Path(dirpath) / name))
    return found


def diff_scan(
    files: list[str],
    existing: dict[str, tuple[int, float | None]],
) -> tuple[list[str], list[str]]:
    """Compare found files against known records (filepath -> (size, mtime)).

    Returns (need_probe, stale): files whose size/mtime changed (or are new),
    and previously-known paths under the same root that no longer exist.
    """
    existing_paths = set(existing)
    files_set = set(files)
    need_probe = []
    for fp in files:
        cur = existing.get(fp)
        if cur is None:
            need_probe.append(fp)
            continue
        try:
            st = os.stat(fp)
        except OSError:
            continue
        size_ok = cur[0] == st.st_size
        mtime_ok = cur[1] is not None and abs(cur[1] - st.st_mtime) < 1.0
        if not (size_ok and mtime_ok):
            need_probe.append(fp)
    stale = [p for p in existing_paths if p not in files_set]
    return need_probe, stale
