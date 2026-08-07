"""设置对话框: 主题(跟随系统/浅色/深色)、音量记忆、备份保留份数。

「确定」保存到 settings.json 并立即应用主题(apply_theme);「取消」不改任何
设置。音量与播放器共用 "volume" 键(播放器 closeEvent 记忆同一值)。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

import config
from ui.theme import apply_theme

THEME_CHOICES = [
    ("system", "跟随系统"),
    ("light", "浅色"),
    ("dark", "深色"),
]


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(360)

        settings = config.load_settings()

        self.combo_theme = QComboBox()
        for value, label in THEME_CHOICES:
            self.combo_theme.addItem(label, value)
        current = settings.get("theme", "system")
        idx = next(
            (i for i, (value, _l) in enumerate(THEME_CHOICES) if value == current),
            0,
        )
        self.combo_theme.setCurrentIndex(idx)

        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(int(settings.get("volume", 80)))
        self.volume_label = QLabel(f"{self.slider_volume.value()}%")
        self.slider_volume.valueChanged.connect(
            lambda v: self.volume_label.setText(f"{v}%")
        )

        self.spin_backup = QSpinBox()
        self.spin_backup.setRange(1, 30)
        self.spin_backup.setValue(int(settings.get("backup_keep", config.BACKUP_KEEP)))
        self.spin_backup.setSuffix(" 份")

        volume_row = QHBoxLayout()
        volume_row.addWidget(self.slider_volume)
        volume_row.addWidget(self.volume_label)

        form = QFormLayout()
        form.addRow("主题", self.combo_theme)
        form.addRow("音量", volume_row)
        form.addRow("备份保留", self.spin_backup)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def accept(self) -> None:
        config.save_setting("theme", self.combo_theme.currentData())
        config.save_setting("volume", self.slider_volume.value())
        config.save_setting("backup_keep", self.spin_backup.value())

        app = QApplication.instance()
        if app is not None:
            apply_theme(app)
        super().accept()
