from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QLineEdit


class SearchBar(QLineEdit):
    """Filename search with 300ms debounce."""

    search_submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("搜索文件名... (支持中文)")
        self.setClearButtonEnabled(True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(lambda: self.search_submitted.emit(self.text().strip()))
        self.textChanged.connect(lambda _: self._timer.start())
