"""MpvSession 命令格式与 IPC 读取健壮性测试(不启动真实 mpv 进程)。"""
import ctypes
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from services.mpv_session import MpvIpcClient, MpvSession


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _session(qapp):
    from PyQt6.QtWidgets import QWidget

    return MpvSession(QWidget())


def test_load_without_resume_keeps_old_format(qapp):
    s = _session(qapp)
    calls = []
    s._send = lambda cmd: calls.append(cmd)
    s.load(r"D:\v\a.mp4")
    assert calls == [["loadfile", r"D:\v\a.mp4", "replace"]]


def test_load_with_resume_uses_index_placeholder_and_string_value(qapp):
    s = _session(qapp)
    calls = []
    s._send = lambda cmd: calls.append(cmd)
    s.load(r"D:\v\a.mp4", resume_sec=5.032)
    cmd = calls[0]
    assert cmd[0] == "loadfile" and cmd[1] == r"D:\v\a.mp4" and cmd[2] == "replace"
    # mpv 0.38+: options 必须用 -1 占 index 位, 值必须是字符串
    assert cmd[3] == -1
    assert isinstance(cmd[4], dict) and cmd[4]["start"] == "5.032"


class _FakePipe:
    """假 kernel32: 按段吐出预置字节, 模拟 pipe 缓冲拆分消息。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._pending = b""

    @staticmethod
    def _set(arg, value):
        ctypes.cast(arg, ctypes.POINTER(ctypes.c_ulong))[0] = value

    def PeekNamedPipe(self, handle, buf, size, read, avail, total):
        n = len(self._pending) + (len(self._chunks[0]) if self._chunks else 0)
        self._set(avail, n)
        return True

    def ReadFile(self, handle, buf, size, read, _):
        if self._chunks:
            self._pending += self._chunks.pop(0)
        n = min(size, len(self._pending))
        dst = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        for i in range(n):
            dst[i] = self._pending[i]
        self._pending = self._pending[n:]
        self._set(read, n)
        return True


def _pipe_client(chunks):
    c = MpvIpcClient(r"\\.\pipe\unused")
    c._kernel32 = _FakePipe(chunks)
    return c


def test_read_reassembles_message_split_across_pipe_reads():
    # 消息在 \n 中间被切两段 —— 半行必须拼接而不是丢弃
    full = b'{"request_id":1,"error":"success"}\n{"event":"file-loaded"}\n'
    c = _pipe_client([full[:20], full[20:]])
    assert c.read() is None, "first read only got half a line, must hold it back"
    assert c.read() == {"request_id": 1, "error": "success"}
    assert c.read() == {"event": "file-loaded"}, "half line must be reassembled"
    assert c.read() is None


def test_read_keeps_multiple_messages_in_order():
    blob = (b'{"event":"file-loaded"}\n{"event":"end-file","reason":"eof"}\n'
            b'{"event":"property-change","name":"pause","data":false}\n')
    c = _pipe_client([blob])
    assert c.read() == {"event": "file-loaded"}
    assert c.read() == {"event": "end-file", "reason": "eof"}
    assert c.read() == {"event": "property-change", "name": "pause", "data": False}
    assert c.read() is None


def test_dispatch_emits_error_on_command_response(qapp):
    s = _session(qapp)
    statuses = []
    s.mediaStatusChanged.connect(lambda st: statuses.append(st))
    s._dispatch({"request_id": 7, "error": "invalid parameter"})
    assert statuses == ["error"]
    s._dispatch({"request_id": 8, "error": "success"})
    assert statuses == ["error"], "success response must stay silent"
