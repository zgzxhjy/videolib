from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from domain.models import Video
from domain.repository import Repository


class PlayerWindow(QWidget):
    """Playback window with resume and play-history recording."""

    finished = pyqtSignal(int, float)  # video_id, last position

    def __init__(self, video: Video, repo: Repository, parent=None):
        super().__init__(parent)
        self._video = video
        self._repo = repo
        self._closing = False
        self._user_seeking = False

        self.setWindowTitle(video.filename)
        self.resize(960, 560)

        self.video_widget = QVideoWidget()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setVideoOutput(self.video_widget)
        self.player.setAudioOutput(self.audio)

        self.btn_play = QPushButton("暂停")
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self._stop)
        self.time_label = QLabel("00:00 / 00:00")

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderPressed.connect(lambda: self._set_seeking(True))
        self.slider.sliderReleased.connect(self._seek_to_slider)
        self.slider.sliderMoved.connect(lambda v: self.time_label.setText(f"{_fmt(v)} / {_fmt(self.player.duration() // 1000)}"))

        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))

        controls = QHBoxLayout()
        controls.addWidget(self.btn_play)
        controls.addWidget(self.btn_stop)
        controls.addWidget(self.time_label)
        controls.addWidget(self.slider, 1)
        controls.addWidget(QLabel("音量"))
        controls.addWidget(self.vol)

        layout = QVBoxLayout(self)
        layout.addWidget(self.video_widget, 1)
        layout.addLayout(controls)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda _d: self._refresh_label())
        self.player.mediaStatusChanged.connect(self._on_status)

        self._start()

    # ---------- playback ----------

    def _start(self) -> None:
        resume = self._repo.last_position(self._video.id)
        self.player.setSource(QUrl.fromLocalFile(self._video.filepath))
        if resume > 5.0 and (self._video.duration is None or resume < self._video.duration * 0.9):
            self.player.setPosition(int(resume * 1000))
        self.player.play()

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("播放")
        else:
            self.player.play()
            self.btn_play.setText("暂停")

    def _stop(self) -> None:
        self.player.stop()
        self.btn_play.setText("播放")

    def _set_seeking(self, value: bool) -> None:
        self._user_seeking = value

    def _seek_to_slider(self) -> None:
        self.player.setPosition(self.slider.value())
        self._set_seeking(False)

    def _on_position(self, pos_ms: int) -> None:
        if not self._user_seeking:
            self.slider.setMaximum(self.player.duration())
            self.slider.setValue(pos_ms)
        self.time_label.setText(f"{_fmt(pos_ms // 1000)} / {_fmt(self.player.duration() // 1000)}")

    def _refresh_label(self) -> None:
        self.slider.setMaximum(self.player.duration())

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._finish(0.0)
        elif status == QMediaPlayer.MediaStatus.LoadedMedia:
            pass

    # ---------- lifecycle ----------

    def _finish(self, position: float) -> None:
        self._repo.record_play(self._video.id, position)
        self.finished.emit(self._video.id, position)
        self._closing = True
        self.close()

    def closeEvent(self, event) -> None:
        if not self._closing:
            pos = self.player.position() / 1000.0
            self._repo.record_play(self._video.id, pos)
            self.finished.emit(self._video.id, pos)
        self.player.stop()
        super().closeEvent(event)


def _fmt(total_seconds: int) -> str:
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
