from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Video:
    id: int = 0
    filename: str = ""
    filepath: str = ""
    file_size: int = 0
    file_mtime: float | None = None
    duration: float | None = None
    resolution: str | None = None
    codec: str | None = None
    thumb_path: str | None = None
    scanned_at: datetime = field(default_factory=datetime.now)


@dataclass
class Category:
    id: int = 0
    name: str = ""
    parent_id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class PlayRecord:
    id: int = 0
    video_id: int = 0
    played_at: datetime = field(default_factory=datetime.now)
    position: float = 0.0
