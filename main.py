import faulthandler
import sys

from PyQt6.QtWidgets import QApplication

import config
from domain.repository import Repository
from ui.main_window import MainWindow


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
    window = MainWindow(repo)
    window.show()
    exit_code = app.exec()
    repo.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
