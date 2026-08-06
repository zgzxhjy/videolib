from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

import config
from domain.repository import Repository
from services.backup import backup_db, list_backups
from ui.video_list import _fmt_size


def _display_name(path) -> str:
    """'videolib-20260706-143000.db' -> '2026-07-06 14:30:00' (best effort)."""
    stem = path.stem  # videolib-YYYYMMDD-HHMMSS
    try:
        stamp = stem.split("-", 1)[1]
        ts = datetime.strptime(stamp, "%Y%m%d-%H%M%S")
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    except (IndexError, ValueError):
        return stem


class RestoreBackupDialog(QDialog):
    """List database snapshots, take a new one, or pick one to restore.

    Restoring is applied by the caller (MainWindow) after this dialog closes:
    restoring closes the live DB and relaunches the app, so it cannot happen
    from inside a modal dialog. The selected backup is exposed as
    `self.restore_choice`.
    """

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self._repo = repo
        self.restore_choice = None
        self.setWindowTitle("备份与还原")
        self.setMinimumWidth(460)

        self.list = QListWidget()
        self.btn_backup = QPushButton("立即备份")
        self.btn_backup.clicked.connect(self._take_backup)
        self.btn_restore = QPushButton("还原选中...")
        self.btn_restore.clicked.connect(self._choose_restore)
        self.btn_open = QPushButton("打开备份文件夹")
        self.btn_open.clicked.connect(self._open_folder)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(lambda _: self.close())

        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_backup)
        bottom.addWidget(self.btn_restore)
        bottom.addWidget(self.btn_open)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addLayout(bottom)

        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        backups = list_backups()
        if not backups:
            self.list.addItem("(暂无备份)")
            self.btn_restore.setEnabled(False)
            return
        self.btn_restore.setEnabled(True)
        for p in backups:
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            self.list.addItem(f"{_display_name(p)}   ·   {_fmt_size(size)}  {p.name}")

    def _take_backup(self) -> None:
        dest = backup_db(self._repo, force=True)
        if dest is None:
            QMessageBox.information(self, "备份与还原", "备份失败，请检查磁盘空间。")
            return
        self._reload()
        self.list.setCurrentRow(0)
        QMessageBox.information(self, "备份与还原", f"已备份到:\n{dest}")

    def _choose_restore(self) -> None:
        item = self.list.currentItem()
        if item is None:
            QMessageBox.warning(self, "备份与还原", "请先选择一个备份")
            return
        row = self.list.currentRow()
        backups = list_backups()
        path = backups[row]
        reply = QMessageBox.question(
            self,
            "备份与还原",
            f"还原到 {_display_name(path)}？\n\n"
            "还原前会自动备份当前数据库；缩略图将被清空并在浏览时重新生成；"
            "\n完成后应用将自动重启。",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.restore_choice = path
        self.accept()

    def _open_folder(self) -> None:
        import os

        config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(config.BACKUPS_DIR))