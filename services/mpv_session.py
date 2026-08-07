"""mpv.exe 子进程会话:进程生命周期 + named pipe IPC + 事件分发。

架构(阶段 2 重构核心):
    PlayerWindow (ui/player.py) 只依赖本模块的接口, 不关心底层是
    QMediaPlayer 还是 mpv 进程。本模块封装:
      - MpvProcess: 启动/终止 mpv.exe 子进程
      - MpvSession (QObject): load/play/pause/seek/volume/rate + 信号
    UI 侧信号在 Qt 主线程, 底层读线程用 threading.Thread(不碰 QThread,
    规避「QThread 局部变量必须 parent」铁律), 信号跨线程自动队列投递。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import config

PIPE_PREFIX = r"\\.\pipe\videolib_mpv"


def _mpv_log_path() -> str:
    return os.path.join(os.environ.get("TEMP", "."), f"mpv_session_{os.getpid()}.log")


def _default_mpv_exe() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "vendor", "mpv", "mpv.exe")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "vendor", "mpv", "mpv.exe")


_WNDPROC = None
_WNDCLASS_REGISTERED = False


def _wndproc(hwnd, msg, wp, lp):
    user32 = ctypes.WinDLL("user32")
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_longlong
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


def _child_hwnd(parent_hwnd: int, left: int, top: int, width: int, height: int) -> int:
    """在 Qt 容器窗口内创建承载 mpv 渲染的子窗口, 返回 HWND。

    窗口过程必须是模块级函数且 WNDPROC 对象必须被模块级持有:
    cast 只拷贝裸指针, 引用一旦 GC, 窗口回调就是悬垂指针(access violation)。
    """
    global _WNDPROC, _WNDCLASS_REGISTERED
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")

    class WNDCLASS(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                    ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    cls = "VideoLibMpvChild"
    if not _WNDCLASS_REGISTERED:
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        _WNDPROC = WNDPROC(_wndproc)
        wc = WNDCLASS()
        wc.lpfnWndProc = ctypes.cast(_WNDPROC, ctypes.c_void_p)
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = cls
        user32.RegisterClassW.argtypes = [ctypes.c_void_p]
        user32.RegisterClassW.restype = wt.ATOM
        user32.RegisterClassW(ctypes.byref(wc))
        _WNDCLASS_REGISTERED = True
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wt.HWND, wt.HMENU,
                                       wt.HINSTANCE, wt.LPVOID]
    user32.CreateWindowExW.restype = wt.HWND
    hwnd = user32.CreateWindowExW(
        0, cls, "mpv", 0x40000000 | 0x10000000, left, top, width, height,
        parent_hwnd, None, kernel32.GetModuleHandleW(None), None)
    return int(hwnd or 0)


def _set_child_rect(hwnd: int, left: int, top: int, width: int, height: int) -> None:
    user32 = ctypes.WinDLL("user32")
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.SetWindowPos.restype = wt.BOOL
    user32.SetWindowPos(hwnd, None, left, top, width, height, 0x0004 | 0x0010)  # NOZORDER|NOACTIVATE


class MpvIpcClient:
    """named pipe 客户端: 命令/响应(request_id 关联) + 事件流。线程安全。"""

    def __init__(self, pipe_name: str):
        self.pipe_name = pipe_name
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                               wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
        self._kernel32.CreateFileW.restype = wt.HANDLE
        self._kernel32.WriteFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD,
                                             ctypes.POINTER(wt.DWORD), wt.LPVOID]
        self._kernel32.WriteFile.restype = wt.BOOL
        self._kernel32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD,
                                            ctypes.POINTER(wt.DWORD), wt.LPVOID]
        self._kernel32.ReadFile.restype = wt.BOOL
        self._kernel32.WaitNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD]
        self._kernel32.WaitNamedPipeW.restype = wt.BOOL
        self._kernel32.CloseHandle.argtypes = [wt.HANDLE]
        self._kernel32.CloseHandle.restype = wt.BOOL
        self._kernel32.PeekNamedPipe.argtypes = [
            wt.HANDLE, wt.LPVOID, wt.DWORD,
            ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
            ctypes.POINTER(wt.DWORD)]
        self._kernel32.PeekNamedPipe.restype = wt.BOOL
        self._handle = None
        self._lock = threading.Lock()
        self._pending_lines: list[str] = []
        self._read_buf = b""

    def connect(self, timeout_ms: int = 5000) -> "MpvIpcClient":
        """轮询等待 mpv 建好 named pipe。

        WaitNamedPipeW 在 pipe 不存在时立即失败(不等待), 而 mpv 冷启动
        (d3d11 init) 需要 1-3 秒, 因此必须循环重试直至超时。
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            if self._kernel32.WaitNamedPipeW(self.pipe_name, 250):
                self._handle = self._kernel32.CreateFileW(
                    self.pipe_name, 0xC0000000, 0, None, 3, 0x80, None)
                if self._handle != wt.HANDLE(-1).value:
                    break
            if time.monotonic() >= deadline:
                raise OSError(ctypes.get_last_error(), "CreateFileW failed")
            time.sleep(0.15)
        return self

    def send(self, command: list, request_id: int | None = None) -> None:
        obj: dict = {"command": command}
        if request_id is not None:
            obj["request_id"] = request_id
        data = json.dumps(obj).encode("utf-8") + b"\n"
        with self._lock:
            written = wt.DWORD(0)
            if not self._kernel32.WriteFile(self._handle, data, len(data),
                                            ctypes.byref(written), None):
                raise OSError(ctypes.get_last_error(), "WriteFile failed")

    def read(self) -> dict | None:
        """非阻塞读: 返回一条 JSON 消息, 无数据返回 None。

        同步 ReadFile 阻塞时, 同一句柄上另一线程的 WriteFile 会卡死,
        因此读侧必须永远不阻塞 —— 用 PeekNamedPipe 探测, 有数据才读。
        pipe 是字节流, 单次 Read 可能落在消息中间: 半行留在 _read_buf,
        与下次数据拼成完整行, 绝不丢弃(丢弃会永久错位, 事件丢失)。
        """
        while self._pending_lines:
            line = self._pending_lines.pop(0)
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        avail = wt.DWORD(0)
        if not self._kernel32.PeekNamedPipe(self._handle, None, 0, None,
                                            ctypes.byref(avail), None):
            return None
        if avail.value == 0:
            time.sleep(0.05)
            return None
        buf = ctypes.create_string_buffer(1 << 16)
        read = wt.DWORD(0)
        ok = self._kernel32.ReadFile(self._handle, buf, 1 << 16,
                                     ctypes.byref(read), None)
        if not ok or read.value == 0:
            return None
        self._read_buf += buf.raw[:read.value]
        lines = self._read_buf.split(b"\n")
        self._read_buf = lines.pop()  # 最后一段可能是不完整的半行, 保留
        decoded = []
        for raw in lines:
            text = raw.decode("utf-8", "replace").strip()
            if text:
                decoded.append(text)
        if not decoded:
            return None
        try:
            return json.loads(decoded[0])
        except json.JSONDecodeError:
            return None
        finally:
            self._pending_lines.extend(decoded[1:])

    def close(self) -> None:
        if self._handle and self._handle != wt.HANDLE(-1).value:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class MpvProcess:
    """mpv.exe 子进程生命周期。"""

    def __init__(self, child_hwnd: int, pipe_name: str, mpv_exe: str | None = None,
                 extra_args: tuple[str, ...] = ()):
        self.child_hwnd = child_hwnd
        self.pipe_name = pipe_name
        self.mpv_exe = mpv_exe or _default_mpv_exe()
        self.extra_args = extra_args
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        args = [
            self.mpv_exe,
            "--no-config",
            "--idle",
            "--keep-open=yes",
            "--vo=gpu",
            "--ao=wasapi",
            f"--wid={self.child_hwnd}",
            f"--input-ipc-server={self.pipe_name}",
            "--volume=100",
            f"--log-file={_mpv_log_path()}",
            *self.extra_args,
        ]
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def terminate(self, timeout_s: float = 3.0) -> None:
        if not self.proc:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class MpvSession(QObject):
    """mpv 进程会话: 面向播放器的薄接口。生命周期: 创建 -> load -> ... -> close。"""

    positionChanged = pyqtSignal(int)      # 毫秒
    durationChanged = pyqtSignal(int)      # 毫秒
    stateChanged = pyqtSignal(object)      # "playing" | "paused" | "stopped"
    mediaStatusChanged = pyqtSignal(object)  # "loaded" | "end" | "error"
    endOfMedia = pyqtSignal()

    def __init__(self, parent_widget, parent=None):
        super().__init__(parent)
        self._parent_widget = parent_widget
        self._process: MpvProcess | None = None
        self._ipc: MpvIpcClient | None = None
        self._reader: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._req_id = 0
        self._pid = os.getpid()
        self._child_hwnd = 0
        self._started = False
        self._position = 0
        self._duration = 0
        self._state = "stopped"
        self._last_emit = 0.0

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        # 子窗口由 VideoWidget 单一所有(创建/resize 同步/销毁);
        # 这里只取句柄, 保证 mpv 渲染窗口与容器同步。
        self._child_hwnd = self._parent_widget.ensure_child()
        if not self._child_hwnd:
            raise OSError("ensure_child failed: CreateWindowExW returned 0")
        self._pipe = f"{PIPE_PREFIX}_{self._pid}"
        self._process = MpvProcess(self._child_hwnd, self._pipe)
        self._process.start()
        self._ipc = MpvIpcClient(self._pipe)
        try:
            self._ipc.connect(timeout_ms=8000)
        except OSError as exc:
            exit_code = self._process.proc.poll() if self._process.proc else None
            self._process.terminate()
            self._process = None
            self._ipc = None
            raise OSError(
                f"{exc}; mpv exit={exit_code}; log: {_mpv_log_path()}"
            ) from exc
        self._reader = threading.Thread(target=self._read_loop, name="mpv-ipc", daemon=True)
        self._reader.start()

    def close(self) -> None:
        self._stop_flag.set()
        if self._ipc:
            try:
                self._ipc.send(["quit"])
            except OSError:
                pass
            self._ipc.close()
        if self._process:
            self._process.terminate()
        self._ipc = None
        self._process = None

    # ---------- control ----------

    def load(self, path: str, resume_sec: float = 0.0) -> None:
        cmd: list = ["loadfile", path, "replace"]
        if resume_sec > 0.0:
            # mpv 0.38+ 签名: loadfile <url> [flags [index [options]]];
            # options 必须用 -1 占 index 位, 且值必须是字符串。
            cmd += [-1, {"start": f"{round(resume_sec, 3)}"}]
        self._send(cmd)

    def play(self) -> None:
        self._send(["set", "pause", "no"])

    def pause(self) -> None:
        self._send(["set", "pause", "yes"])

    def stop(self) -> None:
        self._send(["set", "pause", "yes"])
        self._send(["seek", "0", "absolute"])
        self._position = 0
        self._state = "stopped"
        self.stateChanged.emit("stopped")

    def seek(self, ms: int) -> None:
        self._send(["seek", round(ms / 1000.0, 3), "absolute"])

    def set_volume(self, value: float) -> None:
        self._send(["set", "volume", round(value * 100, 1)])

    def set_mute(self, muted: bool) -> None:
        self._send(["set", "mute", "yes" if muted else "no"])

    def set_rate(self, rate: float) -> None:
        self._send(["set", "speed", rate])

    def screenshot(self, path: str) -> None:
        self._send(["screenshot-to-file", path, "video"])

    def position(self) -> int:
        return self._position

    def duration(self) -> int:
        return self._duration

    def state(self) -> str:
        return self._state

    def child_hwnd(self) -> int:
        return self._child_hwnd

    # ---------- internals ----------

    def _send(self, cmd: list) -> None:
        if not self._ipc:
            return
        self._req_id += 1
        self._ipc.send(cmd, request_id=self._req_id)

    def _read_loop(self) -> None:
        while not self._stop_flag.is_set():
            msg = self._ipc.read()
            if msg is None:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        event = msg.get("event")
        if event == "end-file":
            reason = msg.get("reason")
            if reason == "eof":
                self.endOfMedia.emit()
        elif event == "file-loaded":
            self._send(["observe_property", 1, "duration"])
            self._send(["observe_property", 2, "time-pos"])
            self._send(["observe_property", 3, "pause"])
            self.mediaStatusChanged.emit("loaded")
        elif event == "property-change":
            name = msg.get("name")
            if name == "duration":
                data = msg.get("data")
                if isinstance(data, (int, float)):
                    self._duration = int(data * 1000)
                    self.durationChanged.emit(self._duration)
            elif name == "time-pos":
                data = msg.get("data")
                if isinstance(data, (int, float)) and not isinstance(data, bool):
                    now = time.monotonic()
                    if now - self._last_emit >= 0.1:
                        self._last_emit = now
                        self._position = int(data * 1000)
                        self.positionChanged.emit(self._position)
            elif name == "pause":
                self._state = "paused" if msg.get("data") else "playing"
                self.stateChanged.emit(self._state)
        elif "error" in msg and msg.get("error") != "success":
            # 命令响应报错(如 loadfile 被拒): 不再静默, 让 UI 可见。
            self.mediaStatusChanged.emit("error")

    def _poll_state(self) -> None:
        pass
