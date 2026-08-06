"""mpv IPC 客户端:named pipe 连接 + JSON 命令 + 响应解析(纯 ctypes)。"""
import ctypes
import ctypes.wintypes as wt
import json
import os

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
PIPE_READMODE_MESSAGE = 0x2

ERROR_PIPE_BUSY = 231
ERROR_PIPE_NOT_CONNECTED = 233
ERROR_NO_DATA = 232

kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.WriteFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
kernel32.WriteFile.restype = wt.BOOL
kernel32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
kernel32.ReadFile.restype = wt.BOOL
kernel32.WaitNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD]
kernel32.WaitNamedPipeW.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.GetLastError.restype = wt.DWORD

INVALID_HANDLE_VALUE = wt.HANDLE(-1).value


class MpvIpc:
    def __init__(self, pipe_name):
        self.pipe_name = pipe_name
        self.handle = None

    def connect(self, timeout_ms=5000):
        kernel32.WaitNamedPipeW(self.pipe_name, timeout_ms)
        self.handle = kernel32.CreateFileW(
            self.pipe_name, GENERIC_READ | GENERIC_WRITE, 0, None,
            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if self.handle == INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed")
        return self

    def send(self, command, request_id=None):
        obj = {"command": command}
        if request_id is not None:
            obj["request_id"] = request_id
        data = json.dumps(obj).encode("utf-8") + b"\n"
        written = wt.DWORD(0)
        ok = kernel32.WriteFile(self.handle, data, len(data), ctypes.byref(written), None)
        if not ok:
            raise OSError(ctypes.get_last_error(), "WriteFile failed")

    def read(self, timeout_ms=3000):
        """读一个响应。阻塞至有新数据。返回解析后的 JSON 或 None。"""
        buf = ctypes.create_string_buffer(65536)
        read = wt.DWORD(0)
        ok = kernel32.ReadFile(self.handle, buf, 65536, ctypes.byref(read), None)
        if not ok or read.value == 0:
            return None
        text = buf.raw[:read.value].decode("utf-8", "replace").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def close(self):
        if self.handle and self.handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self.handle)
            self.handle = None


if __name__ == "__main__":
    pipe = r"\\.\pipe\vl_mpv_test"
    ipc = MpvIpc(pipe)
    ipc.connect()
    ipc.send(["get_property", "time-pos"])
    print("resp:", ipc.read())
    ipc.close()
