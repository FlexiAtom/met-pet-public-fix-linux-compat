"""MeaPet 桌面端的主题化安全对话框。"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from meapet.desktop.theme import CONSENT_DIALOG_STYLE
from meapet.ui_theme import (
    MIN_TARGET_SIZE,
    ensure_application_fonts,
    set_scaled_stylesheet,
)


DEFAULT_CLOUD_CONSENT_MESSAGE = "\n".join(
    [
        "即将截取当前屏幕，并把截图发送到云端识别。",
        "",
        "截图可能包含聊天、密码、邮件、代码或其他隐私信息。",
        "只有本次明确允许后才会上传；取消不会截屏。",
    ]
)


class CloudVisionConsentDialog(QDialog):
    """有倒计时且始终默认拒绝的云端截图确认框。"""

    def __init__(
        self,
        parent=None,
        *,
        title: str = "允许本次云端识图？",
        message: str = DEFAULT_CLOUD_CONSENT_MESSAGE,
        timeout_seconds: int = 5,
        accept_text: str = "允许本次上传",
    ) -> None:
        super().__init__(parent)
        ensure_application_fonts()
        self.setObjectName("CloudConsentRoot")
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.Dialog
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(420, 270)
        set_scaled_stylesheet(self, CONSENT_DIALOG_STYLE)
        self.setAccessibleName("云端识图隐私确认")
        self.setAccessibleDescription(
            "五秒内必须明确允许，否则自动取消；Escape 和 Enter 默认取消"
        )

        self.remaining_seconds = max(1, int(timeout_seconds))
        self.auto_cancelled = False
        self._explicit_allow = False
        self._accept_text = accept_text

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        card = QFrame()
        card.setObjectName("CloudConsentCard")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(6)

        eyebrow = QLabel("隐私保护 · 默认取消")
        eyebrow.setObjectName("ConsentEyebrow")
        layout.addWidget(eyebrow)

        title_label = QLabel(title)
        title_label.setObjectName("ConsentTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        body = QLabel(message)
        body.setObjectName("ConsentBody")
        body.setWordWrap(True)
        body.setAccessibleName("上传隐私说明")
        layout.addWidget(body, 1)

        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("ConsentCountdown")
        self.countdown_label.setWordWrap(True)
        self.countdown_label.setAccessibleName("自动取消倒计时")
        layout.addWidget(self.countdown_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.allow_button = QPushButton(self._accept_text)
        self.allow_button.setObjectName("AllowUploadButton")
        self.allow_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.allow_button.setAccessibleName("明确允许本次截图上传")
        self.allow_button.setAutoDefault(False)
        self.allow_button.setDefault(False)
        self.allow_button.clicked.connect(self._allow_once)
        buttons.addWidget(self.allow_button, 1)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("CancelUploadButton")
        self.cancel_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.cancel_button.setAccessibleName("取消截图上传")
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button, 1)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._update_countdown()

    def _update_countdown(self) -> None:
        self.countdown_label.setText(
            f"{self.remaining_seconds} 秒后自动取消。"
        )
        self.countdown_label.setAccessibleDescription(
            f"剩余 {self.remaining_seconds} 秒，超时后拒绝上传"
        )

    def _tick(self) -> None:
        if self.remaining_seconds <= 0:
            return
        self.remaining_seconds -= 1
        if self.remaining_seconds <= 0:
            self.auto_cancelled = True
            self.reject()
            return
        self._update_countdown()

    def _allow_once(self) -> None:
        self._explicit_allow = True
        self.accept()

    def accept(self) -> None:
        """阻止 Enter、默认按钮或外部误调用绕过显式允许按钮。"""
        if not self._explicit_allow:
            return
        super().accept()

    def done(self, result: int) -> None:
        self._timer.stop()
        super().done(result)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.frameGeometry().center()
        else:
            screen = QApplication.primaryScreen()
            center = screen.availableGeometry().center() if screen is not None else None
        if center is not None:
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            x = center.x() - self.width() // 2
            y = center.y() - self.height() // 2
            if screen is not None:
                available = screen.availableGeometry().adjusted(24, 24, -24, -24)
                max_x = max(available.left(), available.right() - self.width() + 1)
                max_y = max(available.top(), available.bottom() - self.height() + 1)
                x = min(max(x, available.left()), max_x)
                y = min(max(y, available.top()), max_y)
            self.move(x, y)
        self.cancel_button.setFocus(Qt.OtherFocusReason)
        self._timer.start()


def confirm_cloud_vision(
    parent=None,
    *,
    title: str = "允许本次云端识图？",
    message: str = DEFAULT_CLOUD_CONSENT_MESSAGE,
    timeout_seconds: int = 5,
    accept_text: str = "允许本次上传",
) -> bool:
    """仅在用户明确点击允许按钮时返回 ``True``。"""
    dialog = CloudVisionConsentDialog(
        parent,
        title=title,
        message=message,
        timeout_seconds=timeout_seconds,
        accept_text=accept_text,
    )
    return dialog.exec_() == QDialog.Accepted
