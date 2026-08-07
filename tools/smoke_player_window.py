"""PlayerWindow 真实集成冒烟: 真实 mpv.exe + 临时库, 验证播放/暂停/断点恢复闭环。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QApplication

from domain.models import Video
from domain.repository import Repository
from services.mpv_session import MpvSession
from ui.player import PlayerWindow

VIDEO = r"E:\图片\26.7上海\VID_20260718_195617.mp4"
assert Path(VIDEO).exists(), f"video missing: {VIDEO}"

_orig_load = MpvSession.load
loads: list[tuple[str, float]] = []

def _spy_load(self, path, resume_sec=0.0):
    loads.append((path, resume_sec))
    _orig_load(self, path, resume_sec)

MpvSession.load = _spy_load

app = QApplication([])
repo = Repository(Path(tempfile.mkdtemp()) / "smoke.db")
repo.upsert_videos([Video(filename="VID_20260718_195617.mp4", filepath=VIDEO)])
video = repo.get_by_path(VIDEO)
assert video is not None

log = []

def step1_open_and_play():
    log.append("step1: open player")
    win = PlayerWindow(video, repo)
    win.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
    win.show()
    return win

win = step1_open_and_play()
state = {"loaded": False, "pos_ms": 0, "closed": False}

def on_loaded():
    state["loaded"] = True
    log.append("loaded, playing")

def on_pos(ms):
    state["pos_ms"] = ms

win.session.mediaStatusChanged.connect(lambda s: on_loaded() if s == "loaded" else None)
win.session.positionChanged.connect(on_pos)

def step2_close_after_5s():
    log.append(f"step2: closing at pos={state['pos_ms']}ms")
    win.close()

def step3_reopen():
    log.append("step3: reopen to check resume")
    resume = repo.last_position(video.id)
    log.append(f"resume recorded: {resume}")
    assert resume >= 4.0, f"resume too small: {resume}"
    win2 = PlayerWindow(video, repo)
    win2.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
    win2.show()

    def check_resume():
        path, resume_sec = loads[-1]
        log.append(f"step4: reopened load resume_sec={resume_sec}")
        assert resume_sec >= 4.0, f"reopen did not carry resume: {resume_sec}"
        win2.close()
        log.append("PASS: resume loop ok")
        print("\n".join(log), flush=True)
        app.quit()

    QTimer.singleShot(3000, check_resume)

QTimer.singleShot(6000, step2_close_after_5s)
QTimer.singleShot(9000, step3_reopen)
QTimer.singleShot(20000, lambda: (print("FAIL: timeout", flush=True), app.quit()))
app.exec()
repo.close()
