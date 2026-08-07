"""Shared test helpers (no pytest imports; plain functions only)."""

import time
from pathlib import Path


def make_test_video(path: Path, seconds: int = 1) -> Path:
    """Generate a tiny real video file using PyAV (mpeg4, 64x48)."""
    import av

    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = 64
        stream.height = 48
        stream.pix_fmt = "yuv420p"
        for i in range(seconds * 10):
            frame = av.VideoFrame(64, 48, "yuv420p")
            for plane in frame.planes:
                plane.update(bytes([i % 256]) * plane.buffer_size)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def mk_video(repo, filepath: str) -> None:
    from domain.models import Video

    repo.upsert_videos([Video(filename=Path(filepath).name, filepath=filepath)])
