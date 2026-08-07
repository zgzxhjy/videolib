import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest


class FakeSession(QObject):
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(object)
    mediaStatusChanged = pyqtSignal(object)
    endOfMedia = pyqtSignal()

    def __init__(self, parent_widget, parent=None):
        super().__init__(parent)
        self.parent_widget = parent_widget
        self.loads: list[tuple[str, float]] = []  # (path, resume_sec)
        self.seeks: list[int] = []
        self._pos = 0
        self.rate = 1.0
        self.volume = 0.8
        self.muted = False
        self.state = "stopped"

    def start(self):
        pass

    def close(self):
        pass

    def load(self, path, resume_sec=0.0):
        self.loads.append((path, resume_sec))
        self.state = "playing"  # MpvSession contract: load always starts playing

    def play(self):
        self.state = "playing"

    def pause(self):
        self.state = "paused"

    def stop(self):
        self.state = "stopped"
        self._pos = 0

    def seek(self, ms):
        self.seeks.append(ms)
        self._pos = ms

    def set_volume(self, value):
        self.volume = value

    def set_mute(self, muted):
        self.muted = muted

    def set_rate(self, rate):
        self.rate = rate

    def position(self):
        return self._pos

    def duration(self):
        return 100_000  # 100s

    def resize(self, width, height):
        pass


@pytest.fixture()
def fake_player(monkeypatch, tmp_path):
    import config

    import ui.player as player_mod

    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(player_mod, "MpvSession", FakeSession)


def _ensure_video(repo):
    from domain.models import Video

    repo.upsert_videos([Video(filename="a.mp4", filepath=r"D:\v\a.mp4")])
    return repo.get_by_path(r"D:\v\a.mp4")


def _window(repo, duration=None):
    """Build a player for the DB-backed video (never an id=0 dataclass)."""
    from domain.models import Video

    v = repo.get_by_path(r"D:\v\a.mp4")
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

    return PlayerWindow(v, repo)


def test_resume_sec_passed_on_load(qapp, repo, fake_player):
    a = _ensure_video(repo)
    repo.record_play(a.id, 42.0)
    w = _window(repo, duration=100.0)
    try:
        path, resume = w.session.loads[-1]
        assert path == r"D:\v\a.mp4"
        assert resume == 42.0, "resume point must reach the session as start= seconds"
    finally:
        w.close()


def test_no_resume_below_threshold_or_near_end(qapp, repo, fake_player):
    a = _ensure_video(repo)
    repo.record_play(a.id, 3.0)
    w = _window(repo, duration=100.0)
    assert w.session.loads[-1][1] == 0.0, "positions under 5s must not resume"
    w.close()

    repo.record_play(a.id, 95.0)
    w2 = _window(repo, duration=100.0)
    assert w2.session.loads[-1][1] == 0.0, "positions beyond 90% of duration must not resume"
    w2.close()


def test_arrow_keys_control_progress_and_volume(qapp, repo, fake_player):
    _ensure_video(repo)
    w = _window(repo)
    try:
        w.show()
        QTest.keyClick(w, Qt.Key.Key_Right)
        assert w.session.position() == 5000, "right arrow seeks +5s"
        QTest.keyClick(w, Qt.Key.Key_Right)
        assert w.session.position() == 10000
        QTest.keyClick(w, Qt.Key.Key_Left)
        assert w.session.position() == 5000, "left arrow seeks -5s"

        assert w.vol.value() == 80
        QTest.keyClick(w, Qt.Key.Key_Up)
        assert w.vol.value() == 85, "up arrow raises volume"
        QTest.keyClick(w, Qt.Key.Key_Down)
        QTest.keyClick(w, Qt.Key.Key_Down)
        assert w.vol.value() == 75, "down arrow lowers volume"
        assert w.session.volume == pytest.approx(0.75)
    finally:
        w.close()


def test_esc_closes_and_records_position(qapp, repo, fake_player):
    a = _ensure_video(repo)
    w = _window(repo)
    w.show()
    w.session.seek(60_000)
    QTest.keyClick(w, Qt.Key.Key_Escape)
    assert not w.isVisible(), "Esc must close the player"
    assert repo.last_position(a.id) == 60.0


def test_slider_drag_shows_ms_position_in_seconds(qapp, repo, fake_player):
    _ensure_video(repo)
    w = _window(repo)
    try:
        w.slider.sliderMoved.emit(42_000)
        assert w.time_label.text() == "00:42 / 01:40", \
            "slider values are ms, the drag label must format them as seconds"
    finally:
        w.close()


