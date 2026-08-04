from PyQt6.QtCore import QThread, pyqtSignal

from domain.repository import Repository
from services.metadata import build_video
from services.scanner import scan_directory


class ScanWorker(QThread):
    """Full scan of a directory in a background thread."""

    message = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)  # done, total, current filepath
    done = pyqtSignal(bool)  # True = completed, False = canceled/error

    def __init__(self, root: str, repo: Repository, parent=None):
        super().__init__(parent)
        self._root = root
        self._repo = repo
        self._cancel_flag = False

    def cancel(self) -> None:
        self._cancel_flag = True

    def run(self) -> None:
        completed = False
        try:
            self.message.emit(f"正在枚举视频文件... {self._root}")
            files = scan_directory(self._root)
            self.message.emit(f"发现 {len(files)} 个视频文件，正在提取元数据...")
            videos = []
            canceled = False
            for i, fp in enumerate(files, 1):
                if self._cancel_flag:
                    canceled = True
                    break
                videos.append(build_video(fp))
                self.progress.emit(i, len(files), fp)
            self._repo.upsert_videos(videos)
            if canceled:
                self.message.emit(f"扫描已取消，保留已处理的 {len(videos)} 个视频")
            else:
                known = self._repo.all_filepaths()
                stale = [p for p in known if p not in set(files)]
                if stale:
                    self._repo.remove_by_filepaths(stale)
                self.message.emit(f"索引完成，库中共 {self._repo.count()} 个视频")
                completed = True
        except Exception as e:
            self.message.emit(f"扫描出错: {e}")
        finally:
            self.done.emit(completed)
