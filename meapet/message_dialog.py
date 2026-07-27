"""MeaPet 跨桌宠与配置中心复用的主题消息对话框。

Qt 样式表无法控制 Windows 原生标题栏。直接给 ``QMessageBox`` 套深色 QSS
会形成“白色系统标题栏 + 深色内容区”的割裂外观，因此这里使用无边框
``QDialog`` 自绘完整窗口，同时继续返回 ``QMessageBox.StandardButton`` 值，
让现有确认与错误处理逻辑保持不变。
"""
from __future__ import annotations

from PyQt5.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from meapet.desktop.screen_geometry import (
    available_geometry_for,
    calculate_centered_position,
    clamp_position,
)
from meapet.ui_theme import (
    BUTTON_SECONDARY_BG,
    BUTTON_SECONDARY_BG_HOVER,
    BUTTON_SECONDARY_BG_PRESSED,
    DISPLAY_FONT_FAMILY,
    FONT_FAMILY,
    GRADIENT_PAPER,
    GRADIENT_PRIMARY,
    GRADIENT_PRIMARY_HOVER,
    MIN_TARGET_SIZE,
    PALETTE,
    RADIUS_LARGE,
    RADIUS_SMALL,
    ensure_application_fonts,
    rgba,
    seam_highlight,
    set_scaled_stylesheet,
)


MESSAGE_DIALOG_STYLE = f"""
    QDialog#MeaMessageDialog {{
        background: transparent;
        color: {PALETTE['text_primary']};
        font-family: {FONT_FAMILY};
        font-size: 14px;
    }}
    QFrame#MessageCard {{
        background: {GRADIENT_PAPER};
        border: 1px solid {PALETTE['border_strong']};
        border-top-color: {seam_highlight(110)};
        border-radius: {RADIUS_LARGE}px;
    }}
    QWidget#MessageHeader {{
        background: transparent;
    }}
    QLabel#MessageKind {{
        background: transparent;
        color: {PALETTE['text_muted']};
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#MessageTitle {{
        background: transparent;
        color: {PALETTE['text_primary']};
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 20px;
        font-weight: 700;
    }}
    QLabel#MessageStatusIcon {{
        background: {rgba(PALETTE['accent'], 28)};
        color: {PALETTE['accent']};
        border: 1px solid {rgba(PALETTE['accent'], 125)};
        border-radius: 19px;
        min-width: 38px;
        max-width: 38px;
        min-height: 38px;
        max-height: 38px;
        font-family: {DISPLAY_FONT_FAMILY};
        font-size: 20px;
        font-weight: 700;
    }}
    QLabel#MessageStatusIcon[kind="warning"] {{
        background: {rgba(PALETTE['warning'], 24)};
        color: {PALETTE['warning']};
        border-color: {rgba(PALETTE['warning'], 120)};
    }}
    QLabel#MessageStatusIcon[kind="critical"] {{
        background: {rgba(PALETTE['danger'], 26)};
        color: {PALETTE['danger']};
        border-color: {rgba(PALETTE['danger'], 135)};
    }}
    QLabel#MessageStatusIcon[kind="question"] {{
        background: {rgba(PALETTE['primary'], 25)};
        color: {PALETTE['primary']};
        border-color: {rgba(PALETTE['primary'], 130)};
    }}
    QPushButton#MessageCloseButton {{
        background: transparent;
        color: {PALETTE['text_secondary']};
        border: 1px solid transparent;
        border-radius: 10px;
        min-width: {MIN_TARGET_SIZE}px;
        max-width: {MIN_TARGET_SIZE}px;
        min-height: {MIN_TARGET_SIZE}px;
        max-height: {MIN_TARGET_SIZE}px;
        padding: 0;
        font-size: 17px;
        font-weight: 700;
    }}
    QPushButton#MessageCloseButton:hover {{
        background: {rgba(PALETTE['danger'], 38)};
        color: {PALETTE['danger']};
        border-color: {rgba(PALETTE['danger'], 105)};
    }}
    QPushButton#MessageCloseButton:focus {{
        border: 2px solid {PALETTE['focus']};
    }}
    QFrame#MessageRule {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {rgba(PALETTE['primary'], 125)},
            stop:0.38 {rgba(PALETTE['accent'], 75)},
            stop:1 {rgba(PALETTE['border'], 0)});
        border: none;
        min-height: 1px;
        max-height: 1px;
    }}
    QTextBrowser#MessageBody {{
        background: transparent;
        color: {PALETTE['text_primary']};
        border: none;
        padding: 0;
        font-family: {FONT_FAMILY};
        font-size: 14px;
        selection-background-color: {rgba(PALETTE['primary'], 190)};
        selection-color: {PALETTE['on_primary']};
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {rgba(PALETTE['border_strong'], 175)};
        border-radius: 4px;
        min-height: 26px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {PALETTE['text_muted']};
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QPushButton#MessagePrimaryButton,
    QPushButton#MessageSecondaryButton,
    QPushButton#MessageDangerButton {{
        min-height: {MIN_TARGET_SIZE}px;
        min-width: 104px;
        padding: 8px 18px;
        border: 1px solid {PALETTE['border_strong']};
        border-radius: {RADIUS_SMALL}px;
        background: {BUTTON_SECONDARY_BG};
        color: {PALETTE['text_primary']};
        font-family: {FONT_FAMILY};
        font-size: 14px;
        font-weight: 600;
    }}
    QPushButton#MessagePrimaryButton {{
        background: {GRADIENT_PRIMARY};
        color: {PALETTE['on_primary']};
        border-color: {PALETTE['primary']};
        font-weight: 700;
    }}
    QPushButton#MessageDangerButton {{
        background: {rgba(PALETTE['danger'], 25)};
        color: {PALETTE['danger']};
        border-color: {rgba(PALETTE['danger'], 145)};
    }}
    QPushButton#MessageSecondaryButton:hover {{
        background: {BUTTON_SECONDARY_BG_HOVER};
        border-color: {PALETTE['focus']};
    }}
    QPushButton#MessagePrimaryButton:hover {{
        background: {GRADIENT_PRIMARY_HOVER};
        border-color: {PALETTE['primary_hover']};
    }}
    QPushButton#MessageDangerButton:hover {{
        background: {rgba(PALETTE['danger'], 42)};
        border-color: {PALETTE['danger']};
    }}
    QPushButton#MessagePrimaryButton:pressed {{
        background: {PALETTE['primary']};
    }}
    QPushButton#MessageSecondaryButton:pressed {{
        background: {BUTTON_SECONDARY_BG_PRESSED};
    }}
    QPushButton#MessageDangerButton:pressed {{
        background: {rgba(PALETTE['danger'], 58)};
    }}
    QPushButton#MessagePrimaryButton:focus,
    QPushButton#MessageSecondaryButton:focus,
    QPushButton#MessageDangerButton:focus {{
        border: 2px solid {PALETTE['focus']};
        padding: 7px 17px;
    }}
"""


