import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def av_hook():
    spec = importlib.util.spec_from_file_location(
        "hook_av", ROOT / "build-hooks" / "hook-av.py"
    )
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


def test_av_runtime_submodules_importable(av_hook):
    """Guard for the PyInstaller hook-av.py list.

    The frozen exe failed metadata probes for every video with an embedded
    subtitle track because PyAV lazy-imports av.subtitles.stream (and other
    Cython submodules) at runtime, which the exe's module graph lacked. If an
    av upgrade drops or renames one of these, the hook list must follow —
    failing here before the next build catches it in dev.
    """
    hiddenimports = av_hook.hiddenimports

    assert len(hiddenimports) >= 20
    for name in hiddenimports:
        importlib.import_module(name)