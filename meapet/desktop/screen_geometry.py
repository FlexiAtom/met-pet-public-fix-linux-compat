"""浮层/弹窗的屏幕边界工具。

桌宠可以被拖到屏幕任意角落，因此所有以桌宠为锚点弹出的窗口（消息编辑器、
状态面板、菜单窗口、对话框等）都必须先判断目标位置是否越过屏幕边框，
否则窗口会跑到屏幕外——在无边框置顶窗口上这等于彻底消失。

这里的函数分两层：

- 纯计算（``clamp_position`` / ``calculate_popup_position`` /
  ``calculate_centered_position``）：只吃 ``QRect`` / ``QSize``，方便测试；
- Qt 便利层（``available_geometry_for`` / ``move_within_screen``）：查询
  锚点所在屏幕的可用区域（已排除任务栏），并把窗口真正移动过去。
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect, QSize
from PyQt5.QtWidgets import QApplication, QWIDGETSIZE_MAX

# 与屏幕可用区域边缘保持的最小空隙，避免窗口贴边或压住任务栏圆角。
POPUP_SCREEN_MARGIN = 8
# 弹窗与锚点（通常是桌宠窗口）之间的间距。
POPUP_ANCHOR_GAP = 12

# 首选方位放不下时的退让顺序。
_PLACEMENT_FALLBACKS = {
    "above": ("above", "below", "right", "left"),
    "below": ("below", "above", "right", "left"),
    "right": ("right", "left", "below", "above"),
    "left": ("left", "right", "below", "above"),
}


def _safe_area(area: QRect, margin: int) -> QRect:
    """收缩出安全区；屏幕比留白还小时退回原始区域。"""
    safe = area.adjusted(margin, margin, -margin, -margin)
    if safe.width() <= 0 or safe.height() <= 0:
        return QRect(area)
    return safe


def clamp_position(
    position: QPoint,
    size: QSize,
    area: QRect,
    *,
    margin: int = POPUP_SCREEN_MARGIN,
) -> QPoint:
    """把左上角坐标夹进可用区域，保证窗口尽可能完整可见。

    窗口比屏幕还大时对齐到安全区左上角（右/下溢出），这与系统弹窗一致。
    """
    if area is None or area.isEmpty():
        return QPoint(position)
    safe = _safe_area(area, margin)
    max_x = max(safe.left(), safe.right() - size.width() + 1)
    max_y = max(safe.top(), safe.bottom() - size.height() + 1)
    x = min(max(position.x(), safe.left()), max_x)
    y = min(max(position.y(), safe.top()), max_y)
    return QPoint(x, y)


def _placement_origin(
    placement: str, anchor: QRect, size: QSize, gap: int
) -> QPoint:
    centered_x = anchor.center().x() - size.width() // 2
    if placement == "above":
        return QPoint(centered_x, anchor.top() - gap - size.height())
    if placement == "below":
        return QPoint(centered_x, anchor.bottom() + 1 + gap)
    if placement == "left":
        return QPoint(anchor.left() - gap - size.width(), anchor.top())
    return QPoint(anchor.right() + 1 + gap, anchor.top())


def calculate_popup_position(
    anchor: QRect,
    size: QSize,
    area: QRect,
    *,
    placement: str = "above",
    gap: int = POPUP_ANCHOR_GAP,
    margin: int = POPUP_SCREEN_MARGIN,
) -> QPoint:
    """在锚点旁挑一个完整落在屏幕内的位置。

    依次尝试首选方位及其退让顺序；四个方位都放不下时（例如小屏幕上的大窗口），
    按首选方位摆放后再夹进安全区。
    """
    if area is None or area.isEmpty():
        return _placement_origin(placement, anchor, size, gap)
    safe = _safe_area(area, margin)
    order = _PLACEMENT_FALLBACKS.get(placement, _PLACEMENT_FALLBACKS["above"])
    for candidate in order:
        origin = _placement_origin(candidate, anchor, size, gap)
        if safe.contains(QRect(origin, size)):
            return origin
    return clamp_position(
        _placement_origin(placement, anchor, size, gap),
        size,
        area,
        margin=margin,
    )


def calculate_centered_position(
    anchor: QRect,
    size: QSize,
    area: QRect,
    *,
    margin: int = POPUP_SCREEN_MARGIN,
) -> QPoint:
    """以锚点为中心摆放，并夹进屏幕可用区域。"""
    origin = QPoint(
        anchor.center().x() - size.width() // 2,
        anchor.center().y() - size.height() // 2,
    )
    return clamp_position(origin, size, area, margin=margin)


def overlap_area(rect: QRect, other: QRect) -> int:
    """两个矩形的重叠面积（像素）。"""
    overlap = rect.intersected(other)
    return max(0, overlap.width()) * max(0, overlap.height())


def choose_screen_index(rect: QRect, geometries) -> int | None:
    """窗口属于哪块屏幕：先看中心点，再看重叠面积。

    返回 ``None`` 表示窗口完全落在所有屏幕之外（分辨率调小、显示器被拔掉），
    调用方应回退到主屏并把窗口拉回可视范围。
    """
    areas = list(geometries)
    if not areas:
        return None
    center = rect.center()
    for index, geometry in enumerate(areas):
        if geometry.contains(center):
            return index
    best_index = None
    best_area = 0
    for index, geometry in enumerate(areas):
        area = overlap_area(rect, geometry)
        if area > best_area:
            best_index = index
            best_area = area
    return best_index


def is_sufficiently_visible(
    rect: QRect, area: QRect, *, ratio: float = 0.5
) -> bool:
    """窗口在给定区域内是否露出足够多（宽高各占 ``ratio``）。

    用于判断“需不需要把窗口拉回来”，避免把用户故意贴边/压在任务栏上的窗口
    在每次屏幕变化时都挪动一下。
    """
    if area is None or area.isEmpty() or rect.isEmpty():
        return False
    visible = rect.intersected(area)
    if visible.isEmpty():
        return False
    return (
        visible.width() >= rect.width() * ratio
        and visible.height() >= rect.height() * ratio
    )


def screen_bounds_for_rect(rect: QRect):
    """返回窗口所属屏幕的 ``(整屏几何, 可用区域)``；无法确定时回退主屏。

    可见性判断用整屏几何（压在任务栏上的桌宠仍然是可见的），把窗口拉回来时
    用可用区域（避免藏到任务栏后面）。
    """
    app = QApplication.instance()
    if app is None:
        return None
    screens = [screen for screen in app.screens() if screen is not None]
    if not screens:
        return None
    index = choose_screen_index(rect, [screen.geometry() for screen in screens])
    if index is None:
        screen = app.primaryScreen() or screens[0]
    else:
        screen = screens[index]
    try:
        return screen.geometry(), screen.availableGeometry()
    except RuntimeError:
        return None


def available_geometry_for(reference=None) -> QRect | None:
    """返回参考点/矩形所在屏幕的可用区域；无法确定时回退主屏。

    ``reference`` 可以是 ``QPoint``、``QRect`` 或带 ``geometry()`` 的窗口对象。
    没有 QApplication（纯逻辑测试）时返回 ``None``，调用方应保留原有行为。
    """
    if QApplication.instance() is None:
        return None

    point: QPoint | None = None
    if isinstance(reference, QRect):
        point = reference.center()
    elif isinstance(reference, QPoint):
        point = QPoint(reference)
    elif reference is not None:
        geometry = getattr(reference, "frameGeometry", None) or getattr(
            reference, "geometry", None
        )
        if callable(geometry):
            try:
                rect = geometry()
            except (RuntimeError, TypeError):
                rect = None
            if isinstance(rect, QRect):
                point = rect.center()

    screen = None
    if point is not None:
        try:
            screen = QApplication.screenAt(point)
        except (AttributeError, RuntimeError):
            screen = None
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:
        return None
    try:
        return screen.availableGeometry()
    except RuntimeError:
        return None


def widget_size(widget) -> QSize:
    """取窗口当前尺寸；尚未布局时退回 sizeHint。"""
    width = int(widget.width())
    height = int(widget.height())
    if width <= 1 or height <= 1:
        hint = getattr(widget, "sizeHint", None)
        if callable(hint):
            suggested = hint()
            if isinstance(suggested, QSize) and suggested.isValid():
                width = max(width, suggested.width())
                height = max(height, suggested.height())
    return QSize(max(1, width), max(1, height))


def resize_dialog_to_content(
    dialog,
    preferred: QSize,
    *,
    reference=None,
    margin: int = POPUP_SCREEN_MARGIN,
) -> QSize:
    """按布局提示调整弹窗，并限制在当前屏幕可用区域内。

    ``setFixedSize`` 会在字体放大后把文字和按钮裁掉，也会让 Windows 原生
    窗口管理器拒绝 Qt 请求的几何尺寸。本函数先解除固定约束，再取
    ``sizeHint`` / ``minimumSizeHint`` / 设计基准三者的最大值，最后按任务栏
    之外的可用区域收敛。返回实际请求的尺寸，便于调用方二次定位。
    """
    dialog.setMinimumSize(0, 0)
    dialog.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)

    layout = dialog.layout()
    if layout is not None:
        layout.invalidate()
        layout.activate()
    dialog.updateGeometry()

    def desired_size() -> QSize:
        hint = dialog.sizeHint()
        minimum_hint = dialog.minimumSizeHint()
        return QSize(
            max(
                int(preferred.width()),
                int(hint.width()),
                int(minimum_hint.width()),
                int(dialog.minimumWidth()),
            ),
            max(
                int(preferred.height()),
                int(hint.height()),
                int(minimum_hint.height()),
                int(dialog.minimumHeight()),
            ),
        )

    desired = desired_size()
    area = available_geometry_for(
        reference if reference is not None else dialog.parentWidget()
    )
    if area is not None and not area.isEmpty():
        safe = _safe_area(area, margin)
        desired.setWidth(min(desired.width(), safe.width()))
        desired.setHeight(min(desired.height(), safe.height()))

    dialog.resize(desired.width(), desired.height())

    # Word-wrap 的高度提示依赖最终宽度；用新宽度激活一次布局后再校正高度。
    if layout is not None:
        layout.invalidate()
        layout.activate()
    second_pass = desired_size()
    second_pass.setWidth(desired.width())
    if area is not None and not area.isEmpty():
        safe = _safe_area(area, margin)
        second_pass.setHeight(min(second_pass.height(), safe.height()))
    dialog.resize(second_pass.width(), second_pass.height())
    return QSize(second_pass)


def move_within_screen(
    widget,
    position: QPoint,
    *,
    reference=None,
    margin: int = POPUP_SCREEN_MARGIN,
) -> QPoint:
    """把窗口移动到夹进屏幕可用区域后的坐标，并返回该坐标。"""
    size = widget_size(widget)
    area = available_geometry_for(
        reference if reference is not None else QRect(position, size)
    )
    target = (
        QPoint(position)
        if area is None
        else clamp_position(position, size, area, margin=margin)
    )
    widget.move(target)
    return target
