import os
import subprocess
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from domain.models import Video
from domain.repository import Repository
from services.thumbnailer import THUMB_SCALE, THUMB_PIXEL_HEIGHT, THUMB_PIXEL_WIDTH
from ui.video_list import _fmt_duration, _fmt_size


class VideoInfoDialog(QDialog):
    """Read-only details for one video: metadata, categories, favorite lists."""

    def __init__(self, video: Video, repo: Repository, parent=None):
        super().__init__(parent)
        self._video = video
        self._repo = repo
        self.setWindowTitle(f"视频信息 - {video.filename}")
        self.setMinimumWidth(420)

        thumb = self._thumb_pixmap()
        self.thumb_label = QLabel()
        if thumb.isNull():
            self.thumb_label.setText("(无缩略图)")
        else:
            self.thumb_label.setPixmap(thumb)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setMinimumHeight(THUMB_PIXEL_HEIGHT // THUMB_SCALE + 8)

        rows = [
            ("文件名", video.filename),
            ("完整路径", video.filepath),
            ("大小", _fmt_size(video.file_size)),
            ("时长", _fmt_duration(video.duration)),
            ("分辨率", video.resolution or "未知"),
            ("编码", video.codec or "未知"),
            ("加入时间", self._fmt_time(video.scanned_at)),
            ("修改时间", self._fmt_epoch(video.file_mtime)),
            ("所属分类", self._fmt_names(c.name for c in repo.categories_of_video(video.id))),
            ("收藏夹", self._fmt_names(l.name for l in repo.lists_of_video(video.id))),
            ("续播位置", _fmt_duration(repo.last_position(video.id))),
        ]
        self.labels: list[QLabel] = []
        grid = QVBoxLayout()
        for name, value in rows:
            name_l = QLabel(name)
            value_l = QLabel(value)
            name_l.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            value_l.setWordWrap(True)
            value_l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row = QHBoxLayout()
            row.addWidget(name_l)
            row.addWidget(value_l, 1)
            grid.addLayout(row)
            self.labels.append(value_l)

        btn_copy = QPushButton("复制路径")
        btn_copy.clicked.connect(self._copy_path)
        btn_open = QPushButton("打开所在文件夹")
        btn_open.clicked.connect(self._reveal)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(btn_copy)
        buttons.addWidget(btn_open)
        buttons.addStretch(1)
        buttons.addWidget(btn_close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.thumb_label)
        layout.addLayout(grid)
        layout.addLayout(buttons)

    # ---------- helpers ----------

    def _thumb_pixmap(self) -> QPixmap:
        from pathlib import Path

        import config

        candidates = []
        if self._video.thumb_path:
            candidates.append(Path(self._video.thumb_path))
        candidates.append(Path(config.THUMBS_DIR) / f"{self._video.id}.jpg")
        for p in candidates:
            if p.exists():
                pixmap = QPixmap(str(p))
                pixmap.setDevicePixelRatio(THUMB_SCALE)
                return pixmap
        return QPixmap()

    @staticmethod
    def _fmt_time(value) -> str:
        if not value:
            return "未知"
        if isinstance(value, str):
            return value  # DB returns ISO strings already
        try:
            return value.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            return "未知"

    @staticmethod
    def _fmt_epoch(value: float | None) -> str:
        if value is None:
            return "未知"
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _fmt_names(names) -> str:
        values = [n for n in names if n]
        return "、".join(values) if values else "(无)"

    def _copy_path(self) -> None:
        QApplication.clipboard().setText(self._video.filepath)

    def _reveal(self) -> None:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self._video.filepath)])
