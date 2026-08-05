import threading
from enum import StrEnum
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRunnable,
    QSize,
    QThreadPool,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyledItemDelegate,
    QStyle,
    QTableView,
)

from domain.models import Video
from domain.repository import Repository
from services.thumbnailer import THUMB_HEIGHT, Thumbnailer

COL_THUMB = 0
COL_PLAY = 6


class ViewKind(StrEnum):
    """The four navigation views over the library."""

    CURRENT = "current"
    ALL = "all"
    FAVORITES = "favorites"
    RECENT = "recent"


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


class PlayButtonDelegate(QStyledItemDelegate):
    """Draws a clickable 'play' button inside the play column."""

    BUTTON_TEXT = "▶ 播放"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row: int | None = None
        self._press_row: int | None = None

    def sizeHint(self, option, index):
        fm = QFontMetrics(option.font)
        return QSize(fm.horizontalAdvance(self.BUTTON_TEXT) + 24, fm.height() + 20)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(6, 10, -6, -10)
        if rect.height() <= 0:
            painter.restore()
            return
        pressed = index.row() == self._press_row
        hovered = index.row() == self._hover_row
        if pressed:
            bg, fg = QColor("#2458c9"), QColor("#ffffff")
        elif hovered:
            bg, fg = QColor("#2f6fed"), QColor("#ffffff")
        else:
            bg, fg = QColor("#e6ebf3"), QColor("#3a3f47")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(fg)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.BUTTON_TEXT)
        painter.restore()


class PlayTableView(QTableView):
    """Table with hover/press-aware play column.

    Row heights are fixed (THUMB_HEIGHT): with tens of thousands of rows,
    ResizeToContents would recompute size hints for every row on each reset.
    Long filenames wrap within the fixed cell and the full path is available
    as a tooltip.
    """

    play_clicked = pyqtSignal(int)  # row

    def __init__(self, parent=None):
        super().__init__(parent)
        self._delegate = PlayButtonDelegate(self)
        self._hover_row: int | None = None
        self._press_row: int | None = None
        self.setMouseTracking(True)

    def setModel(self, model) -> None:
        super().setModel(model)
        self.setItemDelegateForColumn(COL_PLAY, self._delegate)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.verticalHeader().setDefaultSectionSize(THUMB_HEIGHT)

    def _play_row_at(self, pos) -> int:
        idx = self.indexAt(pos)
        if idx.isValid() and idx.column() == COL_PLAY:
            return idx.row()
        return -1

    def mouseMoveEvent(self, event) -> None:
        row = self._play_row_at(event.position().toPoint())
        if row != self._hover_row:
            self._hover_row = row
            self._delegate._hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_row = None
        self._press_row = None
        self._delegate._hover_row = None
        self._delegate._press_row = None
        self.viewport().update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        row = self._play_row_at(event.position().toPoint())
        if row >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self._press_row = row
            self._delegate._press_row = row
            self.viewport().update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        row = self._play_row_at(event.position().toPoint())
        if (
            row >= 0
            and row == self._press_row
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.play_clicked.emit(row)
        self._press_row = None
        self._delegate._press_row = None
        self.viewport().update()
        super().mouseReleaseEvent(event)


class VideoTableModel(QAbstractTableModel):
    COLUMNS = ["", "文件名", "时长", "分辨率", "编码", "大小", "播放"]

    thumb_ready = pyqtSignal(int)

    def __init__(self, repo: Repository, thumbs_dir: Path):
        super().__init__()
        self._repo = repo
        self._thumbs_dir = thumbs_dir
        self._videos: list[Video] = []
        self._id_to_row: dict[int, int] = {}
        self._scanning = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._requested: set[int] = set()
        self._lock = threading.Lock()
        self.thumb_ready.connect(self._on_thumb_ready)

    # ---------- data loading ----------

    def set_videos(self, videos: list[Video]) -> None:
        self.beginResetModel()
        self._videos = videos
        self._id_to_row = {v.id: i for i, v in enumerate(videos)}
        self._requested.clear()
        self.endResetModel()

    def set_scanning(self, scanning: bool) -> None:
        """While a scan runs, skip scheduling new thumbnails (avoid I/O thrash)."""
        self._scanning = scanning

    def show(
        self,
        view: ViewKind,
        *,
        root: str | None = None,
        category_id: int | None = None,
        favorite_list_id: int | None = None,
        search_text: str = "",
    ) -> None:
        """Load the videos for a view; the view→query mapping lives here.

        Precedence: search_text filters everything (all dirs), then a
        category filter, then the view kind (favorites/recent/current/all).
        """
        if search_text:
            videos = self._repo.search(search_text)
        elif category_id is not None:
            videos = self._repo.videos_in_category(category_id)
        elif view == ViewKind.FAVORITES:
            if favorite_list_id is not None:
                videos = self._repo.get_favorites(favorite_list_id)
            else:
                videos = self._repo.all_videos()
        elif view == ViewKind.RECENT:
            videos = [v for _rec, v in self._repo.recent_plays(limit=200)]
        elif view == ViewKind.CURRENT:
            videos = self._repo.videos_in_root(root)
        else:
            videos = self._repo.all_videos()
        self.set_videos(videos)

    def all_videos(self) -> list[Video]:
        return self._videos

    def video_at(self, row: int) -> Video | None:
        if 0 <= row < len(self._videos):
            return self._videos[row]
        return None

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
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (2, 3, 4, 5, COL_PLAY):
            return int(Qt.AlignmentFlag.AlignCenter)
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
        if role == Qt.ItemDataRole.DecorationRole and col == COL_THUMB:
            return self._load_thumb(v)
        if role == Qt.ItemDataRole.ToolTipRole:
            return v.filepath
        if role == Qt.ItemDataRole.UserRole:
            return v
        return None

    # ---------- thumbnails ----------

    def _thumb_path(self, video_id: int) -> Path:
        return Thumbnailer.path(self._thumbs_dir, video_id)

    def _load_thumb(self, v: Video) -> QIcon | None:
        thumb = self._thumb_path(v.id)
        if thumb.exists():
            pixmap = QPixmap(str(thumb))
            if not pixmap.isNull():
                return QIcon(pixmap)
        self._schedule(v)
        return QIcon()

    def _schedule(self, v: Video) -> None:
        if self._scanning:
            return
        with self._lock:
            if v.id in self._requested:
                return
            self._requested.add(v.id)
        if not Path(v.filepath).exists():
            return
        runnable = ThumbRunnable(v.filepath, v.id, thumb=self._thumb_path(v.id), repo=self._repo, cb=self)
        self._pool.start(runnable)

    def _on_thumb_ready(self, video_id: int) -> None:
        row = self._id_to_row.get(video_id)
        if row is None:
            return
        idx = self.index(row, COL_THUMB)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])


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
