"""Live2D 顶层窗口视口与模型站立锚点的可视化编辑控件。"""

from __future__ import annotations

from collections.abc import Iterable

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meapet.config.defaults import (
    DEFAULT_LIVE2D_PLACEMENT_ANCHOR,
    DEFAULT_LIVE2D_WINDOW_MASK,
    DEFAULT_LIVE2D_WINDOW_SHAPE,
)
from meapet.config.store import (
    MAX_LIVE2D_WINDOW_SHAPE_CONTOURS,
    MAX_LIVE2D_WINDOW_SHAPE_POINTS,
    normalize_live2d_placement_anchor,
    normalize_live2d_window_mask,
    normalize_live2d_window_shape,
)
from meapet.ui_theme import MIN_TARGET_SIZE, PALETTE, get_ui_font_scale


MIN_VIEWPORT_SPAN = 0.20
_EDGE_PRECISION = 6
_HANDLE_HIT_RADIUS = 22.0
_HANDLE_VISUAL_RADIUS = 6.0
_CANVAS_MARGIN = 18.0
_LASSO_DRAG_THRESHOLD = 4.0
_LASSO_SAMPLE_SPACING = 6.0


def _clamp_unit(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number != number:  # NaN
        number = 0.0
    return max(0.0, min(1.0, number))


def _constrain_span(start: object, end: object, minimum: float) -> tuple[float, float]:
    start = _clamp_unit(start)
    end = _clamp_unit(end)
    if start > end:
        start, end = end, start
    if end - start >= minimum:
        return start, end

    center = (start + end) / 2.0
    start = center - minimum / 2.0
    end = center + minimum / 2.0
    if start < 0.0:
        end -= start
        start = 0.0
    if end > 1.0:
        start -= end - 1.0
        end = 1.0
    return max(0.0, start), min(1.0, end)


def constrain_viewport_edges(
    left: object,
    top: object,
    right: object,
    bottom: object,
    *,
    minimum_span: float = MIN_VIEWPORT_SPAN,
) -> tuple[float, float, float, float]:
    """把任意四边收敛到完整画布内，并保留可操作的最小尺寸。"""
    minimum = max(0.01, min(1.0, float(minimum_span)))
    constrained_left, constrained_right = _constrain_span(
        left,
        right,
        minimum,
    )
    constrained_top, constrained_bottom = _constrain_span(
        top,
        bottom,
        minimum,
    )
    return tuple(
        round(value, _EDGE_PRECISION)
        for value in (
            constrained_left,
            constrained_top,
            constrained_right,
            constrained_bottom,
        )
    )


def window_mask_to_viewport_edges(value: object) -> tuple[float, float, float, float]:
    """把历史中心/半径配置转换成矩形四边（完整画布归一化坐标）。"""
    mask = normalize_live2d_window_mask(value)
    return tuple(
        round(edge, _EDGE_PRECISION)
        for edge in (
            max(0.0, mask["cx"] - mask["rw"]),
            max(0.0, mask["cy"] - mask["rh"]),
            min(1.0, mask["cx"] + mask["rw"]),
            min(1.0, mask["cy"] + mask["rh"]),
        )
    )


def viewport_edges_to_window_mask(
    left: object,
    top: object,
    right: object,
    bottom: object,
    *,
    enabled: bool,
) -> dict:
    """把矩形四边转换回兼容现有运行时的 ``window_mask``。"""
    left, top, right, bottom = constrain_viewport_edges(
        left,
        top,
        right,
        bottom,
    )
    return {
        "enabled": bool(enabled),
        "cx": round((left + right) / 2.0, _EDGE_PRECISION),
        "cy": round((top + bottom) / 2.0, _EDGE_PRECISION),
        "rw": round((right - left) / 2.0, _EDGE_PRECISION),
        "rh": round((bottom - top) / 2.0, _EDGE_PRECISION),
    }


class Live2DViewportEditor(QWidget):
    """在完整 Live2D 帧上编辑矩形视觉视口与模型站立锚点。"""

    viewportChanged = pyqtSignal(float, float, float, float)
    placementAnchorChanged = pyqtSignal(float, float)
    windowShapeChanged = pyqtSignal(object)
    shapeStateChanged = pyqtSignal()

    def __init__(self, preview: QImage | QPixmap | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Live2DViewportEditor")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(300)
        self.setAccessibleName("Live2D 窗口范围预览")
        self.setAccessibleDescription(
            "拖动选框移动窗口范围，拖动边缘或角点缩放；"
            "十字标记可直接拖动为模型站立锚点；启用形状工具后可逐点绘制"
            "保留区或挖空区；方向键移动最后操作的锚点或窗口范围，"
            "下面的百分比数值可精确调整"
        )

        self._preview = QPixmap()
        self._fallback_canvas_size = QSize(525, 735)
        self._edges = window_mask_to_viewport_edges(DEFAULT_LIVE2D_WINDOW_MASK)
        self._anchor = QPointF(
            DEFAULT_LIVE2D_PLACEMENT_ANCHOR["x"],
            DEFAULT_LIVE2D_PLACEMENT_ANCHOR["y"],
        )
        self._crop_enabled = True
        self._keyboard_target = "viewport"
        self._window_shape = normalize_live2d_window_shape(
            DEFAULT_LIVE2D_WINDOW_SHAPE
        )
        self._shape_tool: str | None = None
        self._shape_tool_persistent = False
        self._draft_shape_points: list[QPointF] = []
        self._shape_press_position = QPointF()
        self._shape_freehand_active = False
        self._geometry_editable = True
        self._anchor_drag_offset = QPointF()
        self._drag_mode: str | None = None
        self._drag_origin = QPointF()
        self._drag_origin_edges = self._edges
        self._new_selection_started = False
        self.set_preview(preview)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(560, 360)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(300, 260)

    def has_preview(self) -> bool:
        return not self._preview.isNull()

    def preview_pixmap(self) -> QPixmap:
        return QPixmap(self._preview)

    def fallback_canvas_size(self) -> QSize:
        return QSize(self._fallback_canvas_size)

    def set_preview(self, preview: QImage | QPixmap | None) -> None:
        if isinstance(preview, QImage) and not preview.isNull():
            self._preview = QPixmap.fromImage(preview)
        elif isinstance(preview, QPixmap) and not preview.isNull():
            self._preview = QPixmap(preview)
        else:
            self._preview = QPixmap()
        self.update()

    def set_fallback_canvas_size(self, width: object, height: object) -> None:
        try:
            canvas_width = max(1, int(width))
            canvas_height = max(1, int(height))
        except (TypeError, ValueError):
            canvas_width, canvas_height = 525, 735
        self._fallback_canvas_size = QSize(canvas_width, canvas_height)
        self.update()

    def viewport(self) -> tuple[float, float, float, float]:
        return self._edges

    def placement_anchor(self) -> dict:
        return {
            "x": round(self._anchor.x(), _EDGE_PRECISION),
            "y": round(self._anchor.y(), _EDGE_PRECISION),
        }

    def set_placement_anchor(
        self,
        value: object,
        *,
        emit: bool = False,
    ) -> None:
        anchor = normalize_live2d_placement_anchor(value)
        point = QPointF(anchor["x"], anchor["y"])
        if point == self._anchor:
            return
        self._anchor = point
        self.update()
        if emit:
            self.placementAnchorChanged.emit(point.x(), point.y())

    def set_crop_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._crop_enabled:
            return
        self._crop_enabled = enabled
        if not enabled and self._drag_mode != "anchor":
            self._drag_mode = None
        self.update()

    def window_shape(self) -> dict:
        return normalize_live2d_window_shape(self._window_shape)

    def set_window_shape(self, value: object, *, emit: bool = False) -> None:
        shape = normalize_live2d_window_shape(value)
        if shape == self._window_shape:
            return
        self._window_shape = shape
        self._draft_shape_points = []
        self._shape_tool = None
        self._shape_freehand_active = False
        self._drag_mode = None
        self.update()
        self.shapeStateChanged.emit()
        if emit:
            self.windowShapeChanged.emit(self.window_shape())

    def set_shape_enabled(self, enabled: bool, *, emit: bool = False) -> None:
        enabled = bool(enabled)
        if enabled == self._window_shape["enabled"]:
            return
        self._window_shape["enabled"] = enabled
        if not enabled:
            self._shape_tool = None
            self._draft_shape_points = []
            self._shape_freehand_active = False
            self._drag_mode = None
        self.update()
        self.shapeStateChanged.emit()
        if emit:
            self.windowShapeChanged.emit(self.window_shape())

    def shape_tool(self) -> str | None:
        return self._shape_tool

    def draft_shape_point_count(self) -> int:
        return len(self._draft_shape_points)

    def set_shape_tool_persistent(self, enabled: bool) -> None:
        """完成轮廓后是否保留当前工具，便于连续套索多个区域。"""
        self._shape_tool_persistent = bool(enabled)

    def set_geometry_editable(self, enabled: bool) -> None:
        """控制矩形与锚点是否可编辑；形状对话框会关闭这组交互。"""
        enabled = bool(enabled)
        if enabled == self._geometry_editable:
            return
        self._geometry_editable = enabled
        self._drag_mode = None
        self._shape_freehand_active = False
        self.update()

    def set_shape_tool(self, operation: str | None) -> None:
        normalized = (
            operation
            if self._window_shape["enabled"]
            and operation in ("add", "subtract")
            else None
        )
        if normalized == self._shape_tool:
            if self._shape_tool_persistent:
                # 独立编辑器把工具视为模式；重复点击只保持选中，取消草稿
                # 使用 Escape，避免连续画多个区域时意外关掉工具。
                self.shapeStateChanged.emit()
                return
            normalized = None
        if normalized == self._shape_tool:
            return
        self._shape_tool = normalized
        self._draft_shape_points = []
        self._drag_mode = None
        self.update()
        self.shapeStateChanged.emit()

    def _append_shape_point(self, point: QPointF) -> bool:
        """加入一个归一化轮廓点，并过滤采样产生的连续重复点。"""
        if len(self._draft_shape_points) >= MAX_LIVE2D_WINDOW_SHAPE_POINTS:
            return False
        normalized = QPointF(_clamp_unit(point.x()), _clamp_unit(point.y()))
        if self._draft_shape_points:
            previous = self._draft_shape_points[-1]
            if (
                abs(previous.x() - normalized.x()) <= 1e-6
                and abs(previous.y() - normalized.y()) <= 1e-6
            ):
                return False
        self._draft_shape_points.append(normalized)
        self.update()
        self.shapeStateChanged.emit()
        return True

    def finish_shape_contour(self) -> bool:
        if (
            not self._window_shape["enabled"]
            or self._shape_tool not in ("add", "subtract")
            or len(self._window_shape["contours"])
            >= MAX_LIVE2D_WINDOW_SHAPE_CONTOURS
        ):
            return False
        candidate = normalize_live2d_window_shape(
            {
                "enabled": True,
                "contours": [
                    {
                        "operation": self._shape_tool,
                        "points": [
                            [point.x(), point.y()]
                            for point in self._draft_shape_points
                        ],
                    }
                ],
            }
        )["contours"]
        if not candidate:
            return False
        self._window_shape["contours"].append(candidate[0])
        self._window_shape = normalize_live2d_window_shape(self._window_shape)
        self._draft_shape_points = []
        if not self._shape_tool_persistent:
            self._shape_tool = None
        self._shape_freehand_active = False
        self._drag_mode = None
        self.update()
        self.shapeStateChanged.emit()
        self.windowShapeChanged.emit(self.window_shape())
        return True

    def undo_shape_edit(self) -> bool:
        if self._draft_shape_points:
            self._draft_shape_points.pop()
            self.update()
            self.shapeStateChanged.emit()
            return True
        if self._window_shape["contours"]:
            self._window_shape["contours"].pop()
            self.update()
            self.shapeStateChanged.emit()
            self.windowShapeChanged.emit(self.window_shape())
            return True
        return False

    def clear_window_shape(self) -> bool:
        if not self._window_shape["contours"] and not self._draft_shape_points:
            return False
        self._window_shape["contours"] = []
        self._draft_shape_points = []
        if not self._shape_tool_persistent:
            self._shape_tool = None
        self._shape_freehand_active = False
        self._drag_mode = None
        self.update()
        self.shapeStateChanged.emit()
        self.windowShapeChanged.emit(self.window_shape())
        return True

    def set_viewport(
        self,
        left: object,
        top: object,
        right: object,
        bottom: object,
        *,
        emit: bool = False,
    ) -> None:
        edges = constrain_viewport_edges(left, top, right, bottom)
        if edges == self._edges:
            return
        self._edges = edges
        self.update()
        if emit:
            self.viewportChanged.emit(*edges)

    def _source_size(self) -> QSize:
        return self._preview.size() if self.has_preview() else self._fallback_canvas_size

    def _canvas_rect(self) -> QRectF:
        available = QRectF(self.rect()).adjusted(
            _CANVAS_MARGIN,
            _CANVAS_MARGIN,
            -_CANVAS_MARGIN,
            -_CANVAS_MARGIN,
        )
        source = self._source_size()
        if (
            available.width() <= 0
            or available.height() <= 0
            or source.width() <= 0
            or source.height() <= 0
        ):
            return QRectF()
        scale = min(
            available.width() / source.width(),
            available.height() / source.height(),
        )
        width = source.width() * scale
        height = source.height() * scale
        return QRectF(
            available.center().x() - width / 2.0,
            available.center().y() - height / 2.0,
            width,
            height,
        )

    def _selection_rect(self) -> QRectF:
        canvas = self._canvas_rect()
        left, top, right, bottom = self._edges
        return QRectF(
            canvas.left() + canvas.width() * left,
            canvas.top() + canvas.height() * top,
            canvas.width() * (right - left),
            canvas.height() * (bottom - top),
        )

    def _anchor_point(self) -> QPointF:
        return self._canvas_point(self._anchor)

    def _contour_path(self, points: object) -> QPainterPath:
        """把完整画布归一化点转换为闭合预览路径。"""
        path = QPainterPath()
        if not isinstance(points, (list, tuple)) or not points:
            return path
        first = points[0]
        path.moveTo(self._canvas_point(QPointF(first[0], first[1])))
        for point in points[1:]:
            path.lineTo(self._canvas_point(QPointF(point[0], point[1])))
        path.closeSubpath()
        path.setFillRule(Qt.OddEvenFill)
        return path

    def _effective_shape_path(self) -> tuple[QPainterPath, bool]:
        """合并全部保留区并统一扣除挖空区，与运行时语义一致。"""
        additions = QPainterPath()
        subtractions = []
        has_addition = False
        for contour in self._window_shape["contours"]:
            path = self._contour_path(contour["points"])
            if path.isEmpty():
                continue
            if contour["operation"] == "add":
                additions = (
                    additions.united(path) if has_addition else path
                )
                has_addition = True
            else:
                subtractions.append(path)
        for subtraction in subtractions:
            additions = additions.subtracted(subtraction)
        return additions, has_addition

    @staticmethod
    def _handle_points(rect: QRectF) -> dict[str, QPointF]:
        center = rect.center()
        return {
            "nw": rect.topLeft(),
            "n": QPointF(center.x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), center.y()),
            "se": rect.bottomRight(),
            "s": QPointF(center.x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), center.y()),
        }

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(PALETTE["surface_input"]))

        canvas = self._canvas_rect()
        if canvas.isEmpty():
            return

        painter.fillRect(canvas, QColor(PALETTE["surface"]))
        self._draw_canvas_grid(painter, canvas)
        if self.has_preview():
            painter.drawPixmap(
                canvas,
                self._preview,
                QRectF(self._preview.rect()),
            )
        else:
            painter.setPen(QColor(PALETTE["text_muted"]))
            painter.drawText(
                canvas.adjusted(24, 24, -24, -24),
                Qt.AlignCenter | Qt.TextWordWrap,
                "启动 Live2D 桌宠后重新打开配置页，\n这里会显示当前模型预览。",
            )

        selection = self._selection_rect()
        border = QColor(
            PALETTE["focus"] if self.hasFocus() else PALETTE["primary"]
        )
        if not self.isEnabled():
            border = QColor(PALETTE["text_muted"])

        if self._crop_enabled:
            outside = QPainterPath()
            outside.setFillRule(Qt.OddEvenFill)
            outside.addRect(canvas)
            outside.addRect(selection)
            scrim = QColor(PALETTE["canvas"])
            scrim.setAlpha(190)
            painter.fillPath(outside, scrim)

            pen = QPen(
                border,
                3
                if self.hasFocus() and self._keyboard_target == "viewport"
                else 2,
            )
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(selection)

        self._draw_window_shape(painter, canvas, selection)

        if self._crop_enabled and self._geometry_editable:
            painter.setPen(QPen(QColor(PALETTE["canvas"]), 1))
            painter.setBrush(border)
            for point in self._handle_points(selection).values():
                painter.drawEllipse(
                    point,
                    _HANDLE_VISUAL_RADIUS,
                    _HANDLE_VISUAL_RADIUS,
                )

        if self._geometry_editable:
            # 默认锚点与选框下边中点重叠时，十字最后绘制且优先命中；用户仍可
            # 在下边界其余位置拖动边缘，两个对象无需切换模式。
            self._draw_placement_anchor(painter)

    def _draw_window_shape(
        self,
        painter: QPainter,
        canvas: QRectF,
        selection: QRectF,
    ) -> None:
        """显示多保留区、挖空区和当前逐点绘制草稿。"""
        if not self._window_shape["enabled"]:
            return

        painter.save()
        painter.setClipRect(canvas)
        effective, has_addition = self._effective_shape_path()
        active_rect = selection if self._crop_enabled else canvas
        active_path = QPainterPath()
        active_path.addRect(active_rect)
        if has_addition and not effective.isEmpty():
            effective = effective.intersected(active_path)
            outside = active_path.subtracted(effective)
            scrim = QColor(PALETTE["canvas"])
            scrim.setAlpha(150)
            painter.fillPath(outside, scrim)
            kept = QColor(PALETTE["success"])
            kept.setAlpha(28)
            painter.fillPath(effective, kept)

        for contour in self._window_shape["contours"]:
            color = QColor(
                PALETTE[
                    "success"
                    if contour["operation"] == "add"
                    else "warning"
                ]
            )
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(color, 2))
            painter.drawPath(self._contour_path(contour["points"]))

        if self._shape_tool and self._draft_shape_points:
            color = QColor(
                PALETTE[
                    "success" if self._shape_tool == "add" else "warning"
                ]
            )
            draft = QPainterPath()
            draft.moveTo(self._canvas_point(self._draft_shape_points[0]))
            for point in self._draft_shape_points[1:]:
                draft.lineTo(self._canvas_point(point))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(
                QPen(
                    color,
                    3,
                    Qt.SolidLine,
                    Qt.RoundCap,
                    Qt.RoundJoin,
                )
            )
            painter.drawPath(draft)
            if len(self._draft_shape_points) >= 3:
                closing = QPainterPath()
                closing.moveTo(self._canvas_point(self._draft_shape_points[-1]))
                closing.lineTo(self._canvas_point(self._draft_shape_points[0]))
                painter.setPen(QPen(color, 2, Qt.DashLine))
                painter.drawPath(closing)
            painter.setPen(QPen(QColor(PALETTE["canvas"]), 1))
            painter.setBrush(color)
            for point in self._draft_shape_points:
                painter.drawEllipse(self._canvas_point(point), 5, 5)
        painter.restore()

    def _draw_placement_anchor(self, painter: QPainter) -> None:
        """用高对比十字靶标显示模型在桌面上保持不动的画布点。"""
        point = self._anchor_point()
        color = QColor(
            PALETTE["focus"]
            if self._keyboard_target == "anchor" and self.hasFocus()
            else PALETTE["accent"]
        )
        if not self.isEnabled():
            color = QColor(PALETTE["text_muted"])

        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(PALETTE["canvas"]), 5))
        painter.drawEllipse(point, 10, 10)
        painter.drawLine(point + QPointF(-15, 0), point + QPointF(15, 0))
        painter.drawLine(point + QPointF(0, -15), point + QPointF(0, 15))
        painter.setPen(QPen(color, 2))
        painter.drawEllipse(point, 10, 10)
        painter.drawLine(point + QPointF(-15, 0), point + QPointF(15, 0))
        painter.drawLine(point + QPointF(0, -15), point + QPointF(0, 15))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(point, 3, 3)
        painter.restore()

    @staticmethod
    def _draw_canvas_grid(painter: QPainter, canvas: QRectF) -> None:
        painter.save()
        painter.setClipRect(canvas)
        first = QColor(PALETTE["surface"])
        second = QColor(PALETTE["surface_elevated"])
        first.setAlpha(220)
        second.setAlpha(150)
        cell = 18
        row = 0
        y = int(canvas.top())
        while y < int(canvas.bottom()) + 1:
            column = 0
            x = int(canvas.left())
            while x < int(canvas.right()) + 1:
                color = first if (row + column) % 2 == 0 else second
                painter.fillRect(x, y, cell, cell, color)
                x += cell
                column += 1
            y += cell
            row += 1
        painter.restore()

    def _normalized_point(self, point: QPointF) -> QPointF:
        canvas = self._canvas_rect()
        if canvas.isEmpty():
            return QPointF()
        return QPointF(
            _clamp_unit((point.x() - canvas.left()) / canvas.width()),
            _clamp_unit((point.y() - canvas.top()) / canvas.height()),
        )

    def _hit_test(self, point: QPointF) -> str | None:
        canvas = self._canvas_rect()
        if self._shape_tool in ("add", "subtract"):
            return "shape_draw" if canvas.contains(point) else None

        if not self._geometry_editable:
            return None

        anchor = self._anchor_point()
        if (
            abs(point.x() - anchor.x()) <= _HANDLE_HIT_RADIUS
            and abs(point.y() - anchor.y()) <= _HANDLE_HIT_RADIUS
        ):
            return "anchor"
        if not self._crop_enabled:
            return None
        selection = self._selection_rect()
        for name, handle in self._handle_points(selection).items():
            if (
                abs(point.x() - handle.x()) <= _HANDLE_HIT_RADIUS
                and abs(point.y() - handle.y()) <= _HANDLE_HIT_RADIUS
            ):
                return name
        edge_hits = []
        if selection.left() <= point.x() <= selection.right():
            edge_hits.extend(
                (
                    (abs(point.y() - selection.top()), "n"),
                    (abs(point.y() - selection.bottom()), "s"),
                )
            )
        if selection.top() <= point.y() <= selection.bottom():
            edge_hits.extend(
                (
                    (abs(point.x() - selection.left()), "w"),
                    (abs(point.x() - selection.right()), "e"),
                )
            )
        edge_hits = [
            candidate
            for candidate in edge_hits
            if candidate[0] <= _HANDLE_HIT_RADIUS
        ]
        if edge_hits:
            return min(edge_hits)[1]
        if selection.contains(point):
            return "move"
        if canvas.contains(point):
            return "new"
        return None

    @staticmethod
    def _cursor_for_mode(mode: str | None):
        return {
            "nw": Qt.SizeFDiagCursor,
            "se": Qt.SizeFDiagCursor,
            "ne": Qt.SizeBDiagCursor,
            "sw": Qt.SizeBDiagCursor,
            "n": Qt.SizeVerCursor,
            "s": Qt.SizeVerCursor,
            "e": Qt.SizeHorCursor,
            "w": Qt.SizeHorCursor,
            "move": Qt.SizeAllCursor,
            "new": Qt.CrossCursor,
            "anchor": Qt.CrossCursor,
            "shape_draw": Qt.CrossCursor,
        }.get(mode, Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.LeftButton or not self.isEnabled():
            super().mousePressEvent(event)
            return
        mode = self._hit_test(QPointF(event.pos()))
        if mode is None:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.MouseFocusReason)
        normalized = self._normalized_point(QPointF(event.pos()))
        if mode == "shape_draw":
            self._append_shape_point(normalized)
            self._drag_mode = "shape_draw"
            self._shape_press_position = QPointF(event.pos())
            self._shape_freehand_active = False
            event.accept()
            return
        self._drag_mode = mode
        self._drag_origin = normalized
        self._drag_origin_edges = self._edges
        self._new_selection_started = False
        if mode == "anchor":
            self._anchor_drag_offset = QPointF(
                self._anchor.x() - normalized.x(),
                self._anchor.y() - normalized.y(),
            )
            self._keyboard_target = "anchor"
        else:
            self._keyboard_target = "viewport"
        self.update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        point = QPointF(event.pos())
        if self._drag_mode is None:
            self.setCursor(self._cursor_for_mode(self._hit_test(point)))
            super().mouseMoveEvent(event)
            return

        if self._drag_mode == "shape_draw":
            if (
                not self._shape_freehand_active
                and (point - self._shape_press_position).manhattanLength()
                >= _LASSO_DRAG_THRESHOLD
            ):
                self._shape_freehand_active = True
            if self._shape_freehand_active and self._draft_shape_points:
                previous = self._canvas_point(self._draft_shape_points[-1])
                if (point - previous).manhattanLength() >= _LASSO_SAMPLE_SPACING:
                    self._append_shape_point(self._normalized_point(point))
            event.accept()
            return

        normalized = self._normalized_point(point)
        dx = normalized.x() - self._drag_origin.x()
        dy = normalized.y() - self._drag_origin.y()
        left, top, right, bottom = self._drag_origin_edges
        mode = self._drag_mode

        if mode == "anchor":
            self.set_placement_anchor(
                {
                    "x": normalized.x() + self._anchor_drag_offset.x(),
                    "y": normalized.y() + self._anchor_drag_offset.y(),
                },
                emit=True,
            )
            event.accept()
            return
        if mode == "move":
            width = right - left
            height = bottom - top
            left = max(0.0, min(1.0 - width, left + dx))
            top = max(0.0, min(1.0 - height, top + dy))
            right = left + width
            bottom = top + height
        elif mode == "new":
            canvas = self._canvas_rect()
            if (
                not self._new_selection_started
                and (
                    QPointF(event.pos())
                    - self._canvas_point(self._drag_origin)
                ).manhattanLength()
                < 4
            ):
                return
            self._new_selection_started = True
            left = min(self._drag_origin.x(), normalized.x())
            right = max(self._drag_origin.x(), normalized.x())
            top = min(self._drag_origin.y(), normalized.y())
            bottom = max(self._drag_origin.y(), normalized.y())
        else:
            if "w" in mode:
                left = min(max(0.0, left + dx), right - MIN_VIEWPORT_SPAN)
            if "e" in mode:
                right = max(min(1.0, right + dx), left + MIN_VIEWPORT_SPAN)
            if "n" in mode:
                top = min(max(0.0, top + dy), bottom - MIN_VIEWPORT_SPAN)
            if "s" in mode:
                bottom = max(min(1.0, bottom + dy), top + MIN_VIEWPORT_SPAN)

        self.set_viewport(left, top, right, bottom, emit=True)
        event.accept()

    def _canvas_point(self, normalized: QPointF) -> QPointF:
        canvas = self._canvas_rect()
        return QPointF(
            canvas.left() + normalized.x() * canvas.width(),
            canvas.top() + normalized.y() * canvas.height(),
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self._drag_mode == "shape_draw":
            if self._shape_freehand_active:
                point = QPointF(event.pos())
                if self._draft_shape_points:
                    previous = self._canvas_point(self._draft_shape_points[-1])
                    if (
                        (point - previous).manhattanLength()
                        >= _LASSO_SAMPLE_SPACING
                    ):
                        self._append_shape_point(self._normalized_point(point))
                if len(self._draft_shape_points) >= 3:
                    self.finish_shape_contour()
            self._drag_mode = None
            self._shape_freehand_active = False
            self.setCursor(
                self._cursor_for_mode(self._hit_test(QPointF(event.pos())))
            )
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._drag_mode is not None:
            self._drag_mode = None
            self._new_selection_started = False
            self.setCursor(self._cursor_for_mode(self._hit_test(QPointF(event.pos()))))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        """逐点模式可双击最后一点完成，不必再寻找工具栏按钮。"""
        point = QPointF(event.pos())
        if (
            event.button() == Qt.LeftButton
            and self.isEnabled()
            and self._shape_tool in ("add", "subtract")
            and self._canvas_rect().contains(point)
        ):
            self._append_shape_point(self._normalized_point(point))
            if len(self._draft_shape_points) >= 3:
                self.finish_shape_contour()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_mode is None:
            self.unsetCursor()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._shape_tool in ("add", "subtract"):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.finish_shape_contour()
                event.accept()
                return
            if event.key() == Qt.Key_Backspace:
                self.undo_shape_edit()
                event.accept()
                return
            if event.key() == Qt.Key_Escape:
                self.set_shape_tool(None)
                event.accept()
                return

        movement = {
            Qt.Key_Left: (-1.0, 0.0),
            Qt.Key_Right: (1.0, 0.0),
            Qt.Key_Up: (0.0, -1.0),
            Qt.Key_Down: (0.0, 1.0),
        }.get(event.key())
        if movement is None or not self.isEnabled():
            super().keyPressEvent(event)
            return

        if not self._geometry_editable:
            super().keyPressEvent(event)
            return

        step = 0.05 if event.modifiers() & Qt.ShiftModifier else 0.01
        dx, dy = movement[0] * step, movement[1] * step
        if self._keyboard_target == "anchor":
            self.set_placement_anchor(
                {
                    "x": self._anchor.x() + dx,
                    "y": self._anchor.y() + dy,
                },
                emit=True,
            )
            event.accept()
            return
        if not self._crop_enabled:
            super().keyPressEvent(event)
            return
        left, top, right, bottom = self._edges
        width, height = right - left, bottom - top
        left = max(0.0, min(1.0 - width, left + dx))
        top = max(0.0, min(1.0 - height, top + dy))
        self.set_viewport(left, top, left + width, top + height, emit=True)
        event.accept()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().focusOutEvent(event)


class Live2DWindowShapeDialog(QDialog):
    """在独立、可缩放画布中编辑 Live2D 精细窗口形状。"""

    def __init__(self, preview: QImage | QPixmap | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Live2DWindowShapeDialog")
        self.setWindowTitle("编辑 Live2D 精细窗口形状")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.setModal(True)
        self.setSizeGripEnabled(True)
        font_scale = get_ui_font_scale()
        scale_extra = max(0.0, font_scale - 1.0)
        self.setMinimumSize(
            round(720 + 160 * scale_extra),
            round(640 + 180 * scale_extra),
        )
        self.resize(960, 720)
        self.setAccessibleName("编辑 Live2D 精细窗口形状")
        self.setAccessibleDescription(
            "用套索或逐点多边形定义窗口保留区和挖空区；只有应用后才会"
            "写回配置页"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        title = QLabel("编辑精细窗口形状")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self.instructions_label = QLabel(
            "先选择“保留套索”或“挖空套索”。按住鼠标沿轮廓描一圈，"
            "松开即完成；也可以单击逐点放置，双击或按 Enter 完成。"
        )
        self.instructions_label.setObjectName("PageDescription")
        self.instructions_label.setWordWrap(True)
        self.instructions_label.setAccessibleName("精细形状编辑说明")
        layout.addWidget(self.instructions_label)

        tools = QHBoxLayout()
        tools.setSpacing(10)
        self.shape_add_button = self._make_button(
            "保留套索",
            "绘制需要保留并可接收鼠标操作的窗口区域",
            checkable=True,
            object_name="ShapeAddTool",
        )
        self.shape_subtract_button = self._make_button(
            "挖空套索",
            "从保留区中挖去透明孔洞或不需要响应鼠标的区域",
            checkable=True,
            object_name="ShapeSubtractTool",
        )
        self.shape_finish_button = self._make_button(
            "完成轮廓",
            "逐点绘制至少三个点后，完成当前轮廓",
        )
        self.shape_undo_button = self._make_button(
            "撤销",
            "优先撤销当前轮廓的最后一点，否则删除最后一个已完成轮廓",
        )
        self.shape_clear_button = self._make_button(
            "清空",
            "删除全部保留区和挖空区",
        )
        for button in (
            self.shape_add_button,
            self.shape_subtract_button,
            self.shape_finish_button,
            self.shape_undo_button,
            self.shape_clear_button,
        ):
            tools.addWidget(button)
        tools.addStretch()
        layout.addLayout(tools)

        self.editor = Live2DViewportEditor(preview=preview)
        self.editor.setObjectName("Live2DWindowShapeEditor")
        self.editor.set_shape_tool_persistent(True)
        self.editor.set_geometry_editable(False)
        self.editor.setAccessibleName("Live2D 精细窗口形状画布")
        self.editor.setAccessibleDescription(
            "选择保留或挖空套索后，可按住鼠标自由描边，或单击逐点绘制；"
            "双击或回车完成，退格撤点，Escape 取消当前轮廓"
        )
        layout.addWidget(self.editor, 1)

        self.shape_status_label = QLabel()
        self.shape_status_label.setObjectName("HelperText")
        self.shape_status_label.setWordWrap(True)
        self.shape_status_label.setAccessibleName("当前精细窗口形状状态")
        layout.addWidget(self.shape_status_label)

        keyboard_hint = QLabel(
            "键盘：Enter 完成当前轮廓 · Backspace 撤销一点 · "
            "Esc 先取消当前草稿。取消对话框不会应用任何改动。"
        )
        keyboard_hint.setObjectName("HelperText")
        keyboard_hint.setWordWrap(True)
        layout.addWidget(keyboard_hint)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch()
        self.cancel_button = self._make_button(
            "取消",
            "关闭编辑器并放弃本次形状改动",
        )
        self.apply_button = self._make_button(
            "应用形状",
            "把当前精细窗口形状应用到配置页",
            object_name="PrimaryButton",
        )
        self.cancel_button.setMinimumWidth(104)
        self.apply_button.setMinimumWidth(124)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)

        self.shape_add_button.clicked.connect(
            lambda _checked=False: self.editor.set_shape_tool("add")
        )
        self.shape_subtract_button.clicked.connect(
            lambda _checked=False: self.editor.set_shape_tool("subtract")
        )
        self.shape_finish_button.clicked.connect(
            lambda _checked=False: self.editor.finish_shape_contour()
        )
        self.shape_undo_button.clicked.connect(
            lambda _checked=False: self.editor.undo_shape_edit()
        )
        self.shape_clear_button.clicked.connect(
            lambda _checked=False: self.editor.clear_window_shape()
        )
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.accept)
        self.editor.shapeStateChanged.connect(self._update_shape_state)
        self.editor.windowShapeChanged.connect(
            lambda _shape: self._update_shape_state()
        )
        self.set_window_shape(DEFAULT_LIVE2D_WINDOW_SHAPE)

    @staticmethod
    def _make_button(
        text: str,
        description: str,
        *,
        checkable: bool = False,
        object_name: str = "SecondaryButton",
    ) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setCheckable(checkable)
        button.setMinimumHeight(MIN_TARGET_SIZE)
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)
        return button

    def set_window_shape(self, value: object) -> None:
        shape = normalize_live2d_window_shape(value)
        # 打开编辑器本身就是编辑精细形状的明确意图；临时副本始终启用，
        # 主配置只会在 accepted 信号发出后收到它。
        shape["enabled"] = True
        self.editor.set_window_shape(shape)
        self.editor.set_shape_enabled(True)
        self._update_shape_state()

    def window_shape(self) -> dict:
        shape = self.editor.window_shape()
        shape["enabled"] = True
        return normalize_live2d_window_shape(shape)

    def _update_shape_state(self) -> None:
        shape = self.editor.window_shape()
        contours = shape["contours"]
        tool = self.editor.shape_tool()
        draft_points = self.editor.draft_shape_point_count()
        at_limit = len(contours) >= MAX_LIVE2D_WINDOW_SHAPE_CONTOURS
        has_edits = bool(contours or draft_points)

        self.shape_add_button.setEnabled(not at_limit)
        self.shape_subtract_button.setEnabled(not at_limit)
        self.shape_finish_button.setEnabled(
            tool is not None and draft_points >= 3 and not at_limit
        )
        self.shape_undo_button.setEnabled(has_edits)
        self.shape_clear_button.setEnabled(has_edits)
        self.shape_add_button.setChecked(tool == "add")
        self.shape_subtract_button.setChecked(tool == "subtract")

        additions = sum(
            contour["operation"] == "add" for contour in contours
        )
        subtractions = len(contours) - additions
        counts = f"{additions} 个保留区，{subtractions} 个挖空区"
        if at_limit:
            text = (
                f"已达到 {MAX_LIVE2D_WINDOW_SHAPE_CONTOURS} 个轮廓上限；"
                f"当前有 {counts}。"
            )
        elif tool is not None:
            operation = "保留套索" if tool == "add" else "挖空套索"
            if draft_points:
                text = (
                    f"当前工具：{operation}；当前轮廓已有 {draft_points} 个点。"
                    f"已完成 {counts}。"
                )
            else:
                text = (
                    f"当前工具：{operation}；可直接按住鼠标描边或逐点单击。"
                    f"已完成 {counts}。"
                )
        elif additions == 0:
            text = (
                f"尚未选择工具；当前有 {counts}。请先画至少一个保留区，"
                "否则运行时仍使用普通矩形窗口。"
            )
        else:
            text = f"尚未选择工具；当前有 {counts}。"
        self.shape_status_label.setText(text)
        self.shape_status_label.setAccessibleDescription(text)


