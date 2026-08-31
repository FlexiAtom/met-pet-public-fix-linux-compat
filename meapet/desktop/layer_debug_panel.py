"""穿透开关：贴边常驻小窗口，用于切换「穿透 / 交互」双模。

穿透模式下桌宠由 layer surface 呈现且完全不可交互（点击穿透到下层），
只能通过这个开关切回交互模式来拖动或唤出右键菜单。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class LayerDebugPanel(QWidget):
    """可拖动的常驻开关窗口。"""

    def __init__(self, on_toggle=None):
        super().__init__()
        self._on_toggle = on_toggle
        self._drag_offset = None
        self._penetrate = True

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setFixedSize(150, 78)
        self.setWindowTitle("穿透开关")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._label = QLabel("点击穿透：开")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet(
            "color:#fff; background:#333; border-radius:4px; padding:4px;"
        )
        self._btn = QPushButton("切到 交互")
        self._btn.clicked.connect(self._toggle)

        layout.addWidget(self._label)
        layout.addWidget(self._btn)
        self.setStyleSheet("background:#222; border-radius:6px;")

    # ---------------- 切换 ----------------
    def _toggle(self):
        self._penetrate = not self._penetrate
        self._label.setText(f"点击穿透：{'开' if self._penetrate else '关'}")
        self._btn.setText("切到 交互" if self._penetrate else "切到 穿透")
        print(f"[panel] 切换到 {'穿透' if self._penetrate else '交互'} 模式", flush=True)
        if self._on_toggle is None:
            print("[panel] ✗ 回调为空", flush=True)
            return
        try:
            self._on_toggle(self._penetrate)
        except Exception as exc:
            print(f"[panel] ✗ 切换异常: {type(exc).__name__}: {exc}", flush=True)

    # ---------------- 拖动 ----------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.pos()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