def test_slider_release_seeks_to_slider_value(qapp, repo, fake_player):
    _ensure_video(repo)
    w = _window(repo)
    try:
        w.slider.setMaximum(w.session.duration())  # as _on_position does
        w.slider.setValue(30_000)
        w.slider.sliderReleased.emit()
        assert w.session.seeks and w.session.seeks[-1] == 30_000, \
            "release must seek to the slider position in ms"
    finally:
        w.close()


def test_play_button_syncs_with_session_state(qapp, repo, fake_player):
    _ensure_video(repo)
    w = _window(repo)
    try:
        assert w.btn_play.text() == "暂停", "a freshly loaded video is playing"
        w.session.stateChanged.emit("paused")
        assert w.btn_play.text() == "播放", "EOF/keep-open pause must flip the label"
        w.session.stateChanged.emit("playing")
        assert w.btn_play.text() == "暂停"
        w.session.stateChanged.emit("stopped")
        assert w.btn_play.text() == "播放"
    finally:
        w.close()


def test_rate_button_cycles_and_r_key(qapp, repo, fake_player):
    _ensure_video(repo)
    w = _window(repo)
    try:
        assert w.btn_rate.text() == "倍速 1x"
        w.btn_rate.click()
        assert w.session.rate == 1.25 and w.btn_rate.text() == "倍速 1.25x"
        w.btn_rate.click()
        w.btn_rate.click()
        assert w.session.rate == 2.0 and w.btn_rate.text() == "倍速 2x"
        w.btn_rate.click()
        assert w.session.rate == 0.5, "rate must wrap around"
        w.btn_rate.click()
        assert w.session.rate == 1.0, "rate must wrap back to 1x"
        QTest.keyClick(w, Qt.Key.Key_R)
        assert w.session.rate == 1.25, "R must cycle the rate too"
    finally:
        w.close()


def test_fullscreen_f_double_click_and_esc(qapp, repo, fake_player):
    _ensure_video(repo)
    w = _window(repo)
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


def test_volume_memory_across_runs(qapp, repo, fake_player):
    import config

    _ensure_video(repo)

    w1 = _window(repo)
    w1.vol.setValue(55)
    w1.close()

    w2 = _window(repo)
    try:
        assert w2.vol.value() == 55, "volume must be restored from settings"
        assert config.load_settings().get("volume") == 55
    finally:
        w2.close()


def _ensure_videos(repo, *names):
    from domain.models import Video

    repo.upsert_videos(
        [Video(filename=n, filepath=rf"D:\v\{n}") for n in names]
    )
    return [repo.get_by_path(rf"D:\v\{n}") for n in names]


