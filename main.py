import faulthandler
import sys

from PyQt6.QtWidgets import QApplication

import config
from domain.repository import Repository
from ui.main_window import MainWindow


def main() -> int:
    faulthandler.enable()
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
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
