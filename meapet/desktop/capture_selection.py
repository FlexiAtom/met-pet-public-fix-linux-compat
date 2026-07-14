"""本地截图区域拖选层；只返回坐标，不采集或保存画面。"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QKeyEvent, QMouseEvent, QPaintEvent, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QDialog

from meapet.ui_theme import PALETTE, ensure_application_fonts


def virtual_desktop_geometry(screens=None) -> QRect:
    """返回全部显示器的联合逻辑坐标，保留左侧/上方屏幕的负坐标。"""
    available = tuple(screens if screens is not None else QApplication.screens())
    if not available:
        return QRect(0, 0, 1, 1)
    result = QRect(available[0].geometry())
    for screen in available[1:]:
        result = result.united(screen.geometry())
    return result


def region_from_local_rect(
    local_rect: QRect,
    desktop_geometry: QRect,
) -> dict[str, int] | None:
    """把拖选层局部矩形映射回 ImageGrab 使用的虚拟桌面坐标。"""
    normalized = QRect(local_rect).normalized()
    if normalized.width() < 2 or normalized.height() < 2:
        return None
    return {
        "x": desktop_geometry.x() + normalized.x(),
        "y": desktop_geometry.y() + normalized.y(),
        "width": normalized.width(),
        "height": normalized.height(),
    }


class ScreenRegionSelector(QDialog):
    """仿系统截图工具的全桌面矩形拖选层。"""

    def __init__(
        self,
        parent=None,
        *,
        desktop_geometry: QRect | None = None,
        initial_region: dict | None = None,
    ) -> None:
        # 多屏顶层窗口不能受隐藏父窗口的几何范围约束。
        super().__init__(None)
        ensure_application_fonts()
        self.setObjectName("ScreenRegionSelector")
        self.setWindowTitle("拖拽选择截图区域")
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName("拖拽选择截图区域")
        self.setAccessibleDescription(
            "按住鼠标左键拖出矩形，松开完成；Escape 或鼠标右键取消"
        )

        self.desktop_geometry = QRect(
            desktop_geometry or virtual_desktop_geometry()
        )
        self.setGeometry(self.desktop_geometry)
        self._drag_origin: QPoint | None = None
        self._drag_current: QPoint | None = None
        self._selected_region: dict[str, int] | None = None
        self._initial_rect = self._local_rect_from_region(initial_region)

    @property
    def selected_region(self) -> dict[str, int] | None:
        return dict(self._selected_region) if self._selected_region else None

    def _local_rect_from_region(self, region: object) -> QRect:
        if not isinstance(region, dict):
            return QRect()
        try:
            rect = QRect(
                int(region["x"]) - self.desktop_geometry.x(),
                int(region["y"]) - self.desktop_geometry.y(),
                int(region["width"]),
                int(region["height"]),
            )
        except (KeyError, TypeError, ValueError):
            return QRect()
        return rect.intersected(self.rect())

    def _selection_rect(self) -> QRect:
        if self._drag_origin is not None and self._drag_current is not None:
            return QRect(self._drag_origin, self._drag_current).normalized()
        return QRect(self._initial_rect)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            self.reject()
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        self._initial_rect = QRect()
        self._drag_origin = event.pos()
        self._drag_current = event.pos()
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is None:
            event.ignore()
            return
        self._drag_current = event.pos()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or self._drag_origin is None:
            event.ignore()
            return
        self._drag_current = event.pos()
        region = region_from_local_rect(
            self._selection_rect(),
            self.desktop_geometry,
        )
        if region is None:
            self._drag_origin = None
            self._drag_current = None
            self.update()
            event.accept()
            return
        self._selected_region = region
        event.accept()
        super().accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.reject()
            event.accept()
            return
        # Enter 不应把旧区域当作本次新的明确拖选。
        event.ignore()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        selection = self._selection_rect().intersected(self.rect())

        dim = QColor(PALETTE["canvas"])
        dim.setAlpha(178)
        if selection.isEmpty():
            painter.fillRect(self.rect(), dim)
        else:
            painter.fillRect(QRect(0, 0, self.width(), selection.top()), dim)
            painter.fillRect(
                QRect(
                    0,
                    selection.bottom() + 1,
                    self.width(),
                    max(0, self.height() - selection.bottom() - 1),
                ),
                dim,
            )
            painter.fillRect(
                QRect(0, selection.top(), selection.left(), selection.height()),
                dim,
            )
            painter.fillRect(
                QRect(
                    selection.right() + 1,
                    selection.top(),
                    max(0, self.width() - selection.right() - 1),
                    selection.height(),
                ),
                dim,
            )
            painter.setPen(QPen(QColor(PALETTE["focus"]), 2))
            painter.drawRect(selection.adjusted(1, 1, -1, -1))

        instruction = "按住鼠标左键拖选区域 · Esc / 右键取消"
        if not selection.isEmpty():
            instruction += f" · {selection.width()} × {selection.height()}"
        box_width = min(max(320, painter.fontMetrics().horizontalAdvance(instruction) + 32), max(320, self.width() - 32))
        box = QRect(max(16, (self.width() - box_width) // 2), 20, box_width, 44)
        background = QColor(PALETTE["surface"])
        background.setAlpha(235)
        painter.setPen(QPen(QColor(PALETTE["border_strong"]), 1))
        painter.setBrush(background)
        painter.drawRoundedRect(box, 10, 10)
        painter.setPen(QColor(PALETTE["text_primary"]))
        painter.drawText(box.adjusted(12, 0, -12, 0), Qt.AlignCenter, instruction)


def select_screen_region(
    parent=None,
    initial_region: dict | None = None,
) -> dict[str, int] | None:
    """运行一次拖选；确认框由调用方暂时隐藏，倒计时也由调用方暂停。"""
    del parent
    selector = ScreenRegionSelector(initial_region=initial_region)
    if selector.exec_() != QDialog.Accepted:
        return None
    return selector.selected_region


__all__ = [
    "ScreenRegionSelector",
    "region_from_local_rect",
    "select_screen_region",
    "virtual_desktop_geometry",
]
