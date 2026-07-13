"""Startup splash / loading page for MeaPet."""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from meapet.desktop.theme import SPLASH_STYLE
from meapet.ui_theme import ensure_application_fonts, set_scaled_stylesheet


class StartupSplash(QWidget):
    """Centered frameless loading card shown while the pet boots."""

    finished = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        ensure_application_fonts()
        self.setWindowTitle("MeaPet")
        self.setObjectName("SplashRoot")
        self.setFixedSize(440, 300)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setAccessibleName("MeaPet 启动进度")
        set_scaled_stylesheet(self, SPLASH_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        self.card = QFrame()
        self.card.setObjectName("SplashCard")
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(10)
        outer.addWidget(self.card)

        title_row = QHBoxLayout()
        mark = QLabel("M")
        mark.setObjectName("SplashMark")
        mark.setFixedSize(36, 36)
        mark.setAlignment(Qt.AlignCenter)
        mark.setAccessibleName("MeaPet")
        title_row.addWidget(mark)
        title = QLabel("MeaPet")
        title.setObjectName("SplashTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)

        subtitle = QLabel("梅尔桌宠 · 正在准备…")
        subtitle.setObjectName("SplashSubtitle")
        layout.addWidget(subtitle)

        self.status = QLabel("初始化")
        self.status.setObjectName("SplashStatus")
        self.status.setAccessibleName("当前启动步骤")
        layout.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setAccessibleName("启动进度")
        layout.addWidget(self.progress)

        self.detail = QLabel("")
        self.detail.setObjectName("SplashDetail")
        self.detail.setWordWrap(True)
        self.detail.setAccessibleName("启动详情")
        layout.addWidget(self.detail)
        layout.addStretch()

        foot = QLabel("双击桌宠对话 · 右键打开菜单")
        foot.setObjectName("SplashHint")
        foot.setAlignment(Qt.AlignCenter)
        layout.addWidget(foot)

        self._steps: List[Tuple[str, Callable[[], None]]] = []
        self._index = 0
        self._result = None
        self._error: Optional[str] = None

    def set_steps(self, steps: List[Tuple[str, Callable[[], None]]]) -> None:
        self._steps = list(steps or [])
        self._index = 0
        if self._steps:
            self.progress.setRange(0, len(self._steps))
            self.progress.setValue(0)

    def start(self) -> None:
        # center on primary screen
        try:
            from PyQt5.QtWidgets import QApplication
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(
                screen.center().x() - self.width() // 2,
                screen.center().y() - self.height() // 2,
            )
        except Exception:
            pass
        self.show()
        self.raise_()
        QTimer.singleShot(80, self._run_next)

    def _run_next(self) -> None:
        if self._index >= len(self._steps):
            self.status.setText("准备就绪")
            self._set_status_property("success")
            self.detail.setText("梅尔来了喵～")
            self.progress.setValue(self.progress.maximum())
            QTimer.singleShot(350, self._emit_finished)
            return

        label, fn = self._steps[self._index]
        self.status.setText(label)
        self.detail.setText(f"步骤 {self._index + 1}/{len(self._steps)}")
        self.progress.setValue(self._index)
        try:
            self._result = fn()
        except Exception as e:
            self._error = str(e)
            self.status.setText("启动失败")
            self._set_status_property("error")
            self.detail.setText(str(e))
            self.failed.emit(str(e))
            return
        self._index += 1
        self.progress.setValue(self._index)
        QTimer.singleShot(40, self._run_next)

    def _emit_finished(self) -> None:
        self.finished.emit()
        # hide 而非 close：避免部分平台把“最后窗口关闭”当成退出
        self.hide()

    @property
    def result(self):
        return self._result

    def _set_status_property(self, status: str) -> None:
        self.status.setProperty("status", status)
        style = self.status.style()
        style.unpolish(self.status)
        style.polish(self.status)
        self.status.update()
