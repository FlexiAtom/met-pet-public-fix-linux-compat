"""Live2D 顶层窗口视口与模型站立锚点的可视化编辑控件。"""

from __future__ import annotations

from collections.abc import Iterable

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
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
)
from meapet.config.store import (
    normalize_live2d_placement_anchor,
    normalize_live2d_window_mask,
)
from meapet.ui_theme import MIN_TARGET_SIZE, PALETTE


MIN_VIEWPORT_SPAN = 0.20
_EDGE_PRECISION = 6
_HANDLE_HIT_RADIUS = 22.0
_HANDLE_VISUAL_RADIUS = 6.0
_CANVAS_MARGIN = 18.0


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
            "拖动十字标记设置模型站立锚点，方向键移动最后选择的对象；"
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
        self._edit_mode = "viewport"
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

    def set_edit_mode(self, mode: str) -> None:
        normalized = "anchor" if mode == "anchor" else "viewport"
        if normalized == self._edit_mode:
            return
        self._edit_mode = normalized
        self._drag_mode = None
        self.unsetCursor()
        self.update()

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

            pen = QPen(border, 3 if self.hasFocus() else 2)
            if self._edit_mode == "anchor":
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(selection)

        self._draw_placement_anchor(painter)

        # 默认锚点可能正好落在选框下边手柄上；范围编辑模式让手柄最后绘制，
        # 锚点编辑模式则隐藏手柄，避免两个 44px 命中目标相互遮挡。
        if self._crop_enabled and self._edit_mode == "viewport":
            painter.setPen(QPen(QColor(PALETTE["canvas"]), 1))
            painter.setBrush(border)
            for point in self._handle_points(selection).values():
                painter.drawEllipse(
                    point,
                    _HANDLE_VISUAL_RADIUS,
                    _HANDLE_VISUAL_RADIUS,
                )

    def _draw_placement_anchor(self, painter: QPainter) -> None:
        """用高对比十字靶标显示模型在桌面上保持不动的画布点。"""
        point = self._anchor_point()
        color = QColor(
            PALETTE["focus"]
            if self._edit_mode == "anchor" and self.hasFocus()
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
        if self._edit_mode == "anchor":
            return "anchor" if self._canvas_rect().contains(point) else None
        if not self._crop_enabled:
            return None
        selection = self._selection_rect()
        for name, handle in self._handle_points(selection).items():
            if (
                abs(point.x() - handle.x()) <= _HANDLE_HIT_RADIUS
                and abs(point.y() - handle.y()) <= _HANDLE_HIT_RADIUS
            ):
                return name
        if selection.contains(point):
            return "move"
        if self._canvas_rect().contains(point):
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
        self._drag_mode = mode
        self._drag_origin = self._normalized_point(QPointF(event.pos()))
        self._drag_origin_edges = self._edges
        self._new_selection_started = False
        if mode == "anchor":
            self.set_placement_anchor(
                {"x": self._drag_origin.x(), "y": self._drag_origin.y()},
                emit=True,
            )
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        point = QPointF(event.pos())
        if self._drag_mode is None:
            self.setCursor(self._cursor_for_mode(self._hit_test(point)))
            super().mouseMoveEvent(event)
            return

        normalized = self._normalized_point(point)
        dx = normalized.x() - self._drag_origin.x()
        dy = normalized.y() - self._drag_origin.y()
        left, top, right, bottom = self._drag_origin_edges
        mode = self._drag_mode

        if mode == "anchor":
            self.set_placement_anchor(
                {"x": normalized.x(), "y": normalized.y()},
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
                and (QPointF(event.pos()) - self._canvas_point(self._drag_origin)).manhattanLength()
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
        if event.button() == Qt.LeftButton and self._drag_mode is not None:
            self._drag_mode = None
            self._new_selection_started = False
            self.setCursor(self._cursor_for_mode(self._hit_test(QPointF(event.pos()))))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_mode is None:
            self.unsetCursor()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        movement = {
            Qt.Key_Left: (-1.0, 0.0),
            Qt.Key_Right: (1.0, 0.0),
            Qt.Key_Up: (0.0, -1.0),
            Qt.Key_Down: (0.0, 1.0),
        }.get(event.key())
        if movement is None or not self.isEnabled():
            super().keyPressEvent(event)
            return

        step = 0.05 if event.modifiers() & Qt.ShiftModifier else 0.01
        dx, dy = movement[0] * step, movement[1] * step
        if self._edit_mode == "anchor":
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


class Live2DViewportSettings(QFrame):
    """框选视觉视口并校准模型站立锚点的完整配置区。"""

    changed = pyqtSignal()

    def __init__(self, preview: QImage | QPixmap | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("SectionCard")
        self.setAccessibleName("Live2D 窗口范围")
        self.setAccessibleDescription(
            "裁去 Live2D 完整画布周围的透明空白，并设置缩放时保持不动的模型站立点"
        )
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        title = QLabel("Live2D 窗口范围")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        description = QLabel(
            "拖动预览中的矩形框住模型活动范围。这里只缩小透明窗口占用，"
            "不会缩放模型，也不会改变头部和左右区域语音；模型站立锚点决定"
            "缩放或改范围时留在桌面原位的模型位置。"
        )
        description.setObjectName("HelperText")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.crop_enabled = QCheckBox("裁去 Live2D 画布透明边缘")
        self.crop_enabled.setObjectName("Live2DViewportEnabled")
        self.crop_enabled.setAccessibleName("启用 Live2D 窗口范围裁剪")
        self.crop_enabled.setAccessibleDescription(
            "关闭后桌宠窗口恢复为完整 Live2D 画布大小"
        )
        layout.addWidget(self.crop_enabled)

        self.anchor_edit_button = QPushButton("调整模型站立锚点")
        self.anchor_edit_button.setObjectName("SecondaryButton")
        self.anchor_edit_button.setCheckable(True)
        self.anchor_edit_button.setMinimumHeight(MIN_TARGET_SIZE)
        self.anchor_edit_button.setAccessibleName("调整模型站立锚点")
        self.anchor_edit_button.setAccessibleDescription(
            "选中后可在完整画布预览中点击或拖动十字标记；"
            "再次点击返回窗口范围编辑"
        )
        layout.addWidget(self.anchor_edit_button)

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
        for edge, control in self._edge_inputs():
            control.valueChanged.connect(
                lambda value, current_edge=edge: self._on_input_changed(
                    current_edge,
                    value,
                )
            )
        self.crop_enabled.toggled.connect(self._on_enabled_changed)
        self.anchor_edit_button.toggled.connect(self._on_anchor_edit_toggled)
        self.anchor_x_input.valueChanged.connect(self._on_anchor_input_changed)
        self.anchor_y_input.valueChanged.connect(self._on_anchor_input_changed)
        self.reset_button.clicked.connect(self.restore_recommended)
        self.full_canvas_button.clicked.connect(self.use_full_canvas)
        self.anchor_reset_button.clicked.connect(self.reset_placement_anchor)
        self.set_window_mask(DEFAULT_LIVE2D_WINDOW_MASK)
        self.set_placement_anchor(DEFAULT_LIVE2D_PLACEMENT_ANCHOR)

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

    def _on_anchor_input_changed(self, _value: float) -> None:
        if self._syncing:
            return
        self._set_anchor(self.placement_anchor())
        self.changed.emit()

    def _on_anchor_edit_toggled(self, editing_anchor: bool) -> None:
        self.editor.set_edit_mode("anchor" if editing_anchor else "viewport")
        self.anchor_edit_button.setText(
            "正在调整站立锚点" if editing_anchor else "调整模型站立锚点"
        )
        self._update_status()

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

    def _update_enabled_state(self) -> None:
        enabled = self.crop_enabled.isChecked()
        self.editor.set_crop_enabled(enabled)
        for _edge, control in self._edge_inputs():
            control.setEnabled(enabled)

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
        if self.anchor_edit_button.isChecked():
            text += " 当前可在预览中点击或拖动站立锚点。"
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
    "MIN_VIEWPORT_SPAN",
    "constrain_viewport_edges",
    "viewport_edges_to_window_mask",
    "window_mask_to_viewport_edges",
]
