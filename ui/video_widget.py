"""视频承载容器: 一个普通 QWidget, mpv 子窗口渲染内容嵌在它内部。"""
import ctypes

from PyQt6.QtWidgets import QWidget

from services.mpv_session import _child_hwnd, _set_child_rect


class VideoWidget(QWidget):
    """纯容器。内部创建一个 Win32 子窗口承载 mpv 渲染; 尺寸变化时同步。

    子窗口的所有权在本类: ensure_child 创建, resizeEvent 同步尺寸,
    closeEvent 销毁 —— MpvSession 只通过 ensure_child/child_hwnd 取句柄。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._child = 0
        self.setMinimumSize(160, 90)

    def ensure_child(self) -> int:
        if not self._child:
            self._child = _child_hwnd(int(self.winId()), 0, 0,
                                      *self._pixel_size())
        return self._child

    def child_hwnd(self) -> int:
        return self._child

    def _pixel_size(self) -> tuple[int, int]:
        """容器尺寸换算成物理像素(Win32 坐标)。Qt 是逻辑像素, 125% 屏必须乘 dpr。"""
        dpr = self.devicePixelRatio()
        return max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._child:
            _set_child_rect(self._child, 0, 0, *self._pixel_size())

    def closeEvent(self, event) -> None:
        if self._child:
            ctypes.WinDLL("user32").DestroyWindow(self._child)
            self._child = 0
        super().closeEvent(event)
