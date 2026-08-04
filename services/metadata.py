from pathlib import Path

import av

from domain.models import Video


def probe(filepath: str) -> tuple[float | None, str | None, str | None]:
    """Extract (duration_seconds, resolution, codec) via PyAV."""
    try:
        with av.open(filepath) as container:
            duration = None
            if container.duration is not None:
                duration = container.duration / av.time_base
            resolution = None
            codec = None
            stream = next(
                (s for s in container.streams if s.type == "video"), None
            )
            if stream is not None:
                if stream.codec_context.width and stream.codec_context.height:
                    resolution = (
                        f"{stream.codec_context.width}x{stream.codec_context.height}"
                    )
                codec = stream.codec_context.name
            return duration, resolution, codec
    except Exception:
        return None, None, None


def build_video(filepath: str) -> Video:
    p = Path(filepath)
    duration, resolution, codec = probe(filepath)
    try:
        file_size = p.stat().st_size
    except OSError:
        file_size = 0
    return Video(
        filename=p.name,
        filepath=filepath,
        file_size=file_size,
        duration=duration,
        resolution=resolution,
        codec=codec,
    )
