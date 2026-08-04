from PyQt6.QtCore import QThread, pyqtSignal

from domain.repository import Repository
from services.metadata import build_video
from services.scanner import diff_scan, scan_directory


class ScanWorker(QThread):
    """Full scan of a directory in a background thread.

    Incremental: files already indexed with unchanged size/mtime are skipped,
    stale entries are only removed within the scanned root.
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

            videos = []
            canceled = False
            for i, fp in enumerate(need_probe, 1):
                if self._cancel_flag:
                    canceled = True
                    break
                videos.append(build_video(fp))
                self.progress.emit(i, len(need_probe), fp)
            self._repo.upsert_videos(videos)
            if canceled:
                self.message.emit(f"扫描已取消，保留已处理的 {len(videos)} 个视频")
            else:
                if stale:
                    self._repo.remove_by_filepaths(stale)
                self._repo.register_scan(self._root)
                self.message.emit(
                    f"索引完成，库中共 {self._repo.count()} 个视频（清理 {len(stale)} 个失效记录）"
                )
                status = "ok"
        except Exception as e:
            self.message.emit(f"扫描出错: {e}")
        finally:
            self.done.emit(status)