class Live2DViewportSettings(QFrame):
    """在独立页面编辑视觉视口、站立锚点与可选窗口形状。"""

    changed = pyqtSignal()

    def __init__(self, preview: QImage | QPixmap | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCard")
        self.setAccessibleName("Live2D 窗口范围")
        self.setAccessibleDescription(
            "裁去 Live2D 完整画布周围的透明空白，设置缩放时保持不动的"
            "模型站立点，并可绘制静态多边形窗口形状"
        )
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        title = QLabel("Live2D 窗口范围")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        description = QLabel(
            "拖动预览中的矩形框住模型活动范围。这里只缩小透明窗口占用，"
            "不会缩放模型，也不会改变头部和左右区域语音；模型站立锚点决定"
            "缩放或改范围时留在桌面原位的模型位置。矩形和十字锚点可直接"
            "在同一预览中拖动，无需切换模式。"
        )
        description.setObjectName("PageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.crop_enabled = QCheckBox("裁去 Live2D 画布透明边缘")
        self.crop_enabled.setObjectName("Live2DViewportEnabled")
        self.crop_enabled.setAccessibleName("启用 Live2D 窗口范围裁剪")
        self.crop_enabled.setAccessibleDescription(
            "关闭后桌宠窗口恢复为完整 Live2D 画布大小"
        )
        layout.addWidget(self.crop_enabled)

        self.editor = Live2DViewportEditor(preview=preview)
        layout.addWidget(self.editor, 1)

        self.status_label = QLabel()
        self.status_label.setObjectName("HelperText")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("当前 Live2D 窗口范围")
        layout.addWidget(self.status_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.left_input = self._make_edge_input("左边界")
        self.top_input = self._make_edge_input("上边界")
        self.right_input = self._make_edge_input("右边界")
        self.bottom_input = self._make_edge_input("下边界")
        inputs = (
            ("左边界", self.left_input, 0, 0),
            ("上边界", self.top_input, 0, 2),
            ("右边界", self.right_input, 1, 0),
            ("下边界", self.bottom_input, 1, 2),
        )
        for label_text, control, row, column in inputs:
            label = QLabel(label_text)
            label.setObjectName("InlineFieldLabel")
            grid.addWidget(label, row, column)
            grid.addWidget(control, row, column + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        anchor_title = QLabel("模型站立锚点")
        anchor_title.setObjectName("InlineFieldLabel")
        layout.addWidget(anchor_title)

        anchor_hint = QLabel(
            "通常放在双脚之间；X、Y 均相对于完整 Live2D 画布。"
            "它只负责位置稳定，不会改变语音分区。"
        )
        anchor_hint.setObjectName("HelperText")
        anchor_hint.setWordWrap(True)
        layout.addWidget(anchor_hint)

        anchor_grid = QGridLayout()
        anchor_grid.setHorizontalSpacing(12)
        anchor_grid.setVerticalSpacing(8)
        self.anchor_x_input = self._make_anchor_input("X 坐标")
        self.anchor_y_input = self._make_anchor_input("Y 坐标")
        for label_text, control, column in (
            ("X 坐标", self.anchor_x_input, 0),
            ("Y 坐标", self.anchor_y_input, 2),
        ):
            label = QLabel(label_text)
            label.setObjectName("InlineFieldLabel")
            anchor_grid.addWidget(label, 0, column)
            anchor_grid.addWidget(control, 0, column + 1)
        anchor_grid.setColumnStretch(1, 1)
        anchor_grid.setColumnStretch(3, 1)
        layout.addLayout(anchor_grid)

        shape_title = QLabel("精细窗口形状")
        shape_title.setObjectName("InlineFieldLabel")
        layout.addWidget(shape_title)

        shape_hint = QLabel(
            "复杂发型、配饰或分离区域可用套索精细描边。它只改变静态窗口和"
            "鼠标命中范围，不会重新定义头部、左侧、右侧语音触发区；请为"
            "头发与大幅动作留出余量。"
        )
        shape_hint.setObjectName("HelperText")
        shape_hint.setWordWrap(True)
        layout.addWidget(shape_hint)

        shape_row = QHBoxLayout()
        shape_row.setSpacing(12)
        self.shape_enabled = QCheckBox("使用精细窗口形状")
        self.shape_enabled.setObjectName("Live2DWindowShapeEnabled")
        self.shape_enabled.setAccessibleName("启用 Live2D 自定义窗口形状")
        self.shape_enabled.setAccessibleDescription(
            "关闭时保留已绘制轮廓，但桌宠恢复为普通矩形窗口"
        )
        shape_row.addWidget(self.shape_enabled)
        shape_row.addStretch()
        self.shape_edit_button = QPushButton("编辑精细形状…")
        self.shape_edit_button.setObjectName("SecondaryButton")
        self.shape_edit_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.shape_edit_button.setAccessibleName("编辑 Live2D 精细窗口形状")
        self.shape_edit_button.setAccessibleDescription(
            "在可缩放的独立画布中绘制保留区和挖空区"
        )
        shape_row.addWidget(self.shape_edit_button)
        layout.addLayout(shape_row)

        self.shape_summary_label = QLabel()
        self.shape_summary_label.setObjectName("HelperText")
        self.shape_summary_label.setWordWrap(True)
        self.shape_summary_label.setAccessibleName("当前 Live2D 精细窗口形状")
        layout.addWidget(self.shape_summary_label)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        self.reset_button = QPushButton("恢复默认范围")
        self.reset_button.setObjectName("SecondaryButton")
        self.reset_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.reset_button.setAccessibleName("恢复默认的 Live2D 窗口范围")
        actions.addWidget(self.reset_button)

        self.full_canvas_button = QPushButton("使用完整画布")
        self.full_canvas_button.setObjectName("SecondaryButton")
        self.full_canvas_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.full_canvas_button.setAccessibleName("Live2D 窗口使用完整画布")
        actions.addWidget(self.full_canvas_button)

        self.anchor_reset_button = QPushButton("锚点移到范围底部中心")
        self.anchor_reset_button.setObjectName("SecondaryButton")
        self.anchor_reset_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.anchor_reset_button.setAccessibleName(
            "把模型站立锚点移到当前窗口范围底部中心"
        )
        actions.addWidget(self.anchor_reset_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.editor.viewportChanged.connect(self._on_editor_changed)
        self.editor.placementAnchorChanged.connect(
            self._on_editor_anchor_changed
        )
        self.editor.windowShapeChanged.connect(
            self._on_editor_shape_changed
        )
        self.editor.shapeStateChanged.connect(self._update_shape_state)
        for edge, control in self._edge_inputs():
            control.valueChanged.connect(
                lambda value, current_edge=edge: self._on_input_changed(
                    current_edge,
                    value,
                )
            )
        self.crop_enabled.toggled.connect(self._on_enabled_changed)
        self.anchor_x_input.valueChanged.connect(self._on_anchor_input_changed)
        self.anchor_y_input.valueChanged.connect(self._on_anchor_input_changed)
        self.shape_enabled.toggled.connect(self._on_shape_enabled_changed)
        self.shape_edit_button.clicked.connect(self._open_shape_editor)
        self.reset_button.clicked.connect(self.restore_recommended)
        self.full_canvas_button.clicked.connect(self.use_full_canvas)
        self.anchor_reset_button.clicked.connect(self.reset_placement_anchor)
        self.set_window_mask(DEFAULT_LIVE2D_WINDOW_MASK)
        self.set_placement_anchor(DEFAULT_LIVE2D_PLACEMENT_ANCHOR)
        self.set_window_shape(DEFAULT_LIVE2D_WINDOW_SHAPE)

    @staticmethod
    def _make_edge_input(name: str) -> QDoubleSpinBox:
        control = QDoubleSpinBox()
        control.setObjectName(f"Live2D{name}")
        control.setRange(0.0, 100.0)
        control.setDecimals(1)
        control.setSingleStep(1.0)
        control.setSuffix("%")
        control.setKeyboardTracking(False)
        control.setMinimumHeight(MIN_TARGET_SIZE)
        control.setAccessibleName(f"Live2D 窗口{name}")
        control.setAccessibleDescription("相对于完整 Live2D 画布的百分比坐标")
        return control

    @staticmethod
    def _make_anchor_input(name: str) -> QDoubleSpinBox:
        control = QDoubleSpinBox()
        control.setObjectName(f"Live2D锚点{name}")
        control.setRange(0.0, 100.0)
        control.setDecimals(1)
        control.setSingleStep(1.0)
        control.setSuffix("%")
        control.setKeyboardTracking(False)
        control.setMinimumHeight(MIN_TARGET_SIZE)
        control.setAccessibleName(f"Live2D 模型站立锚点{name}")
        control.setAccessibleDescription("相对于完整 Live2D 画布的百分比坐标")
        return control

    def _edge_inputs(self) -> Iterable[tuple[str, QDoubleSpinBox]]:
        return (
            ("left", self.left_input),
            ("top", self.top_input),
            ("right", self.right_input),
            ("bottom", self.bottom_input),
        )

    def _current_edges(self) -> tuple[float, float, float, float]:
        return (
            self.left_input.value() / 100.0,
            self.top_input.value() / 100.0,
            self.right_input.value() / 100.0,
            self.bottom_input.value() / 100.0,
        )

    def set_window_mask(self, value: object) -> None:
        mask = normalize_live2d_window_mask(value)
        edges = window_mask_to_viewport_edges(mask)
        self._syncing = True
        try:
            self.crop_enabled.setChecked(mask["enabled"])
            self._set_edges(edges)
        finally:
            self._syncing = False
        self._update_enabled_state()
        self._update_status()

    def window_mask(self) -> dict:
        return viewport_edges_to_window_mask(
            *self._current_edges(),
            enabled=self.crop_enabled.isChecked(),
        )

    def set_placement_anchor(self, value: object) -> None:
        anchor = normalize_live2d_placement_anchor(value, self.window_mask())
        self._set_anchor(anchor)

    def placement_anchor(self) -> dict:
        return {
            "x": round(self.anchor_x_input.value() / 100.0, _EDGE_PRECISION),
            "y": round(self.anchor_y_input.value() / 100.0, _EDGE_PRECISION),
        }

    def set_window_shape(self, value: object) -> None:
        shape = normalize_live2d_window_shape(value)
        previous = self._syncing
        self._syncing = True
        try:
            self.shape_enabled.setChecked(shape["enabled"])
            self.editor.set_window_shape(shape)
        finally:
            self._syncing = previous
        self._update_shape_state()

    def window_shape(self) -> dict:
        shape = self.editor.window_shape()
        shape["enabled"] = self.shape_enabled.isChecked()
        return normalize_live2d_window_shape(shape)

    def create_shape_editor_dialog(self) -> Live2DWindowShapeDialog:
        """创建使用临时副本的形状编辑器；接受后才写回主设置。"""
        preview = (
            self.editor.preview_pixmap()
            if self.editor.has_preview()
            else None
        )
        dialog = Live2DWindowShapeDialog(preview=preview, parent=self)
        fallback = self.editor.fallback_canvas_size()
        dialog.editor.set_fallback_canvas_size(
            fallback.width(),
            fallback.height(),
        )
        dialog.editor.set_viewport(*self.editor.viewport())
        dialog.editor.set_crop_enabled(self.crop_enabled.isChecked())
        dialog.set_window_shape(self.window_shape())
        dialog.accepted.connect(
            lambda current=dialog: self._apply_shape_editor_dialog(current)
        )
        return dialog

    def _open_shape_editor(self, _checked: bool = False) -> None:
        dialog = self.create_shape_editor_dialog()
        dialog.exec_()

    def _apply_shape_editor_dialog(
        self,
        dialog: Live2DWindowShapeDialog,
    ) -> None:
        shape = dialog.window_shape()
        changed = shape != self.window_shape()
        self.set_window_shape(shape)
        if changed and not self._syncing:
            self.changed.emit()

    def set_fallback_canvas_size(self, value: object) -> None:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            self.editor.set_fallback_canvas_size(value[0], value[1])

    def _set_edges(self, edges: tuple[float, float, float, float]) -> None:
        constrained = constrain_viewport_edges(*edges)
        previous = self._syncing
        self._syncing = True
        try:
            self.editor.set_viewport(*constrained)
            for value, (_edge, control) in zip(constrained, self._edge_inputs()):
                control.setValue(value * 100.0)
        finally:
            self._syncing = previous
        self._update_status()

    def _set_anchor(self, value: object) -> None:
        anchor = normalize_live2d_placement_anchor(value, self.window_mask())
        previous = self._syncing
        self._syncing = True
        try:
            self.editor.set_placement_anchor(anchor)
            self.anchor_x_input.setValue(anchor["x"] * 100.0)
            self.anchor_y_input.setValue(anchor["y"] * 100.0)
        finally:
            self._syncing = previous
        self._update_status()

    def _on_editor_changed(
        self,
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> None:
        if self._syncing:
            return
        self._set_edges((left, top, right, bottom))
        self.changed.emit()

    def _on_editor_anchor_changed(self, x: float, y: float) -> None:
        if self._syncing:
            return
        self._set_anchor({"x": x, "y": y})
        self.changed.emit()

    def _on_editor_shape_changed(self, _shape: object) -> None:
        self._update_shape_state()
        if not self._syncing:
            self.changed.emit()

    def _on_anchor_input_changed(self, _value: float) -> None:
        if self._syncing:
            return
        self._set_anchor(self.placement_anchor())
        self.changed.emit()

    def _on_input_changed(self, edge: str, value: float) -> None:
        if self._syncing:
            return
        left, top, right, bottom = self._current_edges()
        value = _clamp_unit(value / 100.0)
        if edge == "left":
            left = min(value, right - MIN_VIEWPORT_SPAN)
        elif edge == "top":
            top = min(value, bottom - MIN_VIEWPORT_SPAN)
        elif edge == "right":
            right = max(value, left + MIN_VIEWPORT_SPAN)
        elif edge == "bottom":
            bottom = max(value, top + MIN_VIEWPORT_SPAN)
        self._set_edges((left, top, right, bottom))
        self.changed.emit()

    def _on_enabled_changed(self, _enabled: bool) -> None:
        self._update_enabled_state()
        self._update_status()
        if not self._syncing:
            self.changed.emit()

    def _on_shape_enabled_changed(self, enabled: bool) -> None:
        self.editor.set_shape_enabled(enabled)
        self._update_shape_state()
        if not self._syncing:
            self.changed.emit()

    def _update_enabled_state(self) -> None:
        enabled = self.crop_enabled.isChecked()
        self.editor.set_crop_enabled(enabled)
        for _edge, control in self._edge_inputs():
            control.setEnabled(enabled)

    def _update_shape_state(self) -> None:
        shape = self.editor.window_shape()
        enabled = self.shape_enabled.isChecked()
        contours = shape["contours"]

        additions = sum(
            contour["operation"] == "add" for contour in contours
        )
        subtractions = len(contours) - additions
        counts = f"{additions} 个保留区，{subtractions} 个挖空区"
        if not enabled:
            text = f"精细形状已关闭；已保留 {counts}，重新启用后仍可使用。"
        elif additions == 0:
            text = (
                f"当前有 {counts}。需要至少一个保留区；"
                "在此之前桌宠仍使用上方的普通矩形范围。"
            )
        else:
            text = (
                f"当前有 {counts}；只影响窗口与鼠标命中，不改变语音分区。"
            )
        self.shape_summary_label.setText(text)
        self.shape_summary_label.setAccessibleDescription(text)

    def _update_status(self) -> None:
        anchor = self.placement_anchor()
        anchor_text = (
            f"站立锚点 X {anchor['x'] * 100:.1f}%，"
            f"Y {anchor['y'] * 100:.1f}%"
        )
        if not self.crop_enabled.isChecked():
            text = f"已关闭裁剪：桌宠窗口使用完整画布；{anchor_text}。"
        else:
            left, top, right, bottom = self._current_edges()
            width = max(0.0, right - left) * 100.0
            height = max(0.0, bottom - top) * 100.0
            if width >= 99.95 and height >= 99.95:
                text = f"当前使用完整画布（100% × 100%）；{anchor_text}。"
            else:
                text = (
                    f"窗口约占完整画布的 {width:.1f}% × {height:.1f}%；"
                    f"{anchor_text}；模型大小和语音分区保持不变。"
                )
        text += (
            " 预览中的十字锚点可直接拖动；点击锚点或范围后，"
            "方向键会移动最后操作的对象。"
        )
        self.status_label.setText(text)
        self.status_label.setAccessibleDescription(text)

    def restore_recommended(self) -> None:
        self.crop_enabled.setChecked(True)
        self._set_edges(window_mask_to_viewport_edges(DEFAULT_LIVE2D_WINDOW_MASK))
        self.changed.emit()

    def use_full_canvas(self) -> None:
        self.crop_enabled.setChecked(True)
        self._set_edges((0.0, 0.0, 1.0, 1.0))
        self.changed.emit()

    def reset_placement_anchor(self) -> None:
        self._set_anchor(
            normalize_live2d_placement_anchor(None, self.window_mask())
        )
        self.changed.emit()


__all__ = [
    "Live2DViewportEditor",
    "Live2DViewportSettings",
    "Live2DWindowShapeDialog",
    "MIN_VIEWPORT_SPAN",
    "constrain_viewport_edges",
    "viewport_edges_to_window_mask",
    "window_mask_to_viewport_edges",
]
