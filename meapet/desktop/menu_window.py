"""右键菜单的独立窗口实现：根菜单可拖动，子菜单向侧边级联。

`PetWindowChromeMixin._build_context_menu()` 仍然产出标准 `QMenu`（结构与文案
的唯一来源），本模块把这棵菜单树渲染成一组独立顶层窗口：

- 根菜单不是 popup，失焦不会自动关闭，因此可以拖动到任意位置；
- 根菜单只显示第一层，点击分组时按需在侧边打开独立面板；
- 更深层的分组继续向同一侧级联；空间不足时整条级联自动换向；
- 点击叶子动作后，先关闭整组菜单窗口，再执行原始 `QAction`。
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt5.QtGui import QColor
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

from meapet.desktop.screen_geometry import available_geometry_for, clamp_position
from meapet.desktop.theme import PET_MENU_WINDOW_STYLE
from meapet.ui_theme import ensure_application_fonts, set_scaled_stylesheet


SHADOW_MARGIN = 10
MIN_CARD_WIDTH = 248
MIN_SUBMENU_CARD_WIDTH = 220
# 两个顶层窗口的透明阴影区互相覆盖；实际卡片之间仍保留约 10px 呼吸空间。
SIDE_WINDOW_OVERLAP = SHADOW_MARGIN


def calculate_submenu_position(
    parent_rect: QRect,
    trigger_rect: QRect,
    panel_size: QSize,
    area: QRect | None,
    *,
    preferred_side: str = "right",
    overlap: int = SIDE_WINDOW_OVERLAP,
    lock_side: bool = False,
) -> tuple[QPoint, str]:
    """计算级联面板位置，并返回 ``(左上角, 实际展开方向)``。

    水平方向只在首选侧放不下时换边，纵向则始终与触发按钮的卡片顶部对齐，
    再夹回屏幕可用区域。这样靠近屏幕右缘时会向左展开，而更深层面板会继承
    已选方向，避免折返并盖住上一级菜单。
    """
    preferred = "left" if preferred_side == "left" else "right"
    fallback = "right" if preferred == "left" else "left"

    x_for_side = {
        "right": parent_rect.right() + 1 - overlap,
        "left": parent_rect.left() - panel_size.width() + overlap,
    }
    target_y = trigger_rect.top() - SHADOW_MARGIN

    if area is None or area.isEmpty():
        return QPoint(x_for_side[preferred], target_y), preferred

    def fits_horizontally(side: str) -> bool:
        left = x_for_side[side]
        right = left + panel_size.width() - 1
        return left >= area.left() and right <= area.right()

    if lock_side:
        # 深层级联保持整条菜单朝同一方向展开；若屏幕极窄，最终位置仍由
        # clamp_position 夹回可用区，但不会折返盖住上一级。
        side = preferred
    elif fits_horizontally(preferred):
        side = preferred
    elif fits_horizontally(fallback):
        side = fallback
    else:
        # 极窄屏幕上两侧都放不下时，选择可用空间更多的一侧，再整体夹回屏幕。
        right_space = area.right() - parent_rect.right()
        left_space = parent_rect.left() - area.left()
        side = "right" if right_space >= left_space else "left"

    origin = QPoint(x_for_side[side], target_y)
    return clamp_position(origin, panel_size, area, margin=0), side


def _refresh_dynamic_style(widget: QWidget) -> None:
    """让 Qt 立即重新匹配动态属性选择器。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class PetSubmenuPanel(QWidget):
    """一个按需创建的侧向子菜单面板。"""

    def __init__(
        self,
        owner: "PetMenuWindow",
        menu,
        title: str,
        *,
        level: int,
        trigger_button: QPushButton,
        parent_surface: QWidget,
    ):
        # 顶层工具窗必须没有 QWidget 父级：部分平台会把“有父级的 Tool.move()”
        # 解释成父窗口相对坐标，导致面板实际叠回根菜单。生命周期由 owner 显式管理。
        super().__init__(None)
        self.setObjectName("PetMenuSubmenuRoot")
        self.setWindowTitle(f"{title} — MeaPet 菜单")
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAccessibleName(f"{title} 子菜单")
        self.setAccessibleDescription("独立侧边子菜单，按 Esc 收起当前层级")
        set_scaled_stylesheet(self, PET_MENU_WINDOW_STYLE)

        self._owner = owner
        self._source_menu = menu
        self._title = title
        self.level = level
        self.trigger_button = trigger_button
        self.parent_surface = parent_surface
        self.placement_side = "right"
        self._closing_from_owner = False
        self._requested_pos = QPoint()
        self._menu_groups: list[tuple[QPushButton, object, str]] = []
        self._menu_actions: list[tuple[QPushButton, object]] = []

        self._build_ui(menu, title)

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
        card_layout.setSpacing(5)

        header = QWidget(card)
        header.setObjectName("PetMenuSubmenuHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 2, 6, 3)
        header_layout.setSpacing(8)
        title_label = QLabel(title, header)
        title_label.setObjectName("PetMenuSubmenuTitle")
        header_layout.addWidget(title_label, 1)
        hint = QLabel("Esc 收起", header)
        hint.setObjectName("PetMenuSubmenuHint")
        header_layout.addWidget(hint)
        card_layout.addWidget(header)
        self._header = header

        body = QWidget(card)
        body.setObjectName("PetMenuBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(1)
        self._owner._populate(menu, body_layout, surface=self)
        body_layout.addStretch(1)

        scroll = QScrollArea(card)
        scroll.setObjectName("PetMenuScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        card_layout.addWidget(scroll, 1)
        self._scroll = scroll

        self.sync_size()

    def sync_size(self) -> None:
        body = self._scroll.widget()
        body.adjustSize()
        content_height = body.sizeHint().height()
        header_height = self._header.sizeHint().height()
        card_width = max(
            MIN_SUBMENU_CARD_WIDTH,
            body.sizeHint().width() + 16,
            self._header.sizeHint().width() + 16,
        )
        width = card_width + SHADOW_MARGIN * 2
        chrome = 8 * 2 + 5 + SHADOW_MARGIN * 2 + 2

        max_height = 720
        area = available_geometry_for(self.parent_surface)
        if area is not None and area.height() > 0:
            max_height = int(area.height() * 0.85)
        height = min(content_height + header_height + chrome, max_height)
        self.setFixedSize(width, height)

    def show_at(self, global_pos: QPoint) -> None:
        # 首次 show 时部分窗口管理器会为无父级 Tool 重新选位，因此显示前后各
        # 应用一次目标坐标，并在本轮事件完成后再校正一次。
        self.move_to(global_pos)
        self.show()
        self.move_to(global_pos)
        self.raise_()
        QTimer.singleShot(0, self._restore_requested_position)

    def move_to(self, global_pos: QPoint) -> None:
        self._requested_pos = QPoint(global_pos)
        self.move(self._requested_pos)

    def _restore_requested_position(self) -> None:
        try:
            if self.isVisible():
                self.move(self._requested_pos)
        except RuntimeError:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._owner._close_submenus_from(self.level)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._owner.close()
            event.accept()
            return
        super().mousePressEvent(event)

    def closeEvent(self, event):
        if self._closing_from_owner:
            super().closeEvent(event)
            return
        # Alt+F4 等系统关闭入口也只收起这一层及其后代。
        event.ignore()
        self._owner._close_submenus_from(self.level)

    def _dispose_from_owner(self) -> None:
        self._closing_from_owner = True
        self.close()
        self.deleteLater()


class PetMenuWindow(QWidget):
    """把一棵 `QMenu` 渲染成可拖动、可侧向级联的独立窗口。"""

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
        self._groups: list[tuple[QPushButton, object, str]] = []
        self._action_buttons: list[tuple[QPushButton, object]] = []
        self._menu_groups: list[tuple[QPushButton, object, str]] = []
        self._menu_actions: list[tuple[QPushButton, object]] = []
        self._submenu_panels: list[PetSubmenuPanel] = []

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
        self._populate(menu, self._body_layout, surface=self)
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
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.setToolTip("关闭菜单（Esc）")
        close_button.setAccessibleName("关闭菜单")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button, 0, Qt.AlignTop)

        self._header = header
        self.close_button = close_button
        return header

    def _populate(self, menu, layout: QVBoxLayout, *, surface: QWidget) -> None:
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
                layout.addWidget(
                    self._make_group_button(action, submenu, surface=surface)
                )
                continue

            layout.addWidget(self._make_action_button(action, surface=surface))

    def _make_group_button(
        self, action, submenu, *, surface: QWidget
    ) -> QPushButton:
        title = action.text()
        button = QPushButton(f"▸  {title}")
        button.setObjectName("PetMenuGroup")
        button.setCursor(Qt.PointingHandCursor)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setAccessibleName(f"{title} 子菜单")
        button.setAccessibleDescription("点击后在当前菜单侧边打开")
        button.setProperty("expanded", "false")

        def open_submenu(_checked=False) -> None:
            self._toggle_submenu(
                button,
                submenu,
                title,
                parent_surface=surface,
            )

        button.clicked.connect(open_submenu)

        entry = (button, submenu, title)
        self._groups.append(entry)
        surface._menu_groups.append(entry)
        return button

    def _make_action_button(self, action, *, surface: QWidget) -> QPushButton:
        button = QPushButton(action.text())
        button.setObjectName("PetMenuItem")
        button.setCursor(Qt.PointingHandCursor)
        button.setEnabled(action.isEnabled())
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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

        entry = (button, action)
        self._action_buttons.append(entry)
        surface._menu_actions.append(entry)
        return button

    # -------------------------------------------------------------- behavior
    @staticmethod
    def _set_group_expanded(
        button: QPushButton, title: str, expanded: bool, side: str = "right"
    ) -> None:
        arrow = "◂" if expanded and side == "left" else "▸"
        button.setText(f"{arrow}  {title}")
        button.setProperty("expanded", "true" if expanded else "false")
        if not expanded:
            description = "点击后在当前菜单侧边打开"
        elif side == "left":
            description = "已在左侧打开，点击收起"
        else:
            description = "已在右侧打开，点击收起"
        button.setAccessibleDescription(description)
        _refresh_dynamic_style(button)

    def _toggle_submenu(
        self,
        button: QPushButton,
        submenu,
        title: str,
        *,
        parent_surface: QWidget,
    ) -> None:
        level = (
            parent_surface.level + 1
            if isinstance(parent_surface, PetSubmenuPanel)
            else 0
        )
        current = (
            self._submenu_panels[level]
            if level < len(self._submenu_panels)
            else None
        )
        if current is not None and current.trigger_button is button:
            self._close_submenus_from(level)
            return

        self._close_submenus_from(level)
        panel = PetSubmenuPanel(
            self,
            submenu,
            title,
            level=level,
            trigger_button=button,
            parent_surface=parent_surface,
        )
        self._submenu_panels.append(panel)
        target, side = self._panel_target(panel)
        panel.placement_side = side
        self._set_group_expanded(button, title, True, side)
        panel.show_at(target)

    def _panel_target(self, panel: PetSubmenuPanel) -> tuple[QPoint, str]:
        parent = panel.parent_surface
        # 两个窗口都是 Frameless；geometry() 是稳定的全局客户区坐标，避免某些
        # Qt 后端仍为 frameGeometry() 注入不存在的系统标题栏尺寸。
        parent_rect = parent.geometry()
        top_left = panel.trigger_button.mapToGlobal(QPoint(0, 0))
        trigger_rect = QRect(top_left, panel.trigger_button.size())
        preferred = (
            parent.placement_side
            if isinstance(parent, PetSubmenuPanel)
            else "right"
        )
        area = available_geometry_for(parent_rect)
        return calculate_submenu_position(
            parent_rect,
            trigger_rect,
            panel.size(),
            area,
            preferred_side=preferred,
            lock_side=isinstance(parent, PetSubmenuPanel),
        )

    def _reposition_submenus(self) -> None:
        for panel in tuple(self._submenu_panels):
            try:
                target, side = self._panel_target(panel)
                old_side = panel.placement_side
                panel.placement_side = side
                if side != old_side:
                    self._set_group_expanded(
                        panel.trigger_button, panel._title, True, side
                    )
                panel.move_to(target)
            except RuntimeError:
                # Qt 已在事件队列中销毁窗口时无需继续重排。
                break

    def _unregister_panel_entries(self, panel: PetSubmenuPanel) -> None:
        for entry in panel._menu_groups:
            if entry in self._groups:
                self._groups.remove(entry)
        for entry in panel._menu_actions:
            if entry in self._action_buttons:
                self._action_buttons.remove(entry)

    def _close_submenus_from(self, level: int) -> None:
        if level < 0:
            level = 0
        panels = self._submenu_panels[level:]
        del self._submenu_panels[level:]
        for panel in reversed(panels):
            self._set_group_expanded(
                panel.trigger_button, panel._title, False
            )
            self._unregister_panel_entries(panel)
            panel._dispose_from_owner()

    def _activate(self, action) -> None:
        if not action.isEnabled():
            return
        self.close()
        # 让整组菜单窗口先收起，再执行动作（部分动作会弹出模态对话框）。
        QTimer.singleShot(0, action.trigger)

    def sync_size(self) -> None:
        """按第一层内容计算根窗口大小，并限制在屏幕可用高度内。"""
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
            self.move(self._clamped_pos(self.pos()))
            self._reposition_submenus()

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
            self._reposition_submenus()
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

    def closeEvent(self, event):
        self._close_submenus_from(0)
        super().closeEvent(event)
