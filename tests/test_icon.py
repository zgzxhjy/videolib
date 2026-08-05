import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_app_icon_resolves_and_loads(qapp):
    """app.ico must be reachable in dev (and bundled in the frozen exe)."""
    from PyQt6.QtGui import QIcon

    from main import resolve_icon_path

    p = resolve_icon_path()
    assert p.name == "app.ico"
    assert p.is_file(), "app.ico must sit next to main.py for the dev launcher"
    icon = QIcon(str(p))
    assert not icon.isNull(), "app.ico must decode into a valid QIcon"
