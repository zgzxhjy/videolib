"""线性冒烟: 真实 mpv.exe + 真实 child hwnd + IPC 全链路, 无 Qt 事件循环。"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication

from services.mpv_session import MpvIpcClient, MpvProcess, _child_hwnd
from ui.video_widget import VideoWidget

VIDEO = Path(r"E:\图片\26.7上海\VID_20260718_195617.mp4").as_posix()
SHOT = Path(r"C:\Users\Administrator\AppData\Local\Temp\opencode\smoke_linear.png")
PIPE = r"\\.\pipe\videolib_smoke_linear"

app = QApplication([])
win = VideoWidget()
win.resize(960, 540)
win.show()
print("parent winId:", hex(int(win.winId())), flush=True)

child = _child_hwnd(int(win.winId()), 0, 0, 960, 540)
print("child hwnd:", hex(child), flush=True)
assert child, "child hwnd failed"

proc = MpvProcess(child, PIPE)
proc.start()
print("mpv pid:", proc.proc.pid if proc.proc else None, flush=True)

ipc = MpvIpcClient(PIPE)
t0 = time.time()
ipc.connect(timeout_ms=8000)
print(f"connect ok in {time.time() - t0:.1f}s", flush=True)

ipc.send(["loadfile", VIDEO, "replace"])
time.sleep(5)
ipc.send(["set", "pause", "yes"])
ipc.send(["seek", "3", "absolute"])
time.sleep(2)
ipc.send(["screenshot-to-file", str(SHOT), "video"])
time.sleep(2)

print("shot exists:", SHOT.exists(), "size:", SHOT.stat().st_size if SHOT.exists() else 0, flush=True)

proc.terminate()
print("done", flush=True)