_BUTTON_SPECS = (
    (QMessageBox.Discard, "放弃更改", "discard"),
    (QMessageBox.Abort, "中止", "abort"),
    (QMessageBox.Reset, "重置", "reset"),
    (QMessageBox.RestoreDefaults, "恢复默认", "restore_defaults"),
    (QMessageBox.Open, "打开", "open"),
    (QMessageBox.Save, "保存", "save"),
    (QMessageBox.SaveAll, "全部保存", "save_all"),
    (QMessageBox.Yes, "继续", "yes"),
    (QMessageBox.YesToAll, "全部继续", "yes_to_all"),
    (QMessageBox.Ok, "确定", "ok"),
    (QMessageBox.Retry, "重试", "retry"),
    (QMessageBox.Apply, "应用", "apply"),
    (QMessageBox.Help, "帮助", "help"),
    (QMessageBox.Ignore, "忽略", "ignore"),
    (QMessageBox.No, "取消", "no"),
    (QMessageBox.NoToAll, "全部取消", "no_to_all"),
    (QMessageBox.Cancel, "取消", "cancel"),
    (QMessageBox.Close, "关闭", "close"),
)

_DANGER_BUTTONS = {
    int(QMessageBox.Discard),
    int(QMessageBox.Abort),
    int(QMessageBox.Reset),
}

_ICON_PRESENTATION = {
    int(QMessageBox.Information): ("information", "信息", "i"),
    int(QMessageBox.Warning): ("warning", "注意", "!"),
    int(QMessageBox.Critical): ("critical", "错误", "×"),
    int(QMessageBox.Question): ("question", "确认", "?"),
    int(QMessageBox.NoIcon): ("information", "提示", "i"),
}


