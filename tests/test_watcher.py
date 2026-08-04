import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.repository import Repository
from services.watcher import WatcherThread
from tests.test_scan import _make_test_video


@pytest.fixture()
def watch_env(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    root = tmp_path / "watch"
    root.mkdir()
    yield repo, root
    repo.close()


def _wait_for(condition, timeout=15.0, interval=0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def test_watcher_adds_and_removes(watch_env):
    repo, root = watch_env
    thread = WatcherThread(str(root), repo)
    thread.start()
    try:
        new_file = root / "new_video.mp4"
        _make_test_video(new_file)
        assert _wait_for(lambda: repo.get_by_path(str(new_file)) is not None)
        assert repo.count() == 1

        new_file.unlink()
        assert _wait_for(lambda: repo.get_by_path(str(new_file)) is None)
        assert repo.count() == 0
    finally:
        thread.stop()
        thread.wait(5000)


def test_watcher_ignores_non_video(watch_env):
    repo, root = watch_env
    thread = WatcherThread(str(root), repo)
    thread.start()
    try:
        (root / "notes.txt").write_text("hello")
        time.sleep(3.5)
        assert repo.count() == 0
    finally:
        thread.stop()
        thread.wait(5000)
