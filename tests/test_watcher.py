import threading
import time

import pytest

from PyQt6.QtWidgets import QApplication
from domain.repository import Repository
from services.watcher import WatcherThread
from tests.helpers import make_test_video, wait_for

DEBOUNCE = 0.2
POLL = 0.05


@pytest.fixture()
def watch_env(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    root = tmp_path / "watch"
    root.mkdir()
    yield repo, root
    repo.close()


def _start_thread(repo, root, **kw):
    thread = WatcherThread(
        root, repo, debounce=kw.get("debounce", DEBOUNCE), poll=kw.get("poll", POLL)
    )
    thread.start()
    assert thread.ready.wait(5), "observer did not become ready"
    return thread


def test_watcher_adds_and_removes(watch_env):
    repo, root = watch_env
    thread = _start_thread(repo, str(root))
    try:
        new_file = root / "new_video.mp4"
        make_test_video(new_file)
        assert wait_for(lambda: repo.get_by_path(str(new_file)) is not None)
        assert repo.count() == 1

        new_file.unlink()
        assert wait_for(lambda: repo.get_by_path(str(new_file)) is None)
        assert repo.count() == 0
    finally:
        thread.stop()
        thread.wait(5000)


def test_watcher_ignores_non_video(watch_env):
    repo, root = watch_env
    thread = _start_thread(repo, str(root))
    try:
        (root / "notes.txt").write_text("hello")
        time.sleep(1.0)
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
    thread = _start_thread(repo, [str(root_a), str(root_b)])
    try:
        fa = root_a / "a.mp4"
        fb = root_b / "b.mp4"
        make_test_video(fa)
        make_test_video(fb)
        assert wait_for(lambda: repo.get_by_path(str(fa)) is not None)
        assert wait_for(lambda: repo.get_by_path(str(fb)) is not None)
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
    thread = WatcherThread(
        [str(missing), str(root)], repo, debounce=DEBOUNCE, poll=POLL
    )
    thread.message.connect(msgs.append)
    thread.start()
    assert thread.ready.wait(5), "thread must still become ready"
    try:
        f = root / "a.mp4"
        make_test_video(f)
        assert wait_for(lambda: repo.get_by_path(str(f)) is not None), (
            "the valid root must keep working"
        )
        for _ in range(10):
            QApplication.processEvents()
        assert any("跳过" in m for m in msgs), f"missing root must be reported: {msgs}"
    finally:
        thread.stop()
        thread.wait(5000)
        repo.close()


def test_flush_survives_concurrent_mutation(watch_env, monkeypatch):
    """Watchdog events arriving while _flush drains must never raise.

    The old code iterated the shared sets directly, so an event landing in
    the middle of a flush raised "Set changed size during iteration". That
    exception escapes QThread.run(), which PyQt6 turns into a hard process
    abort (0xc0000409) - the startup flash-crash on the real library.
    """
    from services import watcher as watcher_mod

    calls: list[tuple[list, list]] = []

    class _FakeLibrary:
        def __init__(self, repo):
            pass

        def apply_sync(self, probes, removals, progress=None, should_cancel=None):
            calls.append((list(probes), list(removals)))

    monkeypatch.setattr(watcher_mod, "Library", _FakeLibrary)

    repo, root = watch_env
    thread = WatcherThread(str(root), repo)
    stop = False

    def mutator():
        i = 0
        while not stop:
            i += 1
            with thread._lock:
                thread._changed.add(f"ghost_{i}.mp4")
                thread._added[f"ghost_{i}.mp4"] = time.time()
            time.sleep(0.0005)

    t = threading.Thread(target=mutator, daemon=True)
    t.start()
    try:
        for _ in range(50):
            thread._flush()  # must never raise, whatever the mutator does
            time.sleep(0.0005)
    finally:
        stop = True
        t.join(5)
    assert calls, "flush must still hand a consistent batch to apply_sync"
    assert repo.count() == 0


def test_flush_indexes_pending_changes_directly(watch_env):
    """_flush applies added paths without needing the observer plumbing."""
    repo, root = watch_env
    thread = WatcherThread(str(root), repo)
    f = root / "v.mp4"
    make_test_video(f)
    thread._added[str(f)] = time.time()
    thread._flush()
    assert repo.get_by_path(str(f)) is not None
