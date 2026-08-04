import threading
from pathlib import Path

import av

import config


def _extract_frame(filepath: str, thumb_path: Path, target_width: int = 320) -> bool:
    """Seek to ~10% of the video, decode one frame, save as JPEG."""
    try:
        with av.open(filepath) as container:
            stream = next(
                (s for s in container.streams if s.type == "video"), None
            )
            if stream is None:
                return False
            stream.thread_type = "AUTO"
            if stream.duration:
                seek_pts = int(stream.duration * 0.1)
                container.seek(seek_pts, backward=True, any_frame=False, stream=stream)
            frame = next(container.decode(video=0))
            img = frame.to_image()
            ratio = target_width / img.width
            img = img.resize((target_width, max(1, int(img.height * ratio))))
            img.save(thumb_path, "JPEG", quality=80)
            return True
    except Exception:
        return False


class Thumbnailer:
    """Lazy thumbnail generation with a bounded worker thread."""

    def __init__(self, thumbs_dir: str | Path | None = None):
        self._dir = Path(thumbs_dir or config.THUMBS_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._pending: set[str] = set()

    def path_for(self, video_id: int) -> Path:
        return self._dir / f"{video_id}.jpg"

    def exists(self, video_id: int) -> bool:
        return self.path_for(video_id).exists()

    def ensure(self, filepath: str, video_id: int, repo=None) -> bool:
        """Generate thumb if missing. When repo is given, persist thumb_path."""
        thumb = self.path_for(video_id)
        if thumb.exists():
            return True
        with self._lock:
            if filepath in self._pending:
                return False
            self._pending.add(filepath)
        try:
            ok = _extract_frame(filepath, thumb)
            if ok and repo is not None:
                repo.set_thumb(video_id, str(thumb))
            return ok
        finally:
            with self._lock:
                self._pending.discard(filepath)
