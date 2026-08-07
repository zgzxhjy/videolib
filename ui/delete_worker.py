from PyQt6.QtCore import QThread, pyqtSignal

import config
from domain.repository import Repository
from services.backup import backup_db
from services.thumbnailer import Thumbnailer


class DeleteWorker(QThread):
    """Delete all data under a scan root in a background thread.

    Phases: snapshot the DB, delete video rows (+ categories/FTS), delete
    thumbnails one by one with progress, and only as the very last step
    forget the scan root. The root entry is the resumable marker: if the
    app dies or the user cancels mid-run, the root stays selectable and
    re-running the deletion cleans whatever remains (deletion is
    idempotent).
    """

    message = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)  # done, total, current filepath
    error = pyqtSignal(str)
    done = pyqtSignal(int, bool)  # deleted count, root_removed

    def __init__(self, root: str, repo: Repository, parent=None, thumbs_dir=None):
        super().__init__(parent)
        self._root = root
        self._repo = repo
        self._thumbs_dir = thumbs_dir or config.THUMBS_DIR
        self._cancel_flag = False

    def cancel(self) -> None:
        self._cancel_flag = True

    def run(self) -> None:
        deleted = 0
        root_removed = False
        try:
            self.message.emit(f"正在备份数据库... {self._root}")
            backup_db(self._repo, force=True)

            self.message.emit(f"正在删除 {self._root} 的视频记录...")
            ids = self._repo.remove_videos_under(self._root)
            deleted = len(ids)
            self.message.emit(f"已删除 {deleted} 条记录，正在清理缩略图...")

            thumbs = Thumbnailer(self._thumbs_dir)

            def on_progress(done: int, total: int) -> None:
                if self._cancel_flag:
                    return
                self.progress.emit(done, total, str(thumbs.path_for(ids[done - 1])))

            thumbs.delete_for(
                ids,
                progress=on_progress,
                should_cancel=lambda: self._cancel_flag,
            )

            if self._cancel_flag:
                self.message.emit(
                    f"删除已取消：历史记录保留，可稍后重新删除 {self._root}"
                )
            else:
                self._repo.remove_scan_root(self._root)
                root_removed = True
                self.message.emit(f"已删除 {self._root} 的全部数据")
        except Exception as e:
            self.error.emit(str(e))
            self.message.emit(f"删除出错: {e}")
        finally:
            self.done.emit(deleted, root_removed)
