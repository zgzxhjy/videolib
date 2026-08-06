"""最小:Qt top-level + 自建 child + mpv --wid,不加载不控制,3 秒后退出。"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import faulthandler

faulthandler.enable()

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import QTimer

MPV = r"C:\Users\Administrator\AppData\Local\Temp\opencode\mpvbin\mpv.exe"
VIDEO = sys.argv[1]

user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong


def wndproc(hwnd, msg, wp, lp):
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


CLS = "MPVMIN"
wc = WNDCLASS()
wc.lpfnWndProc = ctypes.cast(WNDPROC(wndproc), ctypes.c_void_p)
wc.hInstance = kernel32.GetModuleHandleW(None)
wc.lpszClassName = CLS
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wt.ATOM
assert user32.RegisterClassW(ctypes.byref(wc))
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND

app = QApplication([])
w = QWidget()
w.resize(640, 360)
w.show()
print("qt widget shown, winId:", hex(int(w.winId())), flush=True)


def launch():
    parent = int(w.winId())
    child = user32.CreateWindowExW(0, CLS, "c", 0x40000000 | 0x10000000, 0, 0, 640, 360, parent, None, wc.hInstance, None)
    print("child:", hex(child or 0), flush=True)
    proc = subprocess.Popen([
        MPV, "--no-config", "--vo=gpu", "--ao=null", "--hwdec=no",
        f"--wid={child}",
        "--log-file=" + r"C:\Users\Administrator\AppData\Local\Temp\opencode\qt_min_mpv.log",
        "--keep-open=yes",
        "--input-ipc-server=" + r"\\.\pipe\vl_mpv_test",
        VIDEO,
    ])
    print("popen ok pid:", proc.pid, flush=True)


def step_ipc():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mpv_ipc import MpvIpc
    try:
        ipc[0] = MpvIpc(r"\\.\pipe\vl_mpv_test").connect(timeout_ms=2000)
        print("ipc connected", flush=True)
        ipc[0].send(["loadfile", VIDEO])
        print("loadfile sent", flush=True)
        QTimer.singleShot(2000, step_shot)
    except OSError as e:
        print("ipc retry:", e, flush=True)
        QTimer.singleShot(300, step_ipc)


ipc = [None]


def step_shot():
    shot_path = r"C:\Users\Administrator\AppData\Local\Temp\opencode\qt_min_shot.ppm"
    if os.path.exists(shot_path):
        os.remove(shot_path)
    ipc[0].send(["screenshot-to-file", shot_path, "video"])
    print("screenshot sent", flush=True)
    QTimer.singleShot(2000, fin)


def fin():
    print("4s ok, quitting", flush=True)
    app.quit()


QTimer.singleShot(100, launch)
QTimer.singleShot(400, step_ipc)
app.exec()
print("app.exec returned", flush=True)
