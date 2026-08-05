import threading
from pathlib import Path

import av
from av.filter import Graph

import config

THUMB_WIDTH = 170
THUMB_HEIGHT = 96
THUMB_ASPECT = THUMB_WIDTH / THUMB_HEIGHT  # ~16:9, crops source to fill

THUMB_SCALE = 2  # generate at 2x so HiDPI displays stay crisp
THUMB_PIXEL_WIDTH = THUMB_WIDTH * THUMB_SCALE
THUMB_PIXEL_HEIGHT = THUMB_HEIGHT * THUMB_SCALE


def _log_failure(filepath: str, exc: Exception) -> None:
    try:
        config.APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.APP_DIR / "thumbnails.log", "a", encoding="utf-8") as f:
            f.write(f"{filepath}: {type(exc).__name__}: {exc}\n")
    except OSError:
        pass


def _center_crop(frame: av.VideoFrame) -> av.VideoFrame:
    """Center-crop to the thumbnail aspect so the image fills the cell."""
    src_w, src_h = frame.width, frame.height
    target_ar = THUMB_ASPECT
    if src_w / src_h > target_ar:
        crop_w = int(round(src_h * target_ar))
        crop_w -= crop_w % 2  # keep even for yuv420p
        x0 = (src_w - crop_w) // 2
        x0 -= x0 % 2
        crop_h, y0 = src_h, 0
    else:
        crop_h = int(round(src_w / target_ar))
        crop_h -= crop_h % 2
        y0 = (src_h - crop_h) // 2
        y0 -= y0 % 2
        crop_w, x0 = src_w, 0

    graph = Graph()
    src = graph.add(
        "buffer",
        video_size=f"{src_w}x{src_h}",
        pix_fmt=frame.format.name,
        time_base="1/25",
        frame_rate="25/1",
    )
    crop = graph.add("crop", w=str(crop_w), h=str(crop_h), x=str(x0), y=str(y0))
    sink = graph.add("buffersink")
    src.link_to(crop)
    crop.link_to(sink)
    graph.configure()
    src.push(frame)
    return sink.pull()


def _extract_frame(filepath: str, thumb_path: Path) -> bool:
    """Seek to ~10% of the video, crop-fill and scale one frame, save as JPEG (pure PyAV)."""
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
            scaled = _center_crop(frame).reformat(
                width=THUMB_PIXEL_WIDTH,
                height=THUMB_PIXEL_HEIGHT,
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


def _is_stale_thumb(thumb_path: Path) -> bool:
    """True when an existing thumbnail was generated at an older resolution
    (1x). Reading just the JPEG header is cheap enough to run once per
    scheduled generation task, and lets existing libraries upgrade lazily."""
    try:
        with av.open(str(thumb_path)) as container:
            stream = next(
                (s for s in container.streams if s.type == "video"), None
            )
            if stream is None:
                return True
            return stream.width < THUMB_PIXEL_WIDTH or stream.height < THUMB_PIXEL_HEIGHT
    except Exception:
        return True


class Thumbnailer:
    """Lazy thumbnail generation with a bounded worker thread."""

    def __init__(self, thumbs_dir: str | Path | None = None):
        self._dir = Path(thumbs_dir or config.THUMBS_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._pending: set[str] = set()

    @staticmethod
    def path(thumbs_dir: str | Path, video_id: int) -> Path:
        """Thumbnail file location: the single source of truth for the formula."""
        return Path(thumbs_dir) / f"{video_id}.jpg"

    def path_for(self, video_id: int) -> Path:
        return self.path(self._dir, video_id)

    def exists(self, video_id: int) -> bool:
        return self.path_for(video_id).exists()

    def ensure(self, filepath: str, video_id: int, repo=None) -> bool:
        """Generate thumb if missing (or zero-byte/old-resolution). When repo
        is given, persist thumb_path."""
        thumb = self.path_for(video_id)
        if thumb.exists() and thumb.stat().st_size > 0 and not _is_stale_thumb(thumb):
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
        """Delete thumbnails whose video no longer exists in the DB, plus
        zero-byte files (corrupt thumbs would never regenerate otherwise)."""
        removed = 0
        for f in Path(thumbs_dir).glob("*.jpg"):
            try:
                video_id = int(f.stem)
            except ValueError:
                continue
            if video_id not in valid_ids or f.stat().st_size == 0:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def delete_for(self, ids) -> int:
        """Delete thumbnails for deleted videos so their ids can never be reused."""
        removed = 0
        for video_id in ids:
            try:
                self.path_for(video_id).unlink()
                removed += 1
            except OSError:
                pass
        return removed
