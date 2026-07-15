"""对话时间线和完整本轮回复窗口。"""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meapet.conversation.timeline import TurnTranscript
from meapet.desktop.theme import DIALOG_STYLE
from meapet.ui_theme import MIN_TARGET_SIZE, ensure_application_fonts, set_scaled_stylesheet


_SOURCE_NAMES = {
    "user_reply": "用户对话",
    "agent_proactive": "Agent 主动消息",
    "system": "系统",
}


def conversation_label(turn: TurnTranscript) -> str:
    key = turn.conversation_key
    mode = "Agent" if key.mode == "agent" else "直连"
    profile = key.profile_id[:48]
    if key.mode != "agent":
        return f"{mode} · {profile}"
    session = key.session_id
    if len(session) > 32:
        session = f"{session[:18]}…{session[-8:]}"
    return f"{mode} · {profile} · {session}"


def render_turn_text(turn: TurnTranscript) -> str:
    lines = []
    if turn.user_text:
        lines.extend(("用户", turn.user_text, ""))
    if turn.segments:
        lines.append("回复")
        for index, segment in enumerate(turn.segments, 1):
            lines.append(f"{index}. {segment.display_text}")
            voice = str(getattr(segment, "voice_text", "") or "").strip()
            voice_lang = str(getattr(segment, "voice_language", "") or "").strip()
            if voice and voice != str(segment.display_text or "").strip():
                lang = f" ({voice_lang})" if voice_lang else ""
                lines.append(f"   语音{lang}: {voice}")
    if turn.system_entries:
        lines.extend(("", "状态"))
        for entry in turn.system_entries:
            text = entry.safe_text or {
                "started": "正在处理",
                "succeeded": "处理完成",
                "failed": "处理失败",
            }.get(entry.state, "状态已更新")
            lines.append(f"- {text}")
    if turn.error_text:
        lines.extend(("", f"错误：{turn.error_text}"))
    return "\n".join(lines).strip()


class TurnDetailDialog(QDialog):
    def __init__(self, turn: TurnTranscript, parent=None):
        super().__init__(parent)
        ensure_application_fonts()
        self.turn = turn
        self.setObjectName("TimelineDetailDialog")
        self.setWindowTitle("本轮完整回复")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(560, 460)
        self.resize(660, 560)
        set_scaled_stylesheet(self, DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        card = QFrame()
        card.setObjectName("TimelineCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("本轮完整回复")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        self.meta = QLabel(
            f"{conversation_label(turn)} · "
            f"{_SOURCE_NAMES.get(turn.source, turn.source)} · "
            f"{datetime.fromtimestamp(turn.created_at).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.meta.setObjectName("HelperText")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)

        self.content = QPlainTextEdit()
        self.content.setObjectName("TurnBody")
        self.content.setReadOnly(True)
        self.content.setPlainText(render_turn_text(turn))
        self.content.setAccessibleName("本轮完整回复正文")
        layout.addWidget(self.content, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        copy_button = QPushButton("复制全部")
        copy_button.setObjectName("PrimaryButton")
        copy_button.setAccessibleName("复制本轮完整回复")
        copy_button.setMinimumSize(112, MIN_TARGET_SIZE)
        copy_button.clicked.connect(self._copy_all)
        buttons.addWidget(copy_button)
        close_button = QPushButton("关闭")
        close_button.setAccessibleName("关闭完整回复")
        close_button.setMinimumSize(96, MIN_TARGET_SIZE)
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        root.addWidget(card)

    def _copy_all(self) -> None:
        QApplication.clipboard().setText(self.content.toPlainText())


class TimelineDialog(QDialog):
    def __init__(self, timeline, parent=None):
        super().__init__(parent)
        ensure_application_fonts()
        self.timeline = timeline
        self._detail = None
        self.setObjectName("TimelineDialog")
        self.setWindowTitle("对话时间线")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(600, 500)
        self.resize(720, 640)
        set_scaled_stylesheet(self, DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("TimelineCard")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 14, 18, 14)
        header_layout.setSpacing(6)
        title = QLabel("最近对话时间线")
        title.setObjectName("PageTitle")
        header_layout.addWidget(title)
        hint = QLabel(
            "不同后端与 Agent 会话彼此隔离；"
            "使用卡片内按钮查看完整回复，旧会话仅供只读查看。"
        )
        hint.setObjectName("HelperText")
        hint.setWordWrap(True)
        header_layout.addWidget(hint)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("TimelineBody")
        self.turn_layout = QVBoxLayout(body)
        self.turn_layout.setContentsMargins(0, 0, 0, 0)
        self.turn_layout.setSpacing(10)
        self.turn_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        close_button = QPushButton("关闭")
        close_button.setAccessibleName("关闭对话时间线")
        close_button.setMinimumSize(96, MIN_TARGET_SIZE)
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        root.addLayout(footer)
        self.refresh()

    def _make_turn_card(self, turn: TurnTranscript) -> QFrame:
        card = QFrame()
        card.setObjectName("TurnCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        meta = QLabel(
            f"{conversation_label(turn)} · "
            f"{_SOURCE_NAMES.get(turn.source, turn.source)} · "
            f"{datetime.fromtimestamp(turn.created_at).strftime('%H:%M:%S')}"
        )
        meta.setObjectName("TurnMeta")
        meta.setWordWrap(True)
        layout.addWidget(meta)

        if turn.user_text:
            user = QLabel(f"用户：{turn.user_text[:80]}")
            user.setObjectName("TurnUser")
            user.setWordWrap(True)
            layout.addWidget(user)

        preview = turn.display_text or turn.error_text or "状态更新"
        body = QLabel(preview[:160] + ("…" if len(preview) > 160 else ""))
        body.setObjectName("TurnPreview")
        body.setWordWrap(True)
        layout.addWidget(body)

        row = QHBoxLayout()
        row.addStretch()
        open_btn = QPushButton("查看完整回复")
        open_btn.setObjectName("GhostButton")
        open_btn.setMinimumSize(128, MIN_TARGET_SIZE)
        open_btn.setAccessibleName(f"查看本轮：{preview[:40]}")
        open_btn.clicked.connect(
            lambda _checked=False, current=turn: self.show_turn(current)
        )
        row.addWidget(open_btn)
        layout.addLayout(row)
        return card

    def refresh(self) -> None:
        while self.turn_layout.count():
            item = self.turn_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        turns = tuple(reversed(self.timeline.all_recent()))
        if not turns:
            empty = QLabel("还没有可查看的对话。")
            empty.setObjectName("HelperText")
            self.turn_layout.addWidget(empty)
            return
        for turn in turns:
            self.turn_layout.addWidget(self._make_turn_card(turn))
        self.turn_layout.addStretch(1)

    def show_turn(self, turn: TurnTranscript) -> None:
        self._detail = TurnDetailDialog(turn, self)
        self._detail.show()
        self._detail.raise_()