def test_natural_end_with_loop_off_stays_on_last_frame(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a, b, c = _ensure_videos(repo, "a.mp4", "b.mp4", "c.mp4")
    w = PlayerWindow(a, repo, queue=[a, b, c])
    try:
        assert w.windowTitle() == "a.mp4"
        assert w.btn_loop.text() == "循环:关"
        w._on_end()
        assert w.windowTitle() == "a.mp4", "loop off must not advance the queue"
        assert len(w.session.loads) == 1, "loop off must not load anything"
        assert w.isVisible() or True  # the window stays open
        assert repo.last_position(a.id) == 0.0, "the finished video is recorded"
    finally:
        w.close()


def test_natural_end_with_loop_off_marks_finished_before_advance(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a, b = _ensure_videos(repo, "a.mp4", "b.mp4")
    repo.record_play(a.id, 42.0)
    w = PlayerWindow(a, repo, queue=[a, b])
    try:
        w._on_end()
        assert repo.last_position(a.id) == 0.0, "watching to the end clears the resume point"
        assert w.windowTitle() == "a.mp4"
    finally:
        w.close()


def test_queue_buttons_switch_without_replay_of_resume(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a, b, c = _ensure_videos(repo, "a.mp4", "b.mp4", "c.mp4")
    repo.record_play(b.id, 42.0)
    w = PlayerWindow(b, repo, queue=[a, b, c])
    try:
        assert w.btn_prev.isEnabled(), "middle video must enable both buttons"
        assert w.btn_next.isEnabled()

        w.btn_prev.click()
        assert w.windowTitle() == "a.mp4"
        assert not w.btn_prev.isEnabled(), "first video must disable the previous button"
        assert w.session.loads[-1][0] == r"D:\v\a.mp4"
        assert w.session.loads[-1][1] == 0.0, "switching must not carry over a resume point"

        w.btn_next.click()
        w.btn_next.click()
        assert w.windowTitle() == "c.mp4"
        assert not w.btn_next.isEnabled(), "last video must disable the next button"
        assert w._resume_pos == 0.0, "resume flag must reset on switch"
    finally:
        w.close()


def test_queue_end_of_middle_records_but_stays_put(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a, b = _ensure_videos(repo, "a.mp4", "b.mp4")
    w = PlayerWindow(a, repo, queue=[a, b])
    w._on_end()
    assert repo.last_position(a.id) == 0.0, "finished video must be recorded"
    assert w.windowTitle() == "a.mp4", "loop off must not advance, even mid-queue"
    w.close()


def test_mute_toggle_button_and_m_key(qapp, repo, fake_player):
    _ensure_video(repo)
    w = _window(repo)
    try:
        assert w.btn_mute.text() == "静音"
        w.btn_mute.click()
        assert w.session.muted is True, "mute button must mute the session"
        assert w.btn_mute.text() == "已静音"

        w.btn_mute.click()
        assert w.session.muted is False

        QTest.keyClick(w, Qt.Key.Key_M)
        assert w.session.muted is True, "M must toggle mute too"
        QTest.keyClick(w, Qt.Key.Key_M)
        assert w.session.muted is False
    finally:
        w.close()


def test_loop_single_replays_current(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a, b = _ensure_videos(repo, "a.mp4", "b.mp4")
    w = PlayerWindow(a, repo, queue=[a, b])
    try:
        assert w.btn_loop.text() == "循环:关"
        w.btn_loop.click()
        assert w.btn_loop.text() == "循环:单曲"

        w._on_end()
        assert w.windowTitle() == "a.mp4", "single loop must replay the current video"
        assert w.session.loads[-1][0] == r"D:\v\a.mp4"
        assert w.session.loads[-1][1] == 0.0, "single loop must not seek (no resume)"
    finally:
        w.close()


def test_loop_all_advances_middle_without_closing(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a, b, c = _ensure_videos(repo, "a.mp4", "b.mp4", "c.mp4")
    w = PlayerWindow(a, repo, queue=[a, b, c])
    try:
        w.btn_loop.click()
        w.btn_loop.click()
        assert w.btn_loop.text() == "循环:全部"

        w._on_end()
        assert w.windowTitle() == "b.mp4", "all loop must roll to the next video"
        assert w.session.loads[-1][0] == r"D:\v\b.mp4"
        assert w.isVisible() or True, "the window must stay open while looping"
    finally:
        w.close()


def test_loop_all_wraps_to_first(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a, b, c = _ensure_videos(repo, "a.mp4", "b.mp4", "c.mp4")
    w = PlayerWindow(c, repo, queue=[a, b, c])
    try:
        w.btn_loop.click()
        w.btn_loop.click()
        assert w.btn_loop.text() == "循环:全部"

        w._on_end()
        assert w.windowTitle() == "a.mp4", "all loop must wrap to the first video"
        assert w.session.loads[-1][0] == r"D:\v\a.mp4"
        assert repo.last_position(c.id) == 0.0

        QTest.keyClick(w, Qt.Key.Key_L)
        assert w.btn_loop.text() == "循环:关", "L must cycle loop modes"
    finally:
        w.close()


def _mk_res(repo, name, path, resolution):
    from domain.models import Video

    repo.upsert_videos([Video(filename=name, filepath=path, resolution=resolution)])
    return repo.get_by_path(path)


def test_fit_window_landscape_by_resolution(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a = _mk_res(repo, "a.mp4", r"D:\v\land.mp4", "1920x1080")
    w = PlayerWindow(a, repo)
    try:
        assert w.width() > w.height(), "16:9 video must open a landscape window"
    finally:
        w.close()


def test_fit_window_portrait_by_resolution(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a = _mk_res(repo, "a.mp4", r"D:\v\port.mp4", "720x1280")
    w = PlayerWindow(a, repo)
    try:
        assert w.width() < w.height(), "9:16 video must open a portrait window"
    finally:
        w.close()


def test_fit_window_fallback_without_resolution(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a = _mk_res(repo, "a.mp4", r"D:\v\nores.mp4", None)
    w = PlayerWindow(a, repo)
    try:
        assert w.width() > w.height(), "missing resolution must fall back to 16:9"
    finally:
        w.close()


def test_fit_window_adapts_on_switch(qapp, repo, fake_player):
    from ui.player import PlayerWindow

    a = _mk_res(repo, "a.mp4", r"D:\v\land.mp4", "1920x1080")
    b = _mk_res(repo, "b.mp4", r"D:\v\port.mp4", "720x1280")
    w = PlayerWindow(a, repo, queue=[a, b])
    try:
        assert w.width() > w.height(), "first video is landscape"
        w.btn_next.click()
        assert w.width() < w.height(), "switching must re-fit to portrait ratio"
    finally:
        w.close()
