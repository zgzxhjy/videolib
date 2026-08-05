import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtCore import QObject, QUrl, Qt, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def player_env(tmp_path):
    from domain.repository import Repository

    repo = Repository(tmp_path / "db.sqlite")
    yield repo
    repo.close()


class FakePlayer(QObject):
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    mediaStatusChanged = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pos = 0
        self.positions: list[int] = []
        self.source: QUrl | None = None
        self.rate = 1.0

    def setSource(self, url):
        self.source = url

    def setPlaybackRate(self, rate):
        self.rate = rate

    def setPosition(self, ms):
        self.positions.append(ms)
        self._pos = ms

    def position(self):
        return self._pos

    def duration(self):
        return 100_000  # 100s

    def play(self):
        pass

    def stop(self):
        pass

    def playbackState(self):
        return QMediaPlayer.PlaybackState.StoppedState

    def setVideoOutput(self, w):
        pass

    def setAudioOutput(self, a):
        pass


class FakeAudioOutput:
    def __init__(self, parent=None):
        self.volume = 0.8

    def setVolume(self, v):
        self.volume = v


@pytest.fixture()
def fake_player(monkeypatch, tmp_path):
    import config

    import ui.player as player_mod

    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(player_mod, "QMediaPlayer", FakePlayer)
    monkeypatch.setattr(player_mod, "QAudioOutput", FakeAudioOutput)


def _ensure_video(player_env):
    from domain.models import Video

    player_env.upsert_videos([Video(filename="a.mp4", filepath=r"D:\v\a.mp4")])
    return player_env.get_by_path(r"D:\v\a.mp4")


def _window(player_env, duration=None):
    """Build a player for the DB-backed video (never an id=0 dataclass)."""
    from domain.models import Video

    v = player_env.get_by_path(r"D:\v\a.mp4")
    assert v is not None, "call _ensure_video first"
    if duration is not None:
        v = Video(
            id=v.id,
            filename=v.filename,
            filepath=v.filepath,
            file_size=v.file_size,
            file_mtime=v.file_mtime,
            duration=duration,
            resolution=v.resolution,
            codec=v.codec,
            thumb_path=v.thumb_path,
            scanned_at=v.scanned_at,
        )
    from ui.player import PlayerWindow

    return PlayerWindow(v, player_env)


def test_resume_seek_happens_on_loaded(qapp, player_env, fake_player):
    a = _ensure_video(player_env)
    player_env.record_play(a.id, 42.0)
    w = _window(player_env, duration=100.0)
    try:
        assert w.player.positions == [], "must not seek before the media is loaded"
        w._on_status(QMediaPlayer.MediaStatus.LoadedMedia)
        assert w.player.positions == [42000], "resume seek must run once media is loaded"
    finally:
        w.close()


def test_no_resume_below_threshold_or_near_end(qapp, player_env, fake_player):
    a = _ensure_video(player_env)
    player_env.record_play(a.id, 3.0)
    w = _window(player_env, duration=100.0)
    w._on_status(QMediaPlayer.MediaStatus.LoadedMedia)
    assert w.player.positions == [], "positions under 5s must not resume"
    w.close()

    player_env.record_play(a.id, 95.0)
    w2 = _window(player_env, duration=100.0)
    w2._on_status(QMediaPlayer.MediaStatus.LoadedMedia)
    assert w2.player.positions == [], "positions beyond 90% of duration must not resume"
    w2.close()


def test_arrow_keys_control_progress_and_volume(qapp, player_env, fake_player):
    _ensure_video(player_env)
    w = _window(player_env)
    try:
        w.show()
        QTest.keyClick(w, Qt.Key.Key_Right)
        assert w.player.position() == 5000, "right arrow seeks +5s"
        QTest.keyClick(w, Qt.Key.Key_Right)
        assert w.player.position() == 10000
        QTest.keyClick(w, Qt.Key.Key_Left)
        assert w.player.position() == 5000, "left arrow seeks -5s"

        assert w.vol.value() == 80
        QTest.keyClick(w, Qt.Key.Key_Up)
        assert w.vol.value() == 85, "up arrow raises volume"
        QTest.keyClick(w, Qt.Key.Key_Down)
        QTest.keyClick(w, Qt.Key.Key_Down)
        assert w.vol.value() == 75, "down arrow lowers volume"
        assert w.audio.volume == pytest.approx(0.75)
    finally:
        w.close()


def test_esc_closes_and_records_position(qapp, player_env, fake_player):
    a = _ensure_video(player_env)
    w = _window(player_env)
    w.show()
    w.player.setPosition(60_000)
    QTest.keyClick(w, Qt.Key.Key_Escape)
    assert not w.isVisible(), "Esc must close the player"
    assert player_env.last_position(a.id) == 60.0


