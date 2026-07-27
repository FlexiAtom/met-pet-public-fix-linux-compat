"""右键菜单的独立窗口实现：可自由拖动、分组可折叠。

`PetWindowChromeMixin._build_context_menu()` 仍然产出标准 `QMenu`（结构与文案
的唯一来源），本模块把这棵菜单树渲染成一个独立顶层窗口：

- 不是弹出式 popup，失焦不会自动关闭，因此可以随意拖动到任意位置；
- 子菜单就地折叠/展开，不再弹出二级 popup，移动窗口时不会丢失层级。
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QSize, Qt, QTimer
from PyQt5.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from meapet.desktop.icons import standard_icon
from meapet.desktop.screen_geometry import available_geometry_for, clamp_position
from meapet.desktop.theme import COLOR_ACCENT, PET_MENU_WINDOW_STYLE
from meapet.ui_theme import ensure_application_fonts, set_scaled_stylesheet


SHADOW_MARGIN = 10
MIN_CARD_WIDTH = 248
INDENT_STEP = 14
CHECK_ICON_SIZE = 16


def _check_icon(checked: bool) -> QIcon:
    """勾选标记图标；未选中时返回等宽透明图标，保证各行文字对齐。"""
    pixmap = QPixmap(CHECK_ICON_SIZE, CHECK_ICON_SIZE)
    pixmap.fill(QColor(0, 0, 0, 0))
    if checked:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(COLOR_ACCENT))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(3, 8, 6, 12)
        painter.drawLine(6, 12, 13, 4)
        painter.end()
    return QIcon(pixmap)


class PetMenuWindow(QWidget):
    """把一棵 `QMenu` 渲染成可拖动的独立窗口。"""

    def __init__(self, menu, parent=None, title: str = "梅尔 · 菜单"):
        super().__init__(parent)
        ensure_application_fonts()
        self.setObjectName("PetMenuWindowRoot")
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAccessibleName("MeaPet 操作菜单")
        self.setAccessibleDescription("可拖动的桌宠菜单窗口，按 Esc 关闭")
        set_scaled_stylesheet(self, PET_MENU_WINDOW_STYLE)

        self._source_menu = menu
        self._drag_offset: QPoint | None = None
        self._groups: list[tuple[QPushButton, QWidget, str]] = []
        self._action_buttons: list[tuple[QPushButton, object]] = []

        self._build_ui(menu, title)

    # ---------------------------------------------------------------- build
    def _build_ui(self, menu, title: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN
        )
        outer.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("PetMenuCard")
        outer.addWidget(card)
        self._card = card

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(6)
        card_layout.addWidget(self._build_header(title))

        body = QWidget(card)
        body.setObjectName("PetMenuBody")
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(1)
        self._populate(menu, self._body_layout, depth=0)
        self._body_layout.addStretch(1)

        scroll = QScrollArea(card)
        scroll.setObjectName("PetMenuScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        card_layout.addWidget(scroll, 1)
        self._scroll = scroll

        self.sync_size()

    def _build_header(self, title: str) -> QWidget:
        header = QWidget(self)
        header.setObjectName("PetMenuHeader")
        header.setCursor(Qt.SizeAllCursor)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(6, 2, 2, 2)
        layout.setSpacing(8)

        heading = QVBoxLayout()
        heading.setSpacing(0)
        title_label = QLabel(title, header)
        title_label.setObjectName("PetMenuTitle")
        heading.addWidget(title_label)
        hint = QLabel("拖动标题栏可移动", header)
        hint.setObjectName("PetMenuHint")
        heading.addWidget(hint)
        layout.addLayout(heading, 1)

        close_button = QPushButton(header)
        close_button.setObjectName("PetMenuCloseButton")
        close_button.setText("✕")
        close_button.setStyleSheet("color: white; font-weight: bold; font-size: 18px;")
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.setToolTip("关闭菜单（Esc）")
        close_button.setAccessibleName("关闭菜单")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button, 0, Qt.AlignTop)

        self._header = header
        self.close_button = close_button
        return header

    def _populate(self, menu, layout: QVBoxLayout, depth: int) -> None:
        for action in menu.actions():
            if action.isSeparator():
                line = QFrame()
                line.setObjectName("PetMenuSeparator")
                line.setFrameShape(QFrame.HLine)
                line.setFixedHeight(1)
                layout.addWidget(line)
                continue

            submenu = action.menu()
            if submenu is not None:
                self._add_group(action, submenu, layout, depth)
                continue

            layout.addWidget(self._make_action_button(action, depth))

    def _add_group(self, action, submenu, layout: QVBoxLayout, depth: int) -> None:
        title = action.text()
        toggle = QPushButton(f"▸  {title}")
        toggle.setObjectName("PetMenuGroup")
        toggle.setCursor(Qt.PointingHandCursor)
        toggle.setIcon(action.icon() if not action.icon().isNull() else _check_icon(False))
        toggle.setIconSize(QSize(CHECK_ICON_SIZE, CHECK_ICON_SIZE))
        toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        toggle.setAccessibleName(f"{title} 分组")
        toggle.setStyleSheet(f"padding-left: {12 + depth * INDENT_STEP}px;")
        layout.addWidget(toggle)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(1)
        self._populate(submenu, container_layout, depth + 1)
        container.setVisible(False)
        layout.addWidget(container)

        toggle.clicked.connect(
            lambda _checked=False, b=toggle, c=container, t=title: self._toggle_group(
                b, c, t
            )
        )
        self._groups.append((toggle, container, title))

    def _make_action_button(self, action, depth: int) -> QPushButton:
        button = QPushButton(action.text())
        button.setObjectName("PetMenuItem")
        if action.isCheckable():
            button.setIcon(_check_icon(action.isChecked()))
        elif not action.icon().isNull():
            button.setIcon(action.icon())
        else:
            # 占位透明图标：保证有无图标的行文字左边缘一致。
            button.setIcon(_check_icon(False))
        button.setIconSize(QSize(CHECK_ICON_SIZE, CHECK_ICON_SIZE))
        button.setCursor(Qt.PointingHandCursor)
        button.setEnabled(action.isEnabled())
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setStyleSheet(f"padding-left: {12 + depth * INDENT_STEP}px;")
        tooltip = action.toolTip()
        if tooltip and tooltip != action.text():
            button.setToolTip(tooltip)
        accessible = action.text()
        if action.isCheckable():
            accessible += " · 已选中" if action.isChecked() else " · 未选中"
        button.setAccessibleName(accessible)
        if action.objectName() == "DangerAction":
            button.setProperty("danger", "true")
        button.clicked.connect(lambda _checked=False, a=action: self._activate(a))
        self._action_buttons.append((button, action))
        return button

    # -------------------------------------------------------------- behavior
    def _toggle_group(self, button: QPushButton, container: QWidget, title: str) -> None:
        expanded = not container.isVisible()
        container.setVisible(expanded)
        button.setText(f"{'▾' if expanded else '▸'}  {title}")
        self.sync_size()

    def _activate(self, action) -> None:
        if not action.isEnabled():
            return
        self.close()
        # 让菜单窗口先收起，再执行动作（部分动作会弹出模态对话框）。
        QTimer.singleShot(0, action.trigger)

    def sync_size(self) -> None:
        """按内容重新计算窗口大小，并限制在屏幕可用高度内。"""
        body = self._scroll.widget()
        body.adjustSize()
        content_height = body.sizeHint().height()
        header_height = self._header.sizeHint().height()
        chrome = 8 * 2 + 6 + SHADOW_MARGIN * 2 + 2
        width = max(MIN_CARD_WIDTH, body.sizeHint().width()) + SHADOW_MARGIN * 2 + 20

        max_height = 720
        area = available_geometry_for(self)
        if area is not None and area.height() > 0:
            max_height = int(area.height() * 0.85)
        height = min(content_height + header_height + chrome, max_height)
        self.setFixedWidth(width)
        self.setFixedHeight(height)
        if self.isVisible():
            # 展开分组后窗口变高，避免整体被挤出屏幕。
            self.move(self._clamped_pos(self.pos()))

    def _clamped_pos(self, global_pos: QPoint) -> QPoint:
        """把目标坐标夹到当前屏幕可用区域内。"""
        size = QSize(self.width(), self.height())
        area = available_geometry_for(global_pos)
        if area is None:
            return QPoint(global_pos)
        # 菜单自带阴影留白，无需再额外空出屏幕边距。
        return clamp_position(global_pos, size, area, margin=0)

    def show_at(self, global_pos: QPoint) -> None:
        """在指定全局坐标显示，并保证完整落在屏幕可用区域内。"""
        self.sync_size()
        self.move(self._clamped_pos(global_pos))
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------- dragging
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self.close()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
