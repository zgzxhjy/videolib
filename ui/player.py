from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import config
from domain.models import Video
from domain.repository import Repository
from services.mpv_session import MpvSession
from ui.video_widget import VideoWidget

# Playback-state strings emitted by MpvSession.
_PLAYING = "playing"
_PAUSED = "paused"
_STOPPED = "stopped"


class PlayerWindow(QWidget):
    """Playback window with resume and play-history recording."""

    finished = pyqtSignal(int, float)  # video_id, last position

    SEEK_STEP_S = 5
    VOL_STEP = 5
    RATES = (0.5, 1.0, 1.25, 1.5, 2.0)

    def __init__(self, video: Video, repo: Repository, queue: list[Video] | None = None, parent=None):
        super().__init__(parent)
        self._queue = list(queue) if queue else [video]
        try:
            self._queue_index = next(
                i for i, v in enumerate(self._queue) if v.id == video.id
            )
        except StopIteration:
            self._queue = [video]
            self._queue_index = 0
        self._video = self._queue[self._queue_index]
        self._repo = repo
        self._closing = False
        self._user_seeking = False
        self._resume_pos = 0.0
        self._rate_idx = self.RATES.index(1.0)
        self._rate = 1.0
        self._muted = False
        self._loop_mode = 0  # 0 off, 1 single, 2 all

        self.setWindowTitle(self._video.filename)
        self._fit_size(self._video)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.video_widget = VideoWidget()
        self.video_widget.installEventFilter(self)
        self.session = MpvSession(self.video_widget, parent=self)
        self.session.start()

        self.btn_prev = QPushButton("⏮")
        self.btn_prev.clicked.connect(self._prev)
        self.btn_play = QPushButton("暂停")
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_next = QPushButton("⏭")
        self.btn_next.clicked.connect(self._next)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_rate = QPushButton(self._rate_label())
        self.btn_rate.clicked.connect(self._cycle_rate)
        self.btn_mute = QPushButton("静音")
        self.btn_mute.clicked.connect(self._toggle_mute)
        self.btn_loop = QPushButton(self._loop_label())
        self.btn_loop.clicked.connect(self._cycle_loop)
        self.time_label = QLabel("00:00 / 00:00")

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderPressed.connect(lambda: self._set_seeking(True))
        self.slider.sliderReleased.connect(self._seek_to_slider)
        self.slider.sliderMoved.connect(lambda v: self.time_label.setText(f"{_fmt(v)} / {_fmt(self.session.duration() // 1000)}"))

        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(int(config.load_settings().get("volume", 80)))
        self.vol.valueChanged.connect(lambda v: self.session.set_volume(v / 100))

        controls = QHBoxLayout()
        controls.addWidget(self.btn_prev)
        controls.addWidget(self.btn_next)
        controls.addWidget(self.btn_play)
        controls.addWidget(self.btn_stop)
        controls.addWidget(self.btn_rate)
        controls.addWidget(self.btn_mute)
        controls.addWidget(self.btn_loop)
        controls.addWidget(self.time_label)
        controls.addWidget(self.slider, 1)
        controls.addWidget(QLabel("音量"))
        controls.addWidget(self.vol)

        layout = QVBoxLayout(self)
        layout.addWidget(self.video_widget, 1)
        layout.addLayout(controls)

        self.session.positionChanged.connect(self._on_position)
        self.session.durationChanged.connect(lambda _d: self._refresh_label())
        self.session.mediaStatusChanged.connect(self._on_status)
        self.session.endOfMedia.connect(self._on_end)
        self.session.stateChanged.connect(self._sync_play_button)

        self._start()
        self.setFocus()

    # ---------- playback ----------

    def _start(self) -> None:
        self._load(self._queue_index)

    def _load(self, index: int) -> None:
        """Switch the player to the video at `index` in the queue."""
        self._queue_index = index
        self._video = self._queue[index]
        self.setWindowTitle(self._video.filename)
        self._fit_size(self._video)
        self._resume_pos = 0.0
        resume = self._repo.last_position(self._video.id)
        if resume > 5.0 and (self._video.duration is None or resume < self._video.duration * 0.9):
            self._resume_pos = resume
        self.session.load(self._video.filepath, resume_sec=self._resume_pos)
        self._resume_pos = 0.0
        self.time_label.setText("00:00 / 00:00")
        self._update_nav_buttons()

    def _fit_size(self, video: Video) -> None:
        """按视频宽高比自适应窗口尺寸(面积守恒 + 屏幕 clamp)。

        resolution 形如 "1920x1080"; 缺失/坏格式回退默认 960x560。
        """
        area = 960 * 560
        try:
            vw, vh = (int(x) for x in video.resolution.lower().split("x"))
        except (AttributeError, ValueError):
            vw, vh = 16, 9
        if vw <= 0 or vh <= 0:
            vw, vh = 16, 9
        ratio = vw / vh
        w = int(round((area * ratio) ** 0.5))
        h = int(round(w / ratio))
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = min(w, int(avail.width() * 0.85))
            h = min(h, int(avail.height() * 0.85))
        self.resize(max(1, w), max(1, h))

    def _prev(self) -> None:
        if self._queue_index > 0:
            self._load(self._queue_index - 1)

    def _next(self) -> None:
        if self._queue_index < len(self._queue) - 1:
            self._load(self._queue_index + 1)

    def _update_nav_buttons(self) -> None:
        self.btn_prev.setEnabled(self._queue_index > 0)
        self.btn_next.setEnabled(self._queue_index < len(self._queue) - 1)

    def _toggle_play(self) -> None:
        if self.session.state() == _PLAYING:
            self.session.pause()
            self.btn_play.setText("播放")
        else:
            self.session.play()
            self.btn_play.setText("暂停")

    def _sync_play_button(self, state: str) -> None:
        """Keep the button label truthful to mpv's actual pause state.

        The keep-open EOF pause arrives as an async property-change; without
        this sync the button could read 暂停 while mpv is really paused.
        """
        self.btn_play.setText("暂停" if state == _PLAYING else "播放")

    def _cycle_rate(self) -> None:
        self._rate_idx = (self._rate_idx + 1) % len(self.RATES)
        self._rate = self.RATES[self._rate_idx]
        self.session.set_rate(self._rate)
        self.btn_rate.setText(self._rate_label())

    def _rate_label(self) -> str:
        return f"倍速 {self._rate:g}x"

    def _toggle_mute(self) -> None:
        self._muted = not self._muted
        self.session.set_mute(self._muted)
        self.btn_mute.setText("已静音" if self._muted else "静音")

    def _cycle_loop(self) -> None:
        self._loop_mode = (self._loop_mode + 1) % 3
        self.btn_loop.setText(self._loop_label())

    def _loop_label(self) -> str:
        return ("循环:关", "循环:单曲", "循环:全部")[self._loop_mode]

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.video_widget and event.type() == QEvent.Type.MouseButtonDblClick:
            self._toggle_fullscreen()
            return True
        return super().eventFilter(obj, event)

    def _stop(self) -> None:
        self.session.stop()
        self.btn_play.setText("播放")

    def _set_seeking(self, value: bool) -> None:
        self._user_seeking = value

    def _seek_to_slider(self) -> None:
        self.session.seek(self.slider.value())
        self._set_seeking(False)

    def _on_position(self, pos_ms: int) -> None:
        if not self._user_seeking:
            self.slider.setMaximum(self.session.duration())
            self.slider.setValue(pos_ms)
        self.time_label.setText(f"{_fmt(pos_ms // 1000)} / {_fmt(self.session.duration() // 1000)}")

    def _refresh_label(self) -> None:
        self.slider.setMaximum(self.session.duration())

    def _on_status(self, status) -> None:
        if self._closing:
            return
        if status == "error":
            self._finish(self.session.position() / 1000.0)

    def _on_end(self) -> None:
        if self._closing:
            return
        if self._loop_mode == 1:
            # single loop: mark finished, replay the same video
            self._repo.record_play(self._video.id, 0.0)
            self._load(self._queue_index)
        elif self._queue_index < len(self._queue) - 1:
            # natural end: record the finished video, then roll on
            self._repo.record_play(self._video.id, 0.0)
            self._load(self._queue_index + 1)
        elif self._loop_mode == 2:
            # all loop: wrap back to the first video
            self._repo.record_play(self._video.id, 0.0)
            self._load(0)
        else:
            self._finish(0.0)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Right:
            self.session.seek(self.session.position() + self.SEEK_STEP_S * 1000)
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            self.session.seek(max(0, self.session.position() - self.SEEK_STEP_S * 1000))
            event.accept()
            return
        if key == Qt.Key.Key_Up:
            self.vol.setValue(min(100, self.vol.value() + self.VOL_STEP))
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            self.vol.setValue(max(0, self.vol.value() - self.VOL_STEP))
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.close()
            event.accept()
            return
        if key == Qt.Key.Key_F:
            self._toggle_fullscreen()
            event.accept()
            return
        if key == Qt.Key.Key_R:
            self._cycle_rate()
            event.accept()
            return
        if key == Qt.Key.Key_M:
            self._toggle_mute()
            event.accept()
            return
        if key == Qt.Key.Key_L:
            self._cycle_loop()
            event.accept()
            return
        super().keyPressEvent(event)

    # ---------- lifecycle ----------

    def _finish(self, position: float) -> None:
        self._repo.record_play(self._video.id, position)
        self.finished.emit(self._video.id, position)
        self._closing = True
        self.close()

    def closeEvent(self, event) -> None:
        config.save_setting("volume", self.vol.value())
        if not self._closing:
            pos = self.session.position() / 1000.0
            if pos < 5.0:
                # a trivial reopen-and-close must not clobber the resume point
                pos = max(pos, self._repo.last_position(self._video.id))
            self._repo.record_play(self._video.id, pos)
            self.finished.emit(self._video.id, pos)
        self.session.close()
        super().closeEvent(event)


def _fmt(total_seconds: int) -> str:
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
