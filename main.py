import faulthandler
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

import config
from domain.repository import Repository
from ui.main_window import MainWindow


def resolve_icon_path() -> Path:
    """Location of app.ico: bundled temp dir when frozen, repo root in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "app.ico"
    return Path(__file__).resolve().parent / "app.ico"


def main() -> int:
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        crash_log = open(config.APP_DIR / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(file=crash_log)
    except OSError:
        pass
    repo = Repository(config.DB_PATH)
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    icon_path = resolve_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow(repo)
    window.show()
    exit_code = app.exec()
    repo.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
