"""冒烟: 真实 mpv.exe 走 MpvSession 全链路(load/play/seek/rate/volume/截图)。"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from services.mpv_session import MpvSession
from ui.video_widget import VideoWidget

video_path = Path(r"E:\图片\26.7上海\VID_20260718_195617.mp4").as_posix()
if not Path(video_path).exists():
    print("video not found:", video_path)
    sys.exit(1)
out_dir = Path(r"C:\Users\Administrator\AppData\Local\Temp\opencode\smoke")
out_dir.mkdir(parents=True, exist_ok=True)

app = QApplication([])
win = VideoWidget()
win.resize(960, 540)
win.show()

session = MpvSession(win, parent=None)
session.start()

state = {"loaded": False, "seeks": 0, "errors": []}

def on_loaded():
    state["loaded"] = True
    session.play()

def on_pos(ms):
    if 2000 <= ms <= 6000 and state["seeks"] == 0:
        state["seeks"] = 1
        session.seek(4000)
        session.set_rate(2.0)
    if ms >= 8000 and state["seeks"] == 1:
        state["seeks"] = 2
        session.screenshot(str(out_dir / "shot.png"))

def on_status(s):
    if s == "error":
        state["errors"].append("error status")

def on_state(s):
    print(f"[state] {s}  pos={session.position()}ms dur={session.duration()}ms")

session.mediaStatusChanged.connect(on_status)
session.mediaStatusChanged.connect(lambda s: print(f"[status] {s}") or (on_loaded() if s == "loaded" else None))
session.positionChanged.connect(on_pos)

def finish():
    print(f"RESULT loaded={state['loaded']} errors={state['errors']}")
    shot = out_dir / "shot.png"
    print(f"shot exists={shot.exists()} size={shot.stat().st_size if shot.exists() else 0}")
    session.close()
    win.close()
    app.quit()

QTimer.singleShot(20000, finish)
session.load(video_path)
sys.exit(app.exec())
