"""断点恢复冒烟: 真实 mpv + resume_sec>0, 验证 loadfile 新签名生效(file-loaded 到达且播放推进)。"""
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
repo = Repository(Path(tempfile.mkdtemp()) / "smoke_resume.db")
repo.upsert_videos([Video(filename="vid.mp4", filepath=VIDEO, resolution="1920x1080")])
video = repo.get_by_path(VIDEO)
assert video is not None

state = {"loaded": False, "pos_ms": 0, "errors": 0}

win = PlayerWindow(video, repo)
win.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
win.session.mediaStatusChanged.connect(
    lambda s: (print(f"status: {s}", flush=True),
               state.__setitem__("errors", state["errors"] + 1) if s == "error" else None,
               state.__setitem__("loaded", True) if s == "loaded" else None)
)
win.session.positionChanged.connect(lambda ms: state.__setitem__("pos_ms", ms))
win.show()

# 播放 4s 建立断点, 关闭, 再带 resume 重开
def step1_close():
    print(f"closing at pos={state['pos_ms']}ms", flush=True)
    win.close()

def step2_reopen():
    resume = repo.last_position(video.id)
    print(f"resume recorded: {resume}", flush=True)
    assert resume >= 3.0, f"resume too small: {resume}"
    # 直接验证 loadfile 带 options 的签名(绕开 PlayerWindow 5s 阈值, 该逻辑已有单测)
    from ui.video_widget import VideoWidget

    s = MpvSession(VideoWidget())
    st = {"loaded": False, "pos": 0, "errors": 0}
    s.mediaStatusChanged.connect(
        lambda stt: (st.__setitem__("errors", st["errors"] + 1) if stt == "error" else None,
                     st.__setitem__("loaded", True) if stt == "loaded" else None))
    s.positionChanged.connect(lambda ms: st.__setitem__("pos", ms))
    s.start()
    s.load(VIDEO, resume_sec=resume)

    def check():
        print(f"resumed session: loaded={st['loaded']} errors={st['errors']} pos={st['pos']}ms", flush=True)
        assert st["loaded"], "file must load with resume"
        assert st["errors"] == 0, "loadfile must not error"
        assert st["pos"] >= resume * 1000 - 800, \
            f"must resume near {resume}s, got {st['pos']}ms"
        print("PASS: resume with new loadfile signature", flush=True)
        s.close()
        app.quit()

    QTimer.singleShot(4000, check)

QTimer.singleShot(5000, step1_close)
QTimer.singleShot(7000, step2_reopen)
QTimer.singleShot(20000, lambda: (print("FAIL: timeout", flush=True), app.quit()))
app.exec()
repo.close()
