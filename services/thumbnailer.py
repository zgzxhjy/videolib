import threading
from pathlib import Path

import av

import config

THUMB_HEIGHT = 64


def _log_failure(filepath: str, exc: Exception) -> None:
    try:
        config.APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.APP_DIR / "thumbnails.log", "a", encoding="utf-8") as f:
            f.write(f"{filepath}: {type(exc).__name__}: {exc}\n")
    except OSError:
        pass


def _extract_frame(filepath: str, thumb_path: Path) -> bool:
    """Seek to ~10% of the video, scale one frame, save as JPEG (pure PyAV)."""
    try:
        with av.open(filepath) as container:
            stream = next(
                (s for s in container.streams if s.type == "video"), None
            )
            if stream is None:
                raise ValueError("no video stream")
            stream.thread_type = "AUTO"
            if stream.duration:
                seek_pts = int(stream.duration * 0.1)
                container.seek(seek_pts, backward=True, any_frame=False, stream=stream)
            frame = next(container.decode(video=0))
            ratio = THUMB_HEIGHT / frame.height
            scaled = frame.reformat(
                width=max(1, int(frame.width * ratio)),
                height=THUMB_HEIGHT,
                format="yuv420p",
            )
            with av.open(str(thumb_path), "w") as out:
                jpeg = out.add_stream("mjpeg")
                jpeg.width = scaled.width
                jpeg.height = scaled.height
                jpeg.pix_fmt = "yuvj420p"
                for packet in jpeg.encode(scaled):
                    out.mux(packet)
                for packet in jpeg.encode():
                    out.mux(packet)
            return True
    except Exception as exc:
        _log_failure(filepath, exc)
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

    @staticmethod
    def cleanup_orphans(thumbs_dir: str | Path, valid_ids: set[int]) -> int:
        """Delete thumbnails whose video no longer exists in the DB."""
        removed = 0
        for f in Path(thumbs_dir).glob("*.jpg"):
            try:
                video_id = int(f.stem)
            except ValueError:
                continue
            if video_id not in valid_ids:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
