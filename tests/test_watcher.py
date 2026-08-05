import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication
from domain.repository import Repository
from services.watcher import WatcherThread
from tests.test_scan import _make_test_video


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


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
    assert thread.ready.wait(5), "observer did not become ready"
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
    assert thread.ready.wait(5), "observer did not become ready"
    try:
        (root / "notes.txt").write_text("hello")
        time.sleep(3.5)
        assert repo.count() == 0
    finally:
        thread.stop()
        thread.wait(5000)


def test_watcher_watches_multiple_roots(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    root_a = tmp_path / "watch_a"
    root_b = tmp_path / "watch_b"
    root_a.mkdir()
    root_b.mkdir()
    thread = WatcherThread([str(root_a), str(root_b)], repo)
    thread.start()
    assert thread.ready.wait(5), "observer did not become ready"
    try:
        fa = root_a / "a.mp4"
        fb = root_b / "b.mp4"
        _make_test_video(fa)
        _make_test_video(fb)
        assert _wait_for(lambda: repo.get_by_path(str(fa)) is not None)
        assert _wait_for(lambda: repo.get_by_path(str(fb)) is not None)
        assert repo.count() == 2
    finally:
        thread.stop()
        thread.wait(5000)
        repo.close()


def test_watcher_skips_missing_root_keeps_others(qapp, tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    missing = tmp_path / "gone"
    root = tmp_path / "watch"
    root.mkdir()
    msgs: list[str] = []
    thread = WatcherThread([str(missing), str(root)], repo)
    thread.message.connect(msgs.append)
    thread.start()
    assert thread.ready.wait(5), "thread must still become ready"
    try:
        f = root / "a.mp4"
        _make_test_video(f)
        assert _wait_for(lambda: repo.get_by_path(str(f)) is not None), (
            "the valid root must keep working"
        )
        for _ in range(10):
            QApplication.processEvents()
        assert any("跳过" in m for m in msgs), f"missing root must be reported: {msgs}"
    finally:
        thread.stop()
        thread.wait(5000)
        repo.close()
