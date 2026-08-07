import threading
import time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import config
from domain.repository import Repository
from services.library import Library


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "WatcherThread"):
        super().__init__()
        self._w = watcher

    def _is_video(self, path: str) -> bool:
        return Path(path).suffix.lower() in config.VIDEO_EXTENSIONS

    def on_created(self, event):
        if not event.is_directory and self._is_video(event.src_path):
            with self._w._lock:
                self._w._added[event.src_path] = time.time()

    def on_moved(self, event):
        if event.is_directory:
            return
        with self._w._lock:
            if self._is_video(event.dest_path):
                self._w._added[event.dest_path] = time.time()
            if self._is_video(event.src_path):
                self._w._removed.add(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and self._is_video(event.src_path):
            with self._w._lock:
                self._w._removed.add(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and self._is_video(event.src_path):
            with self._w._lock:
                self._w._changed.add(event.src_path)


class WatcherThread(QThread):
    """Watch one or more scan roots and apply incremental updates (2s debounce).

    A single watchdog observer can schedule several paths, so one thread
    watches all roots. Roots that do not exist (e.g. an unmounted drive) are
    skipped with a message instead of killing the thread.

    Thread safety: watchdog calls the handler on its own thread while the
    flush loop reads the same collections, so every mutation and every
    snapshot happens under `_lock`. Unhandled exceptions must never escape
    run(): PyQt6 aborts the whole process (silent 0xc0000409) when a Python
    exception propagates out of QThread.run.
    """

    message = pyqtSignal(str)
    changed = pyqtSignal()

    def __init__(
        self,
        roots: str | list[str],
        repo: Repository,
        parent=None,
        debounce: float = 2.0,
        poll: float = 0.5,
    ):
        super().__init__(parent)
        self._roots = [roots] if isinstance(roots, str) else list(roots)
        self._repo = repo
        self._debounce = debounce
        self._poll = poll
        self._added: dict[str, float] = {}
        self._removed: set[str] = set()
        self._changed: set[str] = set()
        self._lock = threading.Lock()
        self._stop_flag = False
        self._flush_ts = 0.0
        self.ready = threading.Event()

    def stop(self) -> None:
        self._stop_flag = True

    def run(self) -> None:
        handler = _Handler(self)
        observer = Observer()
        active = 0
        try:
            for root in self._roots:
                if not Path(root).is_dir():
                    self.message.emit(f"监控跳过（目录不存在）: {root}")
                    continue
                try:
                    observer.schedule(handler, root, recursive=True)
                    active += 1
                    self.message.emit(f"正在监控目录: {root}")
                except OSError as exc:
                    self.message.emit(f"监控失败 {root}: {exc}")
            if active == 0:
                self.ready.set()
                return
            observer.start()
            self.ready.set()
            while not self._stop_flag:
                now = time.time()
                if self._added or self._removed or self._changed:
                    if now - self._flush_ts >= self._debounce:
                        try:
                            self._flush()
                        except Exception as exc:
                            self.message.emit(f"增量同步出错: {exc}")
                        self._flush_ts = now
                time.sleep(self._poll)
        except Exception as exc:
            self.message.emit(f"监控线程异常: {exc}")
        finally:
            observer.stop()
            observer.join()

    def _flush(self) -> None:
        with self._lock:
            removed = list(self._removed)
            added_keys = list(self._added)
            changed_keys = list(self._changed)
            self._removed.clear()
            self._added.clear()
            self._changed.clear()
        library = Library(self._repo)
        added = [p for p in added_keys if Path(p).exists()]
        changed = [p for p in changed_keys if Path(p).exists()]
        library.apply_sync(added + changed, removed)
        self.changed.emit()