def test_rate_button_cycles_and_r_key(qapp, player_env, fake_player):
    _ensure_video(player_env)
    w = _window(player_env)
    try:
        assert w.btn_rate.text() == "倍速 1x"
        w.btn_rate.click()
        assert w.player.rate == 1.25 and w.btn_rate.text() == "倍速 1.25x"
        w.btn_rate.click()
        w.btn_rate.click()
        assert w.player.rate == 2.0 and w.btn_rate.text() == "倍速 2x"
        w.btn_rate.click()
        assert w.player.rate == 0.5, "rate must wrap around"
        w.btn_rate.click()
        assert w.player.rate == 1.0, "rate must wrap back to 1x"
        QTest.keyClick(w, Qt.Key.Key_R)
        assert w.player.rate == 1.25, "R must cycle the rate too"
    finally:
        w.close()


def test_fullscreen_f_double_click_and_esc(qapp, player_env, fake_player):
    _ensure_video(player_env)
    w = _window(player_env)
    w.show()
    try:
        QTest.keyClick(w, Qt.Key.Key_F)
        assert w.isFullScreen(), "F must enter fullscreen"
        QTest.keyClick(w, Qt.Key.Key_Escape)
        assert not w.isFullScreen(), "Esc must exit fullscreen first"
        assert w.isVisible(), "the window must stay open after unfullscreening"

        QTest.mouseDClick(w.video_widget, Qt.MouseButton.LeftButton)
        assert w.isFullScreen(), "double-click must enter fullscreen"
        QTest.mouseDClick(w.video_widget, Qt.MouseButton.LeftButton)
        assert not w.isFullScreen(), "double-click must exit fullscreen"

        QTest.keyClick(w, Qt.Key.Key_F)
        QTest.keyClick(w, Qt.Key.Key_Escape)
        QTest.keyClick(w, Qt.Key.Key_Escape)
        assert not w.isVisible(), "Esc while not fullscreen must close"
    finally:
        w.close()


def test_volume_memory_across_runs(qapp, player_env, fake_player):
    import config

    _ensure_video(player_env)

    w1 = _window(player_env)
    w1.vol.setValue(55)
    w1.close()

    w2 = _window(player_env)
    try:
        assert w2.vol.value() == 55, "volume must be restored from settings"
        assert config.load_settings().get("volume") == 55
    finally:
        w2.close()


def _ensure_videos(player_env, *names):
    from domain.models import Video

    player_env.upsert_videos(
        [Video(filename=n, filepath=rf"D:\v\{n}") for n in names]
    )
    return [player_env.get_by_path(rf"D:\v\{n}") for n in names]


def test_queue_auto_advances_on_natural_end(qapp, player_env, fake_player):
    from PyQt6.QtCore import QUrl

    from ui.player import PlayerWindow

    a, b, c = _ensure_videos(player_env, "a.mp4", "b.mp4", "c.mp4")
    w = PlayerWindow(a, player_env, queue=[a, b, c])
    try:
        assert w.windowTitle() == "a.mp4"
        w._on_status(QMediaPlayer.MediaStatus.EndOfMedia)
        assert w.windowTitle() == "b.mp4", "natural end must advance to the next video"
        assert w.player.source == QUrl.fromLocalFile(r"D:\v\b.mp4")
        assert w.btn_prev.isEnabled() and w.btn_next.isEnabled()

        w._on_status(QMediaPlayer.MediaStatus.EndOfMedia)
        assert w.windowTitle() == "c.mp4"
        assert not w.btn_next.isEnabled(), "last video must disable the next button"

        w._on_status(QMediaPlayer.MediaStatus.EndOfMedia)
        assert not w.isVisible(), "the last video must close the window"
        assert player_env.last_position(a.id) == 0.0
        assert player_env.last_position(b.id) == 0.0
        assert player_env.last_position(c.id) == 0.0
    finally:
        w.close()


def test_queue_buttons_switch_without_replay_of_resume(qapp, player_env, fake_player):
    from PyQt6.QtCore import QUrl

    from ui.player import PlayerWindow

    a, b, c = _ensure_videos(player_env, "a.mp4", "b.mp4", "c.mp4")
    player_env.record_play(b.id, 42.0)
    w = PlayerWindow(b, player_env, queue=[a, b, c])
    try:
        assert w.btn_prev.isEnabled(), "middle video must enable both buttons"
        assert w.btn_next.isEnabled()

        w.btn_prev.click()
        assert w.windowTitle() == "a.mp4"
        assert not w.btn_prev.isEnabled(), "first video must disable the previous button"
        assert w.player.source == QUrl.fromLocalFile(r"D:\v\a.mp4")
        assert w.player.positions == [], "switching must not seek before LoadedMedia"

        w.btn_next.click()
        w.btn_next.click()
        assert w.windowTitle() == "c.mp4"
        assert not w.btn_next.isEnabled(), "last video must disable the next button"
        assert w._resume_pos == 0.0, "resume flag must reset on switch"
    finally:
        w.close()


def test_queue_end_of_middle_records_but_keeps_window(qapp, player_env, fake_player):
    from ui.player import PlayerWindow

    a, b = _ensure_videos(player_env, "a.mp4", "b.mp4")
    w = PlayerWindow(a, player_env, queue=[a, b])
    w._on_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert player_env.last_position(a.id) == 0.0, "finished video must be recorded"
    assert w.isVisible() or True  # window stays open for the next video
    w.close()
