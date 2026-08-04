import threading
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRunnable,
    QThreadPool,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QIcon, QPixmap

from domain.models import Video
from domain.repository import Repository
from services.thumbnailer import Thumbnailer


class ThumbRunnable(QRunnable):
    """Generate one thumbnail off the UI thread, then notify the model."""

    def __init__(self, filepath: str, video_id: int, thumb: Path, repo: Repository, cb):
        super().__init__()
        self.filepath = filepath
        self.video_id = video_id
        self.thumb = thumb
        self.repo = repo
        self.cb = cb

    def run(self):
        ok = Thumbnailer().ensure(self.filepath, self.video_id, repo=self.repo)
        if ok:
            self.cb.thumb_ready.emit(self.video_id)


class VideoTableModel(QAbstractTableModel):
    COLUMNS = ["", "文件名", "时长", "分辨率", "编码", "大小"]

    thumb_ready = pyqtSignal(int)

    def __init__(self, repo: Repository, thumbs_dir: Path):
        super().__init__()
        self._repo = repo
        self._thumbs_dir = thumbs_dir
        self._videos: list[Video] = []
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._requested: set[int] = set()
        self._lock = threading.Lock()
        self.thumb_ready.connect(self._on_thumb_ready)

    # ---------- data loading ----------

    def set_videos(self, videos: list[Video]) -> None:
        self.beginResetModel()
        self._videos = videos
        self._requested.clear()
        self.endResetModel()

    def refresh(self, query: str = "", category_id: int | None = None) -> None:
        if category_id is not None:
            videos = self._repo.videos_in_category(category_id)
        elif query:
            videos = self._repo.search(query)
        else:
            videos = self._repo.all_videos()
        self.set_videos(videos)

    def refresh_favorites(self) -> None:
        self.set_videos(self._repo.get_favorites())

    def refresh_recent(self) -> None:
        self.set_videos([v for _rec, v in self._repo.recent_plays(limit=200)])

    def all_videos(self) -> list[Video]:
        return self._videos

    # ---------- QAbstractTableModel ----------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._videos)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        v = self._videos[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (2, 3, 4, 5):
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ForegroundRole and col == 0:
            return QColor("#888888")
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 1:
                return v.filename
            if col == 2:
                return _fmt_duration(v.duration)
            if col == 3:
                return v.resolution or ""
            if col == 4:
                return v.codec or ""
            if col == 5:
                return _fmt_size(v.file_size)
        if role == Qt.ItemDataRole.DecorationRole and col == 0:
            return self._load_thumb(v)
        if role == Qt.ItemDataRole.UserRole:
            return v
        return None

    # ---------- thumbnails ----------

    def _thumb_path(self, video_id: int) -> Path:
        return self._thumbs_dir / f"{video_id}.jpg"

    def _load_thumb(self, v: Video) -> QIcon | None:
        thumb = self._thumb_path(v.id)
        if thumb.exists():
            return QIcon(QPixmap(str(thumb)))
        self._schedule(v)
        return QIcon()

    def _schedule(self, v: Video) -> None:
        with self._lock:
            if v.id in self._requested:
                return
            self._requested.add(v.id)
        if not Path(v.filepath).exists():
            return
        runnable = ThumbRunnable(v.filepath, v.id, thumb=self._thumb_path(v.id), repo=self._repo, cb=self)
        self._pool.start(runnable)

    def _on_thumb_ready(self, video_id: int) -> None:
        for i, v in enumerate(self._videos):
            if v.id == video_id:
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
                return


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fmt_size(size: int) -> str:
    if size <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
