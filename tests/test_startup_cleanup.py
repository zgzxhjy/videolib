import os
import shutil
import sys
import time

from services import startup_cleanup


def _make_mei(tmp_path, name, age_s):
    d = tmp_path / name
    d.mkdir()
    (d / "python3.dll").write_bytes(b"x")
    old = time.time() - age_s
    os.utime(d, (old, old))
    return d


def _fake_meipass(monkeypatch, tmp_path, current="_MEI100"):
    cur = tmp_path / current
    cur.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(cur), raising=False)
    return cur


def test_noop_when_running_from_source(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    leftover = _make_mei(tmp_path, "_MEIold", 3600)
    assert startup_cleanup.cleanup_stale_mei() == 0
    assert leftover.exists()


def test_removes_all_leftovers_when_alone(tmp_path, monkeypatch):
    _fake_meipass(monkeypatch, tmp_path)
    stale = _make_mei(tmp_path, "_MEIold", 3600)
    fresh = _make_mei(tmp_path, "_MEIfresh", 5)
    monkeypatch.setattr(startup_cleanup, "_other_instances_running", lambda: False)
    assert startup_cleanup.cleanup_stale_mei() == 2
    assert not stale.exists()
    assert not fresh.exists()
    assert (tmp_path / "_MEI100").exists()


def test_concurrent_instance_protects_recent(tmp_path, monkeypatch):
    _fake_meipass(monkeypatch, tmp_path)
    old = _make_mei(tmp_path, "_MEIold", 3600)
    recent = _make_mei(tmp_path, "_MEIrecent", 5 * 60)
    monkeypatch.setattr(startup_cleanup, "_other_instances_running", lambda: True)
    assert startup_cleanup.cleanup_stale_mei() == 1
    assert not old.exists()
    assert recent.exists()


def test_locked_dir_skipped_silently(tmp_path, monkeypatch):
    _fake_meipass(monkeypatch, tmp_path)
    locked = _make_mei(tmp_path, "_MEIlocked", 3600)
    other = _make_mei(tmp_path, "_MEIother", 3600)
    monkeypatch.setattr(startup_cleanup, "_other_instances_running", lambda: False)
    real_rmtree = shutil.rmtree

    def flaky(path, *args, **kwargs):
        if str(path).endswith("_MEIlocked"):
            raise PermissionError("in use")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", flaky)
    assert startup_cleanup.cleanup_stale_mei() == 1
    assert locked.exists()
    assert not other.exists()