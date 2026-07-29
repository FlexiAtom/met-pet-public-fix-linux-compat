"""MeaPet 的键盘友好型浮动消息输入框。"""

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from meapet.desktop.theme import CHAT_COMPOSER_STYLE
from meapet.ui_theme import (
    MIN_TARGET_SIZE,
    ensure_application_fonts,
    set_scaled_stylesheet,
)


CHAT_COMPOSER_WIDTH = 480
CHAT_COMPOSER_HEIGHT = 112


def set_awaiting_reply_state(
    host,
    awaiting: bool,
    message: str = "",
) -> None:
    """同步请求锁与当前消息编辑器，避免回复结束后仍保持只读。"""
    busy = bool(awaiting)
    host._awaiting_reply = busy
    composer = getattr(host, "_chat_input", None)
    if composer is None:
        return
    set_busy = getattr(composer, "set_busy", None)
    if not callable(set_busy):
        return
    try:
        set_busy(busy, message if busy else "")
    except RuntimeError:
        # Qt 对象已被销毁时清理悬空引用；请求状态本身仍已正确更新。
        if getattr(host, "_chat_input", None) is composer:
            host._chat_input = None


class ChatInputBox(QWidget):
    """置顶的消息编辑器，支持 Enter 发送与 Esc 关闭。"""

    text_submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        ensure_application_fonts()
        self.setWindowTitle("和梅尔对话")
        self.setObjectName("ChatComposerRoot")
        self.setFixedSize(CHAT_COMPOSER_WIDTH, CHAT_COMPOSER_HEIGHT)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAccessibleName("和梅尔对话")
        self.setAccessibleDescription("输入消息后按 Enter 或点击发送；按 Escape 关闭")
        set_scaled_stylesheet(self, CHAT_COMPOSER_STYLE)

        self._opacity = 0.0
        self._fade_step = 0.08
        self._closing = False
        self.voice_host = None
        self._reduced_motion = os.environ.get("MEAPET_REDUCED_MOTION", "").lower() in {
            "1",
            "true",
            "yes",
        }

        self._build_ui()

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate_in)
        if self._reduced_motion:
            self._opacity = 1.0
            self.setWindowOpacity(1.0)
        else:
            self.setWindowOpacity(0.0)
            self._anim_timer.start(18)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.container = QFrame()
        self.container.setObjectName("ChatComposer")
        outer.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(8)

        title = QLabel("发消息给梅尔")
        title.setObjectName("ComposerTitle")
        title.setAccessibleName("消息编辑器")
        header.addWidget(title)

        self.hint_label = QLabel("Enter 发送 · Esc 关闭")
        self.hint_label.setObjectName("ComposerHint")
        header.addWidget(self.hint_label)

        self.feedback_label = QLabel("")
        self.feedback_label.setObjectName("ComposerFeedback")
        self.feedback_label.setAccessibleName("消息输入提示")
        self.feedback_label.hide()
        header.addWidget(self.feedback_label)
        header.addStretch()

        self.voice_button = QPushButton("mic")
        self.voice_button.setObjectName("VoiceButton")
        self.voice_button.setFixedSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        self.voice_button.setAccessibleName("语音输入")
        self.voice_button.setToolTip("点击开始录音，再点停止并转文字")
        self.voice_button.clicked.connect(self._toggle_voice)
        self.voice_button.hide()
        header.addWidget(self.voice_button)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_recording)
        self._pulse_state = False
        self._voice_state = "idle"

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("ComposerCloseButton")
        self.close_button.setFixedSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        self.close_button.setAccessibleName("关闭消息输入框")
        self.close_button.setToolTip("关闭（Esc）")
        self.close_button.clicked.connect(self._close_with_fade)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.input = QLineEdit()
        self.input.setObjectName("MessageInput")
        self.input.setMinimumHeight(MIN_TARGET_SIZE)
        self.input.setPlaceholderText("输入你想说的话")
        self.input.setAccessibleName("消息内容")
        self.input.setAccessibleDescription("按 Enter 发送消息")
        self.input.returnPressed.connect(self._submit)
        self.input.textChanged.connect(self._clear_feedback)
        input_row.addWidget(self.input, 1)

        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("SendButton")
        self.send_button.setMinimumSize(80, MIN_TARGET_SIZE)
        self.send_button.setAccessibleName("发送消息")
        self.send_button.setDefault(True)
        self.send_button.setAutoDefault(True)
        self.send_button.clicked.connect(self._submit)
        input_row.addWidget(self.send_button)
        layout.addLayout(input_row)

        self.setTabOrder(self.input, self.send_button)
        self.setTabOrder(self.send_button, self.close_button)

        self._busy = False

    def set_busy(self, busy: bool, message: str = "") -> None:
        """异步回复进行中时禁用发送，并给出可读反馈。"""
        self._busy = bool(busy)
        self.send_button.setEnabled(not self._busy)
        self.input.setReadOnly(self._busy)
        if self._busy:
            text = message or "正在等待回复…"
            self.feedback_label.setText(text)
            self.hint_label.hide()
            self.feedback_label.show()
            self.send_button.setToolTip(text)
            self.setAccessibleDescription(text)
        else:
            self.send_button.setToolTip("发送消息（Enter）")
            self.setAccessibleDescription(
                "输入消息后按 Enter 或点击发送；按 Escape 关闭"
            )
            self._clear_feedback(self.input.text())

    def _animate_in(self) -> None:
        if self._closing:
            return
        self._opacity = min(1.0, self._opacity + self._fade_step)
        self.setWindowOpacity(self._opacity)
        if self._opacity >= 1.0:
            self._anim_timer.stop()

    def _toggle_voice(self) -> None:
        """点击麦克风按钮，委托给 MeaPet 处理。"""
        host = self.voice_host
        if host and hasattr(host, "_voice_toggle_recording"):
            state = host._voice_toggle_recording()
        else:
            self.voice_button.setToolTip("语音输入未就绪")

    def _on_voice_state_changed(self, state: str) -> None:
        """接收 VoiceEngine 状态更新，更新按钮样式和提示文字。"""
        self._voice_state = state
        self._pulse_timer.stop()

        if state == "recording":
            self.voice_button.setText("●")
            self.voice_button.setObjectName("VoiceButtonRecording")
            self.voice_button.setAccessibleName("录音中，点击停止")
            self.voice_button.setToolTip("录音中…点击停止")
            self.voice_button.setEnabled(True)
            self._pulse_timer.start(500)
            self._pulse_state = False
            self.hint_label.setText("录音中…再次点击停止并转文字")
        elif state == "processing":
            self.voice_button.setText("...")
            self.voice_button.setObjectName("VoiceButtonProcessing")
            self.voice_button.setAccessibleName("识别中，请稍候")
            self.voice_button.setToolTip("正在转文字…")
            self.voice_button.setEnabled(False)
            self.hint_label.setText("正在转文字…")
        elif state == "done":
            self.voice_button.setText("mic")
            self.voice_button.setObjectName("VoiceButton")
            self.voice_button.setAccessibleName("语音输入")
            self.voice_button.setToolTip("点击开始录音，再点停止并转文字")
            self.voice_button.setEnabled(True)
            self.hint_label.setText("Enter 发送 · Esc 关闭")
        elif state == "error":
            self.voice_button.setText("!")
            self.voice_button.setObjectName("VoiceButtonError")
            self.voice_button.setAccessibleName("语音识别失败，点击重试")
            self.voice_button.setToolTip("识别失败，点击重试")
            self.voice_button.setEnabled(True)
            self.hint_label.setText("识别失败，点击重试 · Esc 关闭")

    def _pulse_recording(self):
        """录音中闪烁红点。"""
        self._pulse_state = not self._pulse_state
        if self._pulse_state:
            self.voice_button.setObjectName("VoiceButtonRecordingBright")
        else:
            self.voice_button.setObjectName("VoiceButtonRecording")
        # force style refresh
        self.voice_button.style().unpolish(self.voice_button)
        self.voice_button.style().polish(self.voice_button)

    def show_voice_button(self, visible: bool) -> None:
        self.voice_button.setVisible(visible)

    def _submit(self) -> None:
        if getattr(self, "_busy", False):
            if not self.feedback_label.text():
                self.feedback_label.setText("正在等待回复…")
            self.hint_label.hide()
            self.feedback_label.show()
            return
        text = self.input.text().strip()
        if not text:
            self.feedback_label.setText("请输入内容后再发送")
            self.hint_label.hide()
            self.feedback_label.show()
            self.input.setFocus(Qt.OtherFocusReason)
            return
        self.send_button.setEnabled(False)
        self._clear_feedback(text)
        # 先退出编辑浮窗，再同步发出信号；接收方显示气泡时不会与输入框重叠。
        self._closing = True
        self._anim_timer.stop()
        self.hide()
        self.close()
        self.text_submitted.emit(text)

    def _clear_feedback(self, _text: str) -> None:
        if self.feedback_label.text():
            self.feedback_label.clear()
        self.feedback_label.hide()
        self.hint_label.show()

    def _close_with_fade(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._reduced_motion:
            self.close()
            return
        self._fade_step = 0.10
        self._anim_timer.stop()
        try:
            self._anim_timer.timeout.disconnect()
        except TypeError:
            pass
        self._anim_timer.timeout.connect(self._fade_out)
        self._anim_timer.start(20)

    def _fade_out(self) -> None:
        self._opacity = max(0.0, self._opacity - self._fade_step)
        if self._opacity <= 0.0:
            self._anim_timer.stop()
            self.close()
            return
        self.setWindowOpacity(self._opacity)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._close_with_fade()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.input.setFocus(Qt.OtherFocusReason)
        self.input.selectAll()

    def closeEvent(self, event) -> None:
        self._anim_timer.stop()
        super().closeEvent(event)
