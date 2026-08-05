from PyQt6.QtCore import QThread, pyqtSignal

from domain.repository import Repository
from services.library import Library
from services.scanner import diff_scan, scan_directory


class ScanWorker(QThread):
    """Full scan of a directory in a background thread.

    Incremental: files already indexed with unchanged size/mtime are skipped,
    stale entries are only removed within the scanned root. The probing and
    stale-cleanup algorithm lives in Library; this thread only enumerates,
    forwards progress and turns the result into status messages.
    """

    message = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)  # done, total, current filepath
    done = pyqtSignal(str)  # "ok" | "empty" | "cancel"

    def __init__(self, root: str, repo: Repository, parent=None):
        super().__init__(parent)
        self._root = root
        self._repo = repo
        self._cancel_flag = False

    def cancel(self) -> None:
        self._cancel_flag = True

    def run(self) -> None:
        status = "cancel"
        try:
            self.message.emit(f"正在枚举视频文件... {self._root}")
            files = scan_directory(self._root)
            if not files:
                self.message.emit(f"{self._root} 下没有视频文件")
                status = "empty"
                return
            self.message.emit(f"发现 {len(files)} 个视频文件，正在比对变化...")
            need_probe, stale = diff_scan(files, self._repo.existing_under(self._root))
            skipped = len(files) - len(need_probe)
            self.message.emit(f"{len(need_probe)} 个文件需要更新元数据（跳过 {skipped} 个未变化）")

            result = Library(self._repo).apply_sync(
                need_probe,
                stale,
                progress=lambda done, total, fp: self.progress.emit(done, total, fp),
                should_cancel=lambda: self._cancel_flag,
            )
            if result.canceled:
                self.message.emit(f"扫描已取消，保留已处理的 {result.probed} 个视频")
            else:
                self._repo.register_scan(self._root)
                self.message.emit(
                    f"索引完成，库中共 {self._repo.count()} 个视频（清理 {result.removed} 个失效记录）"
                )
                status = "ok"
        except Exception as e:
            self.message.emit(f"扫描出错: {e}")
        finally:
            self.done.emit(status)
