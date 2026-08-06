"""Startup sweep for stale PyInstaller onefile extraction dirs (_MEI*).

The bootloader normally deletes its temp dir on clean exit, but crashes,
force-kills and locked files leave `_MEI*` dirs behind (PyInstaller known
issue).  We sweep them at startup: while a concurrent instance is running,
its extraction dir is protected (recent mtime + locked files), otherwise all
leftovers from earlier runs are removed.
"""

import ctypes
import os
import shutil
import sys
import time

_STALE_AGE_S = 30 * 60


def cleanup_stale_mei() -> int:
    """Delete leftover `_MEI*` extraction dirs. No-op when running from source."""
    mei_pass = getattr(sys, "_MEIPASS", None)
    if not mei_pass:
        return 0
    parent = os.path.dirname(mei_pass)
    current = os.path.basename(mei_pass)
    now = time.time()
    other_running = _other_instances_running()
    removed = 0
    try:
        names = os.listdir(parent)
    except OSError:
        return 0
    for name in names:
        if not name.startswith("_MEI") or name == current:
            continue
        path = os.path.join(parent, name)
        try:
            if other_running and now - os.path.getmtime(path) < _STALE_AGE_S:
                continue
        except OSError:
            continue
        try:
            shutil.rmtree(path)
            removed += 1
        except OSError:
            continue
    return removed


def _other_instances_running() -> bool:
    """True when another process with our executable name exists on Windows."""
    if os.name != "nt":
        return False
    self_name = os.path.basename(sys.executable).lower()

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snapshot:
        return True
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == self_name:
                return True
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return False
    finally:
        kernel32.CloseHandle(snapshot)
