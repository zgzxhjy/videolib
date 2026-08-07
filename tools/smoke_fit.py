"""自适应冒烟: 竖屏/横屏视频窗口比例 + child hwnd 跟随 resize + 切换自适应。"""
import ctypes
import ctypes.wintypes as wt
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QApplication

from domain.models import Video
from domain.repository import Repository
from ui.player import PlayerWindow

LAND = r"E:\图片\26.7上海\VID_20260718_195617.mp4"
db = os.path.join(os.path.expanduser("~"), ".videolib", "videolib.db")
con = sqlite3.connect(db)
port_path = None
for fp, res in con.execute("select filepath, resolution from videos"):
    if fp and res and os.path.exists(fp) and int(res.split("x")[0]) < int(res.split("x")[1]):
        port_path = fp
        break
con.close()
assert port_path, "no portrait video found"
print(f"land={LAND}\nport={port_path}", flush=True)

app = QApplication([])
repo = Repository(Path(tempfile.mkdtemp()) / "fit.db")

def mk(name, fp, resolution):
    repo.upsert_videos([Video(filename=name, filepath=fp, resolution=resolution)])
    v = repo.get_by_path(fp)
    assert v is not None
    return v

a = mk("land.mp4", LAND, "1920x1080")
b = mk("port.mp4", port_path, "720x1280")
print(f"a: id={a.id} res={a.resolution!r} path={a.filepath!r}", flush=True)
print(f"b: id={b.id} res={b.resolution!r} path={b.filepath!r}", flush=True)
print("constructing PlayerWindow...", flush=True)
try:
    w = PlayerWindow(a, repo, queue=[a, b])
    print("PlayerWindow constructed", flush=True)
except Exception as e:
    print(f"CONSTRUCT FAILED: {type(e).__name__}: {e}", flush=True)
    sys.exit(2)
w.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
w.show()

log = []

def ep(exc_type, exc, tb):
    print("".join(__import__("traceback").format_exception(exc_type, exc, tb)), flush=True)
sys.excepthook = ep

def rect(hwnd):
    r = wt.RECT()
    ctypes.WinDLL("user32").GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top

def check_land():
    ww, wh = w.width(), w.height()
    child = w.video_widget.child_hwnd()
    cw, ch = rect(child)
    print(f"land: window={ww}x{wh} child={cw}x{ch} widget={w.video_widget.width()}x{w.video_widget.height()}", flush=True)
    assert ww > wh, "landscape video must open landscape window"
    assert cw == w.video_widget.width() and ch == w.video_widget.height(), \
        "child hwnd must match video widget size"
    w.resize(ww + 200, wh + 100)
    QTimer.singleShot(400, check_child_follows)

def check_child_follows():
    cw, ch = rect(w.video_widget.child_hwnd())
    print(f"after resize: widget={w.video_widget.width()}x{w.video_widget.height()} child={cw}x{ch}", flush=True)
    assert cw == w.video_widget.width() and ch == w.video_widget.height(), \
        "child hwnd must follow window resize"
    w.btn_next.click()
    QTimer.singleShot(400, check_port)

def check_port():
    ww, wh = w.width(), w.height()
    print(f"portrait: window={ww}x{wh}", flush=True)
    assert wh > ww, "switching to portrait video must adapt window to portrait"
    print("PASS: fit + child-follow ok", flush=True)
    w.close()
    app.quit()

QTimer.singleShot(800, check_land)
QTimer.singleShot(15000, lambda: (print("FAIL: timeout", flush=True), app.quit()))
app.exec()
repo.close()