class MeaMessageDialog(QDialog):
    """无原生标题栏、返回 QMessageBox 标准按钮值的主题消息框。"""

    def __init__(
        self,
        parent=None,
        *,
        title: str,
        text: str,
        icon=None,
        buttons=None,
        default_button=None,
    ) -> None:
        super().__init__(parent)
        ensure_application_fonts()
        self.setObjectName("MeaMessageDialog")
        self.setWindowTitle(title)
        flags = Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        if parent is None:
            flags |= Qt.Tool
        self.setWindowFlags(flags)
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setAccessibleName(title)
        self.setAccessibleDescription("MeaPet 主题消息窗口，按 Escape 关闭")
        set_scaled_stylesheet(self, MESSAGE_DIALOG_STYLE)

        icon_value = int(
            QMessageBox.NoIcon if icon is None else icon
        )
        self.kind, kind_text, icon_text = _ICON_PRESENTATION.get(
            icon_value,
            _ICON_PRESENTATION[int(QMessageBox.NoIcon)],
        )
        self._drag_offset: QPoint | None = None
        self._positioned = False
        self._buttons: dict[int, QPushButton] = {}
        self._default_result = self._normalize_button(default_button)
        requested_buttons = (
            int(QMessageBox.Ok)
            if buttons is None or int(buttons) == int(QMessageBox.NoButton)
            else int(buttons)
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("MessageCard")
        outer.addWidget(card)
        self.card = card

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(6, 4, 12, 205))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 17, 20, 18)
        card_layout.setSpacing(12)

        header = QWidget(card)
        header.setObjectName("MessageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        status_icon = QLabel(icon_text, header)
        status_icon.setObjectName("MessageStatusIcon")
        status_icon.setProperty("kind", self.kind)
        status_icon.setAlignment(Qt.AlignCenter)
        status_icon.setAccessibleName(kind_text)
        header_layout.addWidget(status_icon, 0, Qt.AlignTop)

        heading = QVBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(1)
        kind_label = QLabel(kind_text, header)
        kind_label.setObjectName("MessageKind")
        heading.addWidget(kind_label)
        title_label = QLabel(title, header)
        title_label.setObjectName("MessageTitle")
        title_label.setWordWrap(True)
        heading.addWidget(title_label)
        header_layout.addLayout(heading, 1)

        close_button = QPushButton("×", header)
        close_button.setObjectName("MessageCloseButton")
        close_button.setFixedSize(MIN_TARGET_SIZE, MIN_TARGET_SIZE)
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.setToolTip("关闭（Esc）")
        close_button.setAccessibleName("关闭消息窗口")
        close_button.clicked.connect(self.reject)
        header_layout.addWidget(close_button, 0, Qt.AlignTop)
        card_layout.addWidget(header)

        rule = QFrame(card)
        rule.setObjectName("MessageRule")
        rule.setFrameShape(QFrame.HLine)
        card_layout.addWidget(rule)

        body = QTextBrowser(card)
        body.setObjectName("MessageBody")
        body.setPlainText(str(text or ""))
        body.setReadOnly(True)
        body.setOpenExternalLinks(False)
        body.setFrameShape(QFrame.NoFrame)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        body.document().setDocumentMargin(0)
        body.setAccessibleName("消息正文")
        body.setAccessibleDescription("可使用鼠标选择并复制")
        card_layout.addWidget(body)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 2, 0, 0)
        button_row.setSpacing(8)
        button_row.addStretch(1)
        self._create_buttons(
            button_row,
            requested_buttons=requested_buttons,
        )
        card_layout.addLayout(button_row)

        self.header = header
        self.status_icon = status_icon
        self.kind_label = kind_label
        self.title_label = title_label
        self.close_button = close_button
        self.body = body
        self._install_drag_handles(
            header,
            status_icon,
            kind_label,
            title_label,
        )

        if self._default_result is None:
            self._default_result = self._choose_default_result()
        self._apply_default_button()
        self._escape_result = self._choose_escape_result()
        self._sync_size()

    @staticmethod
    def _normalize_button(button) -> int | None:
        if button is None:
            return None
        value = int(button)
        return None if value == int(QMessageBox.NoButton) else value

    def _create_buttons(
        self,
        layout: QHBoxLayout,
        *,
        requested_buttons: int,
    ) -> None:
        for standard_button, localized_text, accessible_key in _BUTTON_SPECS:
            value = int(standard_button)
            if not requested_buttons & value:
                continue
            button = QPushButton(localized_text, self.card)
            button.setObjectName(
                "MessageDangerButton"
                if value in _DANGER_BUTTONS
                else "MessageSecondaryButton"
            )
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(MIN_TARGET_SIZE)
            button.setAccessibleName(localized_text)
            button.setAccessibleDescription(f"选择“{localized_text}”")
            button.setProperty("standardAction", accessible_key)
            button.clicked.connect(
                lambda _checked=False, result=value: self.done(result)
            )
            layout.addWidget(button)
            self._buttons[value] = button

        if not self._buttons:
            value = int(QMessageBox.Ok)
            button = QPushButton("确定", self.card)
            button.setObjectName("MessageSecondaryButton")
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(MIN_TARGET_SIZE)
            button.setAccessibleName("确定")
            button.clicked.connect(
                lambda _checked=False: self.done(value)
            )
            layout.addWidget(button)
            self._buttons[value] = button

    def _choose_default_result(self) -> int:
        safe_order = (
            QMessageBox.Cancel,
            QMessageBox.No,
            QMessageBox.NoToAll,
            QMessageBox.Abort,
            QMessageBox.Ok,
            QMessageBox.Save,
            QMessageBox.SaveAll,
            QMessageBox.Yes,
            QMessageBox.YesToAll,
            QMessageBox.Retry,
            QMessageBox.Apply,
            QMessageBox.Close,
            QMessageBox.Discard,
            QMessageBox.Reset,
            QMessageBox.RestoreDefaults,
            QMessageBox.Open,
            QMessageBox.Ignore,
            QMessageBox.Help,
        )
        for candidate in safe_order:
            if int(candidate) in self._buttons:
                return int(candidate)
        return next(iter(self._buttons))

    def _apply_default_button(self) -> None:
        if self._default_result not in self._buttons:
            self._default_result = self._choose_default_result()
        default = self._buttons[self._default_result]
        if self._default_result not in _DANGER_BUTTONS:
            default.setObjectName("MessagePrimaryButton")
        default.setDefault(True)
        default.setAutoDefault(True)

    def _choose_escape_result(self) -> int:
        for candidate in (
            QMessageBox.Cancel,
            QMessageBox.No,
            QMessageBox.NoToAll,
            QMessageBox.Close,
            QMessageBox.Abort,
            QMessageBox.Ignore,
            QMessageBox.Ok,
        ):
            if int(candidate) in self._buttons:
                return int(candidate)
        return self._default_result

    def button(self, standard_button) -> QPushButton | None:
        """兼容 ``QMessageBox.button()``，便于调用方和测试读取按钮。"""
        return self._buttons.get(int(standard_button))

    def _reference_area(self) -> QRect | None:
        parent = self.parentWidget()
        if parent is not None:
            try:
                return available_geometry_for(parent.frameGeometry())
            except RuntimeError:
                pass
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        return available_geometry_for(screen.availableGeometry())

    def _sync_size(self) -> None:
        area = self._reference_area()
        available_width = area.width() - 32 if area is not None else 520
        target_width = max(280, min(520, available_width))
        body_width = max(240, target_width - 32 - 40)

        document = self.body.document()
        document.setTextWidth(body_width)
        text_height = int(document.size().height()) + 8
        if area is not None:
            max_body_height = max(72, min(360, area.height() - 250))
        else:
            max_body_height = 360
        self.body.setFixedHeight(
            min(max(52, text_height), max_body_height)
        )

        self.setFixedWidth(target_width)
        self.layout().activate()
        self.adjustSize()
        target_height = self.sizeHint().height()
        if area is not None:
            target_height = min(target_height, max(240, area.height() - 24))
        self.setFixedHeight(target_height)

    def _install_drag_handles(self, *widgets: QWidget) -> None:
        self._drag_handles = set(widgets)
        for widget in widgets:
            widget.installEventFilter(self)
            widget.setCursor(Qt.SizeAllCursor)

    def eventFilter(self, watched, event):
        if watched in self._drag_handles:
            if (
                event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton
            ):
                self._drag_offset = (
                    event.globalPos() - self.frameGeometry().topLeft()
                )
                return True
            if (
                event.type() == QEvent.MouseMove
                and self._drag_offset is not None
                and event.buttons() & Qt.LeftButton
            ):
                target = event.globalPos() - self._drag_offset
                area = available_geometry_for(event.globalPos())
                if area is not None:
                    target = clamp_position(
                        target,
                        self.size(),
                        area,
                        margin=0,
                    )
                self.move(target)
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self._drag_offset = None
                return True
        return super().eventFilter(watched, event)

    def reject(self) -> None:
        self.done(self._escape_result)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._positioned:
            self._sync_size()
            parent = self.parentWidget()
            if parent is not None:
                anchor = parent.frameGeometry()
            else:
                area = self._reference_area()
                anchor = area if area is not None else QRect()
            area = available_geometry_for(anchor)
            if area is not None:
                self.move(
                    calculate_centered_position(
                        anchor,
                        self.size(),
                        area,
                    )
                )
            self._positioned = True
        default = self._buttons.get(self._default_result)
        if default is not None:
            QTimer.singleShot(0, self._focus_default_button)

    def _focus_default_button(self) -> None:
        try:
            default = self._buttons.get(self._default_result)
            if default is not None and self.isVisible():
                default.setFocus(Qt.OtherFocusReason)
        except RuntimeError:
            pass


def show_message_dialog(
    parent,
    *,
    title: str,
    text: str,
    icon=None,
    buttons=None,
    default_button=None,
) -> int:
    """显示主题消息窗口，并返回 ``QMessageBox.StandardButton`` 整数值。"""
    dialog = MeaMessageDialog(
        parent,
        title=title,
        text=text,
        icon=icon,
        buttons=buttons,
        default_button=default_button,
    )
    return dialog.exec_()
