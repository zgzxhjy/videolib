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
