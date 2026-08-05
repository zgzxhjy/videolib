from dataclasses import dataclass
from pathlib import Path

import config
from domain.repository import Repository
from services.metadata import build_video
from services.thumbnailer import Thumbnailer


@dataclass
class SyncResult:
    probed: int
    removed: int
    changed: int
    canceled: bool = False


class Library:
    """Owns the cross-cutting invariants of keeping the library in sync with
    the filesystem.

    Probing metadata, upserting rows, and deleting stale rows *together with
    their thumbnails* (SQLite reuses row ids, so a leftover {id}.jpg would
    show on a new video). Stateless beyond its dependencies; construct one
    wherever a sync or delete is needed.
    """

    def __init__(self, repo: Repository, thumbs_dir=None):
        self._repo = repo
        self._thumbs_dir = thumbs_dir or config.THUMBS_DIR

    def apply_sync(
        self,
        probes: list[str],
        removals: list[str],
        progress=None,
        should_cancel=None,
    ) -> SyncResult:
        """Probe metadata for `probes`, then delete `removals` rows + thumbs.

        progress(done, total, filepath) is called per probed file.
        should_cancel() is checked before each probe; when canceled the
        partial batch is still upserted but removals are skipped.
        """
        videos = []
        canceled = False
        total = len(probes)
        for i, fp in enumerate(probes, 1):
            if should_cancel is not None and should_cancel():
                canceled = True
                break
            v = build_video(fp)
            if Path(fp).suffix.lower() == ".bin" and v.codec is None:
                # .bin is extension-ambiguous (firmware/ROM/data); only index
                # files that actually carry a video stream.
                if progress is not None:
                    progress(i, total, fp)
                continue
            videos.append(v)
            if progress is not None:
                progress(i, total, fp)
        changed = self._repo.upsert_videos(videos)
        removed = 0
        if not canceled and removals:
            removed = self.remove_paths(removals)
        return SyncResult(
            probed=len(videos), removed=removed, changed=changed, canceled=canceled
        )

    def remove_paths(self, paths: list[str]) -> int:
        """Delete videos by filepath, along with their thumbnails."""
        deleted_ids = self._repo.remove_by_filepaths(paths)
        Thumbnailer(self._thumbs_dir).delete_for(deleted_ids)
        return len(deleted_ids)

    def remove_root(self, root: str) -> int:
        """Delete all videos under a scan root, along with their thumbnails.

        Favorites/category links cascade inside the repository.
        """
        deleted_ids = self._repo.remove_videos_under(root)
        Thumbnailer(self._thumbs_dir).delete_for(deleted_ids)
        return len(deleted_ids)
