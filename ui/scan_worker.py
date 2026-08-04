from PyQt6.QtCore import QThread, pyqtSignal

from domain.repository import Repository
from services.metadata import build_video
from services.scanner import scan_directory


class ScanWorker(QThread):
    """Full scan of a directory in a background thread."""

    message = pyqtSignal(str)
    done = pyqtSignal()

    def __init__(self, root: str, repo: Repository, parent=None):
        super().__init__(parent)
        self._root = root
        self._repo = repo

    def run(self) -> None:
        try:
            files = scan_directory(self._root)
            self.message.emit(f"发现 {len(files)} 个视频文件，正在提取元数据...")
            videos = []
            for i, fp in enumerate(files, 1):
                videos.append(build_video(fp))
                if i % 100 == 0:
                    self.message.emit(f"已处理 {i}/{len(files)}")
            self._repo.upsert_videos(videos)
            known = self._repo.all_filepaths()
            stale = [p for p in known if p not in set(files)]
            if stale:
                self._repo.remove_by_filepaths(stale)
            self.message.emit(f"索引完成，库中共 {self._repo.count()} 个视频")
        except Exception as e:
            self.message.emit(f"扫描出错: {e}")
        finally:
            self.done.emit()
