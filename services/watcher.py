import time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import config
from domain.repository import Repository
from services.metadata import build_video
from services.scanner import scan_directory


class _Handler(FileSystemEventHandler):
    def __init__(self, added: dict, removed: set, changed: set):
        super().__init__()
        self._added = added
        self._removed = removed
        self._changed = changed

    def _is_video(self, path: str) -> bool:
        return Path(path).suffix.lower() in config.VIDEO_EXTENSIONS

    def on_created(self, event):
        if not event.is_directory and self._is_video(event.src_path):
            self._added[event.src_path] = time.time()

    def on_moved(self, event):
        if event.is_directory:
            return
        if self._is_video(event.dest_path):
            self._added[event.dest_path] = time.time()
        if self._is_video(event.src_path):
            self._removed.add(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and self._is_video(event.src_path):
            self._removed.add(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and self._is_video(event.src_path):
            self._changed.add(event.src_path)


class WatcherThread(QThread):
    """Watch the scanned root and apply incremental updates (2s debounce)."""

    message = pyqtSignal(str)
    changed = pyqtSignal()

    def __init__(self, root: str, repo: Repository, parent=None):
        super().__init__(parent)
        self._root = root
        self._repo = repo
        self._added: dict[str, float] = {}
        self._removed: set[str] = set()
        self._changed: set[str] = set()
        self._stop_flag = False
        self._flush_ts = 0.0

    def stop(self) -> None:
        self._stop_flag = True

    def run(self) -> None:
        handler = _Handler(self._added, self._removed, self._changed)
        observer = Observer()
        observer.schedule(handler, self._root, recursive=True)
        observer.start()
        self.message.emit(f"正在监控目录: {self._root}")
        try:
            while not self._stop_flag:
                now = time.time()
                if self._added or self._removed or self._changed:
                    if now - self._flush_ts >= 2.0:
                        self._flush()
                        self._flush_ts = now
                time.sleep(0.5)
        finally:
            observer.stop()
            observer.join()

    def _flush(self) -> None:
        if self._removed:
            self._repo.remove_by_filepaths(list(self._removed))
            self._removed.clear()
        added = [p for p in self._added if Path(p).exists()]
        self._added.clear()
        if added:
            self._repo.upsert_videos([build_video(p) for p in added])
        if self._changed:
            for p in self._changed:
                if Path(p).exists():
                    self._repo.upsert_videos([build_video(p)])
            self._changed.clear()
        self.changed.emit()
