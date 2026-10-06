"""PNG / Live2D render host: switch modes, size, hit region, standby.

Live2D 保留完整模型画布用于渲染动作，但顶层桌宠窗口只暴露配置的视觉视口：
- OpenGL 子控件尺寸 = 模型画布尺寸 × size_factor
- 顶层窗口尺寸 = ``live2d.window_mask`` 外接矩形对应的视觉区域
- 子控件通过负偏移放在顶层窗口后方，由普通父子窗口矩形裁剪透明留白
- ``live2d.window_shape`` 可选地把多个保留/挖空多边形转换为静态 QRegion
- 不恢复历史椭圆窗形，也不使用 OpenGL stencil；形状关闭时保留普通矩形窗口
"""
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

from PyQt5.QtWidgets import QApplication, QDialog
from PyQt5.QtCore import QEventLoop, QPoint, QRect, QSize, Qt, QTimer
from PyQt5.QtGui import QImage, QPolygon, QRegion

from meapet.config.store import (
    normalize_live2d_placement_anchor,
    normalize_live2d_window_mask,
    normalize_live2d_window_shape,
)
from meapet.config.defaults import bubble_duration_ms
from meapet.desktop.renderer import SpriteCanvas, SpriteRenderer
from meapet.desktop.widgets import (
    SizeScaleDialog,
    calculate_bubble_stack_opacities,
)
from meapet.desktop import status_language
from meapet.desktop.screen_geometry import (
    available_geometry_for,
    calculate_centered_position,
    clamp_position,
    is_sufficiently_visible,
    move_within_screen,
    screen_bounds_for_rect,
    widget_size,
)
from meapet.desktop.click_through import (
    ClickThroughState,
    RightClickEdgeDetector,
    disable_click_through,
    enable_click_through,
    is_right_button_down,
)
from meapet.ui_theme import normalize_pet_size_factor
from meapet.utils import safe_print
from meapet.window_state import load_pet_position, save_pet_position


BUBBLE_SCREEN_MARGIN = 24
BUBBLE_PET_GAP = 12
BUBBLE_STACK_GAP = 8
BUBBLE_HEAD_ANCHOR_RATIO = 0.16
BUBBLE_TAIL_CORNER_INSET = 36
LIVE2D_STARTUP_TIMEOUT_MS = 5000
LIVE2D_PREVIEW_MAX_SOURCE_PIXELS = 8_000_000
LIVE2D_PREVIEW_MAX_EDGE = 1600
# fidus 定位的 GUI 侧节拍：16 ms 搬运跨线程请求，摆位后连等 12 拍（≈200 ms、
# 覆盖 ~6 个 33 ms 推帧）让合成器真的收到新帧，才放行 fidus 线程的下一发读数。
_FIDUS_POLL_MS = 16
_FIDUS_SETTLE_TICKS = 12
# 穿透挂载后"回读—夹移"闭环的预算（工作项 #78，接口见 §7.1 #12）。节拍就是
# _layer_timer 的 33 ms，不新增定时器：configure 由桥接层的泵线程异步送达，
# 实测 1 拍内落地，12 拍（≈400 ms）是给慢合成器的余量。
# 夹移只发**一发**：那一发是按模型算出来的全部结论，模型不成立时第二发就是猜——不猜，出声。
_LAYER_FIT_WAIT_TICKS = 12
# 稳态重查的节流（工作项 #89 · 裁决 (b)）：回读轮落定之后，每这么多拍读一次
# `logical_size()`，只跟"上一次读到的那一份"比，变了才重算那一发夹移。
# 30 拍 ≈ 1 s：夹移的失效是 180 px 级的静默错位，一秒的滞后换得起；每拍一读则是
# 把 #78 §8.1 那条"稳态零次"的成本口径按整个穿透期打折，那不打。
_LAYER_FIT_WATCH_TICKS = 30
# 「量完再挂」那一发的等待预算（人工裁决 2026-10-02：20 秒，宽）。口径是 H25 十四场的
# 墙钟：`calibrate` 2820–4090 ms + 单发 `locate` 916–9377 ms ⇒ 最坏 13.5 s，留出 6.5 s；
# **同一预算里装不下第二发**（22.8 s > 20 s），所以超时即按裁决 1 走「不切 + 出声」，
# 不靠预算兜重试。
_MOUNT_WAIT_MS = 20_000
# 挂载请求按**不透明轮廓**而不是整画布发（丙，人工 2026-10-03「都做」）时给动画留的余量。
# 轮廓是挂载那一帧量的，而 Live2D 的呼吸/摆手会把内容推出这个框：不留余量 ⇒ 动作一到
# 框外就被 `_layer_conform_to_configure` 裁掉。16 px 是**未量过**的幅度 allowance，不是读数；
# 真觉得切手了，加大这一格比改算法便宜。
_LAYER_BBOX_PAD_PX = 16
# 轮廓短到这条以下不当它是轮廓：多半是那一帧正空着（换模型、动作过零、渲染还没热）。
# 宁可多要一块放不下的透明边（有声），也不要挂出一只看不见的小 surface。
_LAYER_MIN_SURFACE_EDGE = 32
# 一次分辨率变更会连发多个屏幕信号，合并后再校正位置。
SCREEN_GUARD_DEBOUNCE_MS = 400
# 桌宠宽高各至少露出这么多比例才算「还看得见」，否则拉回可用区域。
PET_MIN_VISIBLE_RATIO = 0.5

# Live2D 模型画布尺寸的合法范围（像素）。
# 低于此值视为 SDK 返回了无效占位值（如 1x2），需要回退。
MIN_CANVAS_SIZE = 256
# 高于此值视为异常（防止 4K 模型撑爆屏幕），会按比例缩放到合理范围。
MAX_CANVAS_SIZE = 4096
# 当模型画布异常时使用的默认尺寸。
DEFAULT_CANVAS_SIZE = (1024, 1024)


@dataclass(frozen=True)
class Live2DViewportLayout:
    """完整 Live2D 画布子控件与顶层视觉窗口的像素几何。"""

    widget_x: int
    widget_y: int
    widget_width: int
    widget_height: int
    window_width: int
    window_height: int


def calculate_live2d_window_region(
    layout: Live2DViewportLayout,
    window_shape: object,
) -> QRegion | None:
    """把完整画布归一化多轮廓转换为裁剪窗口本地 ``QRegion``。"""
    shape = normalize_live2d_window_shape(window_shape)
    if not shape["enabled"]:
        return None

    additions = QRegion()
    subtractions = []
    has_addition = False
    for contour in shape["contours"]:
        polygon = QPolygon(
            [
                QPoint(
                    layout.widget_x
                    + round(layout.widget_width * float(point[0])),
                    layout.widget_y
                    + round(layout.widget_height * float(point[1])),
                )
                for point in contour["points"]
            ]
        )
        contour_region = QRegion(polygon, Qt.OddEvenFill)
        if contour_region.isEmpty():
            continue
        if contour["operation"] == "add":
            additions = additions.united(contour_region)
            has_addition = True
        else:
            subtractions.append(contour_region)

    if not has_addition or additions.isEmpty():
        return None
    for subtraction in subtractions:
        additions = additions.subtracted(subtraction)

    window_bounds = QRegion(
        QRect(0, 0, layout.window_width, layout.window_height)
    )
    region = additions.intersected(window_bounds)
    return None if region.isEmpty() else region


def calculate_live2d_viewport_layout(
    canvas_width: int,
    canvas_height: int,
    factor: float,
    window_mask: dict | None = None,
) -> Live2DViewportLayout:
    """把模型画布与视觉视口换算成稳定的父子窗口几何。

    ``window_mask`` 是已有配置键。这里不恢复椭圆窗口 mask，只使用椭圆
    的外接矩形作为模型专属视觉视口；这样能裁去已知透明留白，同时保留
    外接矩形四角供头发、耳朵等动作伸展。关闭该配置时完整显示模型画布。
    """
    width = max(1, int(canvas_width))
    height = max(1, int(canvas_height))
    scale = normalize_pet_size_factor(factor)
    widget_width = max(MIN_CANVAS_SIZE // 2, round(width * scale))
    widget_height = max(MIN_CANVAS_SIZE // 2, round(height * scale))

    mask = normalize_live2d_window_mask(window_mask)
    if not mask["enabled"]:
        return Live2DViewportLayout(
            widget_x=0,
            widget_y=0,
            widget_width=widget_width,
            widget_height=widget_height,
            window_width=widget_width,
            window_height=widget_height,
        )

    left_ratio = max(0.0, float(mask["cx"]) - float(mask["rw"]))
    top_ratio = max(0.0, float(mask["cy"]) - float(mask["rh"]))
    right_ratio = min(1.0, float(mask["cx"]) + float(mask["rw"]))
    bottom_ratio = min(1.0, float(mask["cy"]) + float(mask["rh"]))

    crop_left = max(0, min(widget_width - 1, round(widget_width * left_ratio)))
    crop_top = max(0, min(widget_height - 1, round(widget_height * top_ratio)))
    crop_right = max(
        crop_left + 1,
        min(widget_width, round(widget_width * right_ratio)),
    )
    crop_bottom = max(
        crop_top + 1,
        min(widget_height, round(widget_height * bottom_ratio)),
    )
    return Live2DViewportLayout(
        widget_x=-crop_left,
        widget_y=-crop_top,
        widget_width=widget_width,
        widget_height=widget_height,
        window_width=crop_right - crop_left,
        window_height=crop_bottom - crop_top,
    )


def calculate_drag_position(
    window_origin: QPoint,
    pointer_origin: QPoint,
    current_pointer: QPoint,
) -> QPoint:
    """根据一次按下时的固定全局锚点计算窗口位置，避免增量累计漂移。"""
    return window_origin + current_pointer - pointer_origin


def calculate_live2d_anchor_preserving_position(
    window_origin: QPoint,
    before_canvas: QRect,
    after_canvas: QRect,
    placement_anchor: object,
) -> QPoint:
    """计算几何变化后仍让同一画布锚点留在原屏幕位置的窗口坐标。"""
    anchor = normalize_live2d_placement_anchor(placement_anchor)

    def local_point(canvas: QRect) -> QPoint:
        return QPoint(
            canvas.x() + round(canvas.width() * anchor["x"]),
            canvas.y() + round(canvas.height() * anchor["y"]),
        )

    return QPoint(window_origin) + local_point(before_canvas) - local_point(
        after_canvas
    )


def _rect_from_center(cx: float, cy: float, w: int, h: int) -> tuple[int, int, int, int]:
    """把「中心 + 尺寸」反解成挂载矩形 `(x, y, w, h)`。

    两处共用这一条公式，不许分叉：预挂载拿 fidus 量出来的屏幕中心去 `enable(...)`，
    与挂载后 `_fidus_service` 拿同一个读数去 `set_position(...)`，吃的都是这一个减法。
    """
    return (int(round(cx - w / 2.0)), int(round(cy - h / 2.0)), int(w), int(h))


def calculate_bubble_anchor_rect(
    pet_window_rect: QRect,
    visible_local_rect: QRect | None = None,
) -> QRect:
    """把桌宠窗口内的可见区域转换成用于气泡定位的全局矩形。

    Live2D 顶层窗口已经是裁去透明留白后的视觉视口；PNG 顶层窗口则与
    精灵帧一致，因此两种模式都可以直接使用窗口矩形作为默认锚点。
    """
    window = QRect(pet_window_rect)
    if visible_local_rect is None or visible_local_rect.isEmpty():
        return window
    local_window = QRect(QPoint(0, 0), window.size())
    visible = QRect(visible_local_rect).intersected(local_window)
    if visible.isEmpty():
        return window
    visible.translate(window.topLeft())
    return visible


def calculate_bubble_position(
    pet_rect: QRect,
    bubble_size: QSize,
    screen_rect: QRect,
    *,
    margin: int = BUBBLE_SCREEN_MARGIN,
    gap: int = BUBBLE_PET_GAP,
    avoid_rects: tuple[QRect, ...] = (),
) -> QPoint:
    """在屏幕安全区内放置气泡，并避开桌宠及其他浮层。"""
    safe = screen_rect.adjusted(margin, margin, -margin, -margin)
    width = bubble_size.width()
    height = bubble_size.height()
    centered_x = pet_rect.center().x() - width // 2
    head_anchor_y = (
        pet_rect.top() + int(pet_rect.height() * BUBBLE_HEAD_ANCHOR_RATIO)
    )
    upper_y = head_anchor_y - height // 2
    left = QPoint(pet_rect.left() - gap - width, upper_y)
    right = QPoint(pet_rect.right() + gap + 1, upper_y)
    top = QPoint(centered_x, pet_rect.top() - gap - height)
    bottom = QPoint(centered_x, pet_rect.bottom() + gap + 1)
    if pet_rect.center().x() >= safe.center().x():
        candidates = (left, right, top, bottom)
    else:
        candidates = (right, left, top, bottom)
    blocked_rects = (pet_rect.adjusted(-gap, -gap, gap, gap),) + tuple(
        rect.adjusted(-gap, -gap, gap, gap)
        for rect in avoid_rects
        if not rect.isEmpty()
    )

    def is_clear(candidate: QPoint) -> bool:
        candidate_rect = QRect(candidate, bubble_size)
        return safe.contains(candidate_rect) and not any(
            candidate_rect.intersects(blocked) for blocked in blocked_rects
        )

    for candidate in candidates:
        if is_clear(candidate):
            return candidate

    max_x = safe.right() - width + 1
    max_y = safe.bottom() - height + 1

    def clamped(candidate: QPoint) -> QPoint:
        x = (
            safe.left()
            if max_x < safe.left()
            else min(max(candidate.x(), safe.left()), max_x)
        )
        y = (
            safe.top()
            if max_y < safe.top()
            else min(max(candidate.y(), safe.top()), max_y)
        )
        return QPoint(x, y)

    clamped_candidates = tuple(clamped(candidate) for candidate in candidates)
    for candidate in clamped_candidates:
        candidate_rect = QRect(candidate, bubble_size)
        if not any(
            candidate_rect.intersects(blocked) for blocked in blocked_rects
        ):
            return candidate

    for candidate in clamped_candidates:
        adjusted_candidates = []
        for blocked in blocked_rects:
            adjusted_candidates.extend(
                (
                    QPoint(blocked.left() - width, candidate.y()),
                    QPoint(blocked.right() + 1, candidate.y()),
                    QPoint(candidate.x(), blocked.top() - height),
                    QPoint(candidate.x(), blocked.bottom() + 1),
                )
            )
        for adjusted in adjusted_candidates:
            adjusted = clamped(adjusted)
            adjusted_rect = QRect(adjusted, bubble_size)
            if safe.contains(adjusted_rect) and not any(
                adjusted_rect.intersects(blocked)
                for blocked in blocked_rects
            ):
                return adjusted

    def overlap_area(candidate: QPoint) -> int:
        candidate_rect = QRect(candidate, bubble_size)
        return sum(
            max(0, overlap.width()) * max(0, overlap.height())
            for blocked in blocked_rects
            for overlap in (candidate_rect.intersected(blocked),)
        )

    return min(clamped_candidates, key=overlap_area)


def calculate_bubble_stack_positions(
    pet_rect: QRect,
    bubble_sizes: tuple[QSize, ...],
    screen_rect: QRect,
    *,
    margin: int = BUBBLE_SCREEN_MARGIN,
    gap: int = BUBBLE_PET_GAP,
    stack_gap: int = BUBBLE_STACK_GAP,
    avoid_rects: tuple[QRect, ...] = (),
) -> tuple[QPoint, ...]:
    """按"最旧到最新"返回气泡位置，最新靠近角色、旧消息向上堆叠。"""
    sizes = tuple(bubble_sizes)
    if not sizes:
        return ()

    safe = screen_rect.adjusted(margin, margin, -margin, -margin)
    blocked_rects = (pet_rect.adjusted(-gap, -gap, gap, gap),) + tuple(
        rect.adjusted(-gap, -gap, gap, gap)
        for rect in avoid_rects
        if not rect.isEmpty()
    )
    head_anchor_y = (
        pet_rect.top() + int(pet_rect.height() * BUBBLE_HEAD_ANCHOR_RATIO)
    )

    def vertical_positions(newest_y: int) -> list[int]:
        positions = [0] * len(sizes)
        positions[-1] = newest_y
        for index in range(len(sizes) - 2, -1, -1):
            positions[index] = (
                positions[index + 1]
                - stack_gap
                - sizes[index].height()
            )

        group_top = positions[0]
        group_bottom = positions[-1] + sizes[-1].height() - 1
        if group_top < safe.top():
            shift = safe.top() - group_top
            positions = [value + shift for value in positions]
            group_bottom += shift
        if group_bottom > safe.bottom():
            shift = safe.bottom() - group_bottom
            positions = [value + shift for value in positions]
        return positions

    newest_upper_y = head_anchor_y - sizes[-1].height() // 2
    upper_positions = vertical_positions(newest_upper_y)

    def side_positions(side: str) -> tuple[QPoint, ...]:
        if side == "left":
            return tuple(
                QPoint(pet_rect.left() - gap - size.width(), y)
                for size, y in zip(sizes, upper_positions)
            )
        return tuple(
            QPoint(pet_rect.right() + gap + 1, y)
            for y in upper_positions
        )

    def is_clear(positions: tuple[QPoint, ...]) -> bool:
        rects = tuple(
            QRect(position, size)
            for position, size in zip(positions, sizes)
        )
        return all(safe.contains(rect) for rect in rects) and not any(
            rect.intersects(blocked)
            for rect in rects
            for blocked in blocked_rects
        )

    preferred_sides = (
        ("left", "right")
        if pet_rect.center().x() >= safe.center().x()
        else ("right", "left")
    )
    for side in preferred_sides:
        positions = side_positions(side)
        if is_clear(positions):
            return positions

    latest_position = calculate_bubble_position(
        pet_rect,
        sizes[-1],
        screen_rect,
        margin=margin,
        gap=gap,
        avoid_rects=avoid_rects,
    )
    latest_rect = QRect(latest_position, sizes[-1])
    fallback_y = vertical_positions(latest_position.y())
    if latest_rect.right() < pet_rect.left():
        fallback_side = "left"
    elif latest_rect.left() > pet_rect.right():
        fallback_side = "right"
    else:
        fallback_side = "center"

    positions = []
    for size, y in zip(sizes, fallback_y):
        if fallback_side == "left":
            x = (
                latest_position.x()
                + sizes[-1].width()
                - size.width()
            )
        elif fallback_side == "right":
            x = latest_position.x()
        else:
            x = pet_rect.center().x() - size.width() // 2
        max_x = safe.right() - size.width() + 1
        if max_x >= safe.left():
            x = min(max(x, safe.left()), max_x)
        positions.append(QPoint(x, y))
    return tuple(positions)


def calculate_bubble_tail(pet_rect: QRect, bubble_rect: QRect) -> tuple[str, int]:
    """返回气泡朝向桌宠的尾巴边与相对锚点。"""
    pet_center_x = pet_rect.x() + pet_rect.width() // 2
    corner_inset = min(
        BUBBLE_TAIL_CORNER_INSET,
        max(0, bubble_rect.width() // 2),
    )
    if bubble_rect.right() < pet_rect.left():
        return "bottom", bubble_rect.width() - corner_inset
    if bubble_rect.left() > pet_rect.right():
        return "bottom", corner_inset
    if bubble_rect.bottom() < pet_rect.top():
        return "bottom", pet_center_x - bubble_rect.left()
    return "top", pet_center_x - bubble_rect.left()


class PetRenderHostMixin:
    def _init_renderer(self):
        """直接初始化目标渲染器；Live2D 首帧完成前保持顶层窗口透明。"""
        display_cfg = self.config.get("display", {})
        self._scale = display_cfg.get("scale", 0.5)
        self._size_factor = display_cfg.get("size_factor", 1.0)

        self._use_live2d = False
        self._l2d_model = None
        self._l2d_pending = False
        self._live2d_startup_widget = None
        self._cancel_live2d_startup_timeout()
        self._ensure_live2d_startup_timer()
        self._renderer_ready = False
        self._renderer_ready_callbacks: list[Callable[[], None]] = []
        self.renderer = None
        self.sprite_label = None

        from meapet.config.store import resolve_resource_path

        l2d_cfg = self.config.get("live2d", {})
        model_dir = resolve_resource_path(l2d_cfg.get("model_dir", ""))
        force_png = os.environ.get("MEA_PET_FORCE_PNG", "").strip().lower()
        live2d_requested = (
            force_png not in ("1", "true", "yes")
            and l2d_cfg.get("enabled", False)
            and bool(model_dir)
            and os.path.isdir(model_dir)
        )

        if live2d_requested:
            self.setWindowOpacity(1.0)
            try:
                self._start_live2d_renderer()
                return
            except Exception as exc:
                safe_print(f"[pet] Live2D 初始化失败，使用 PNG: {exc}")
                self._fallback_to_png(str(exc))
                return

        if force_png in ("1", "true", "yes"):
            safe_print("[toggle] MEA_PET_FORCE_PNG=1, skip Live2D")
        elif l2d_cfg.get("enabled", False) and not (model_dir and os.path.isdir(model_dir)):
            safe_print(f"[live2d] 模型目录不存在，使用 PNG: {model_dir}")

        self._init_png_renderer()
        self.setWindowOpacity(1.0)
        self._mark_renderer_ready()

    def _init_png_renderer(self):
        """创建 PNG 渲染器；仅用于明确选择 PNG 或 Live2D 失败回退。"""
        self.clearMask()
        char = self.config.get("character", {})
        sprite_dir = self.config.get(
            "sprite_dir",
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sprites"),
        )
        if not os.path.isdir(sprite_dir):
            from meapet.paths import project_path
            sprite_dir = project_path("sprites")
        outfit = char.get("default_outfit", "01")
        direction = char.get("default_direction", "A")
        self.sprite_label = SpriteCanvas(self)
        self.sprite_label.setAttribute(Qt.WA_TranslucentBackground)
        self.sprite_label.setStyleSheet("background: transparent;")
        self.sprite_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.sprite_label.show()
        self.renderer = SpriteRenderer(sprite_dir, outfit, direction)
        safe_print(f"[toggle] PNG renderer 创建成功: {self.renderer is not None}")
        self.renderer.expression_changed.connect(self._on_sprite_changed)
        self._update_sprite()
        if hasattr(self.renderer, "preload_scaled_frames"):
            self.renderer.preload_scaled_frames(
                self.sprite_label.width(),
                self.sprite_label.height(),
            )
        self.renderer.start_blink_animation()

    def _start_live2d_renderer(self):
        """创建 Live2D 控件，但把可见性推迟到它报告真实首帧以后。"""
        from meapet.desktop.live2d_widget import init_live2d

        init_live2d()
        self._use_live2d = True
        self._l2d_pending = True
        self._renderer_ready = False
        self._init_live2d()
        widget = self.sprite_label
        if widget is None:
            raise RuntimeError("Live2D widget not created")
        self._live2d_startup_widget = widget
        # 超时预算从「事件循环真正开始转」起算，而不是从这里起算（agents-rules §2 终局推演）。
        #   * 为什么要推迟起表 = QTimer 只能在循环里派发。构造 MeaPet 的同步初始化
        #     （chat/tts/watcher/DB…）实测可耗时 >LIVE2D_STARTUP_TIMEOUT_MS，于是定时器
        #     在循环启动的那一刻**就已过期**，第一次迭代即触发 → 控件还没来得及画首帧
        #     就被判"加载失败"。后果是**响亮但错误**的降级：本机 L3 实测冷启动一次
        #     复现（初始化 9 s > 5 s ⇒ 回退 PNG），热启动两次都正常 ⇒ 间歇性。
        #   * 什么条件下它会崩 = 首帧永不到来且 `initialization_failed` 也不发
        #     （OpenGL 挂死在 paint 内部、把循环本身堵死）⇒ 定时器同样无法触发，
        #     与改动前**完全一致**（旧写法在这种情况下也同样测不到），不是新增盲区。
        #   * 兜底 = `singleShot(0)` 是投递给循环的第一个事件，因此 5 s 预算只可能被
        #     **渲染**耗掉、不可能被初始化耗掉；起表前先看 `_l2d_pending`，
        #     若首帧/取消已在此之前发生就不起表（避免把已取消的定时器重新点着）。
        QTimer.singleShot(0, self._arm_live2d_startup_timeout)

    def _arm_live2d_startup_timeout(self) -> None:
        """事件循环启动后才开始计首帧预算；已不再等待则不起表。"""
        if not getattr(self, "_l2d_pending", False):
            return
        self._ensure_live2d_startup_timer().start(LIVE2D_STARTUP_TIMEOUT_MS)

    def _ensure_live2d_startup_timer(self) -> QTimer:
        timer = getattr(self, "_live2d_startup_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._on_live2d_startup_timeout)
            self._live2d_startup_timer = timer
        return timer

    def _cancel_live2d_startup_timeout(self) -> None:
        timer = getattr(self, "_live2d_startup_timer", None)
        if timer is not None:
            timer.stop()

    def _deferred_init_live2d(self):
        """兼容旧调用点；新启动流程不再用 800ms 的 PNG 中间态。"""
        if self._use_live2d or not self._l2d_pending:
            return
        self._start_live2d_renderer()

    def when_renderer_ready(self, callback: Callable[[], None]):
        """在渲染器可安全显示时调用 callback；已就绪时立即调用。"""
        if self._renderer_ready:
            callback()
            return
        self._renderer_ready_callbacks.append(callback)

    def _mark_renderer_ready(self):
        if self._renderer_ready:
            return
        self._renderer_ready = True
        callbacks = tuple(self._renderer_ready_callbacks)
        self._renderer_ready_callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:
                safe_print(f"[pet] renderer-ready callback failed: {exc}")
        # 穿透开关的挂载点从「Live2D 首帧」提到这里：PNG 分支也走到这一格，否则
        # PNG 模式下面板、后端、推帧定时器全都不存在（人工 2026-10-06「如果没做需要做」）。
        self._init_layer_overlay_mode()

    def _on_live2d_first_frame(self):
        """首帧已经绘制并提交后，调整窗口大小以匹配模型画布，再显现。"""
        if not self._l2d_pending or not self._use_live2d:
            return
        self._cancel_live2d_startup_timeout()
        self._l2d_pending = False
        self._live2d_startup_widget = None

        # 首帧就绪后，根据模型实际画布大小调整窗口尺寸
        try:
            self._fit_window_to_model()
        except Exception as exc:
            safe_print(f"[live2d] fit window to model skipped: {exc}")

        # Live2D 的最终窗口尺寸要到首帧后才确定；此时再按保存坐标夹回
        # 当前屏幕，避免先恢复位置、随后尺寸变化又把桌宠推出屏幕。
        self._restore_pet_position()

        self._reveal_live2d_window()
        self._mark_renderer_ready()
        safe_print(
            f"[pet] Live2D 首帧就绪 size={self.width()}x{self.height()} "
            f"pos=({self.x()},{self.y()})"
        )

    # ------------------------------------------------------------ 双模架构
    def _layer_geometry(self):
        """返回离屏渲染内容应显示的全局矩形 (x, y, w, h)。

        ⚠️ Wayland 客户端无法查询自身全局坐标，mapToGlobal() 不可靠
        （会返回相对值导致负坐标）。必须用「顶层窗口坐标 + 子控件相对偏移」
        手动合成。子控件（完整画布）通常比窗口大且带负偏移。
        """
        widget = getattr(self, "sprite_label", None)
        if widget is None:
            return self.x(), self.y(), self.width(), self.height()
        try:
            # mapTo 是局部映射，可靠；再叠加顶层窗口坐标
            offset = widget.mapTo(self, QPoint(0, 0))
            return (
                self.x() + offset.x(),
                self.y() + offset.y(),
                widget.width(),
                widget.height(),
            )
        except Exception:
            return self.x(), self.y(), self.width(), self.height()

    def _init_layer_overlay_mode(self) -> None:
        # ★ 只在 Linux + Wayland 会话下启用双模
        if sys.platform != "linux":
            return
        try:
            from PyQt5.QtGui import QGuiApplication
            if str(QGuiApplication.platformName() or "").lower() != "wayland":
                return
        except Exception:
            return
        """初始化 layer-shell 双模；不支持时静默保持原有单窗口行为。"""
        if getattr(self, "_layer_inited", False):
            return
        self._layer_inited = True
        try:
            from meapet.desktop.layer_debug_panel import LayerDebugPanel
            from meapet.desktop.wayland_layer import (
                get_backend,
                is_available as layer_available,
            )
        except Exception as exc:
            safe_print(f"[layer] 双模模块导入失败，保持原行为: {exc}")
            return

        try:
            if not layer_available():
                safe_print("[layer] compositor 不支持 layer-shell，保持原行为")
                return
        except Exception as exc:
            safe_print(f"[layer] layer-shell 探测失败: {exc}")
            return

        backend = get_backend()
        self._layer_backend = backend          # ← 只赋值，不创建

        # 推帧定时器：穿透模式下把离屏渲染结果送到 layer surface
        self._layer_timer = QTimer(self)
        self._layer_timer.setInterval(33)  # ~30fps
        self._layer_timer.timeout.connect(self._push_layer_frame)

        # 常驻开关（永远可点，用来切模式）。`penetrate=False` ⇒ 出场文案「关／切到 穿透」，
        # 与"这里不再挂载"的事实同口径——它是切回交互态的唯一入口，说谎的出口比没有出口更糟。
        self._layer_panel = LayerDebugPanel(self._set_layer_mode, penetrate=False)
        self._position_layer_panel()
        self._layer_panel.show()

        # ★ 启动**不**进穿透（人工裁决 2026-10-03「先把启动默认进入穿透改为非穿透」）。
        # 旧码在此处 `_set_layer_mode(True)`，两处坏处：① 用户第一眼的桌宠就不可点、不可拖，
        # 想互动得先去找那只贴边小面板；② 它是 fidus「量完再挂」的隐形入口——启动即抓帧、
        # 即测量、量不出还可能弹模态，全都发生在用户什么都没做的时候。
        # ⇒ `enable()` 的唯一入口现在是面板（`_toggle` → `_set_layer_mode(True)`）。
        #
        # 下面这笔"第二次 enable 会崩"的账整段留着：它约束的是**任何**路径上的 enable 次数，
        # 不是给启动那一句作的辩护。
        #   * 会崩的条件 = 任何第二次 enable()：wayland_layer.py 的 enable() 直接
        #     覆盖 self._ctx 而不销毁旧 ctx，旧 ctx 连同它的 RING_DEPTH 个 memfd
        #     和一个已 map 的 OVERLAY surface 一起变成孤儿；两个 QTimer 也会各自
        #     以 33ms 推帧（等于双倍提交——`QTimer(self)` 的父对象是本窗口，引用被
        #     覆盖不会销毁它，记录 1H 的 L1 探针实测两个定时器都 active）。
        #     面板**不**双份：`LayerDebugPanel` 是无父 QWidget，引用覆盖后旧实例
        #     随之回收 —— 老代码的可见后果是双 ctx 与双推帧，不是两只开关窗口。
        #   * 为什么不能靠 disable() 兜底 = disable() 有 `if self._ctx:` 守卫，
        #     destroy_context() 之后 _ctx 已是 None，整块（含 layer_shell_cleanup）
        #     被跳过，门面层没有任何可达路径回收孤儿。
        #   * 实测 = 2026-09-20 L3：修复前 `niri msg -j layers` 稳定 2 只 meapet、
        #     /proc/<pid>/fd 里 memfd:meapet-px 恒为 6（2 ctx × RING_DEPTH）；
        #     门面级复现 enable×2 → destroy → disable 仍是 1 只 surface / 3 个 fd，
        #     只有直接 layer_shell_cleanup() 才归零。桥接层行为符合 spec §4.7 第 12 行
        #     （destroy 只释放点名的注册表槽位）＋第 9 行（cleanup 连坐全部存活 ctx），
        #     所以这里修调用方，不改桥。
        # 本函数一次 enable 都不发：挂载由面板那一个入口发起。

    def _position_layer_panel(self) -> None:
        """把开关窗口贴到屏幕左下角。"""
        panel = getattr(self, "_layer_panel", None)
        if panel is None:
            return
        try:
            screen = QApplication.primaryScreen()
            geo = screen.availableGeometry() if screen is not None else None
            if geo is not None:
                panel.move(geo.left() + 12, geo.bottom() - panel.height() - 12)
                return
        except Exception:
            pass
        panel.move(20, 20)

    def _set_layer_mode(self, penetrate: bool) -> None:
        backend = getattr(self, "_layer_backend", None)
        if backend is None:
            return

        if penetrate:
            if getattr(self, "_pending_mount", None) is not None:
                # 一次只允许一个待挂：面板连点不该叠两份测量——两发都在写同一组
                # `_fidus_*` 标志，后起的会把前一只的读数吃掉。
                safe_print("[layer] 量完再挂已在路上，本次不重复起工")
                return
            # ① 必须在 hide() 之前取坐标（Wayland 下隐藏后位置即失效）
            x, y, w, h = self._layer_geometry()
            self._layer_window_pos = (self.x(), self.y())
            if self._fidus_enabled():
                # ②「量完再挂」：测得出来才挂，测不出来**不切**（人工裁决 2026-10-02）。
                self._start_premount_measure(x, y, w, h)
                return
            self._do_layer_mount(max(0, x), max(0, y), w, h, measured=False)
        else:
            self._cancel_premount_measure()
            self._layer_timer.stop()
            self._layer_fit_cancel()
            widget = getattr(self, "sprite_label", None)
            if widget is not None:
                widget._proxy_rect = None

            pos = getattr(self, "_layer_window_pos", None)
            if pos is not None:
                self.move(pos[0], pos[1])

            # ④ 销毁而非 clear —— 关键修复
            backend.destroy_context()
            self.show()
            self.raise_()
            safe_print("[layer] → 交互模式（可拖动 / 右键菜单）")

    def _layer_surface_region(self, w: int, h: int) -> tuple[int, int, int, int]:
        """挂载该请求画布帧里的哪一块：`(ox, oy, ow, oh)` = 不透明轮廓（含动作余量）。

        旧口径请求的是**整画布**（`_layer_geometry()` 给的就是 widget 尺寸），而画布四周那圈
        透明像素用户从来没看见过 —— 它们照样进 margin/size 请求。人工 2026-10-03 那一条
        `只配出 456x463（请求 512x512）@测量位 (910,305)` 里两轴逐位等于「输出 − margin」
        （1366−910=456、768−305=463，本机 niri 就是"剩多少配多少"）⇒ 撑爆请求的正是那圈透明边，
        而被切掉的却是可见内容。这是几何源头，不是门控。

        取不到帧（PNG 模式没有 `render_offscreen`、渲染还没热）／整帧没有不透明像素／轮廓短于
        `_LAYER_MIN_SURFACE_EDGE` ⇒ 回退整画布并**出声**：那是今天的行为，不是新猜的姿势。
        """
        frame = self._fidus_current_frame()
        if frame is None:
            safe_print("[layer] 量不到轮廓 ⇒ 按整画布挂（离屏渲染不可用？）")
            return (0, 0, int(w), int(h))
        try:
            import numpy as np

            from meapet.desktop.fidus_position import ALPHA_MIN

            alpha = frame[..., 3] > ALPHA_MIN
            cols = np.flatnonzero(alpha.any(axis=0))
            rows = np.flatnonzero(alpha.any(axis=1))
            if cols.size == 0 or rows.size == 0:
                raise ValueError("整帧没有不透明像素")
            ox = max(0, int(cols.min()) - _LAYER_BBOX_PAD_PX)
            oy = max(0, int(rows.min()) - _LAYER_BBOX_PAD_PX)
            ow = min(int(cols.max()) + 1 + _LAYER_BBOX_PAD_PX, int(w)) - ox
            oh = min(int(rows.max()) + 1 + _LAYER_BBOX_PAD_PX, int(h)) - oy
        except Exception as exc:
            safe_print(f"[layer] ⚠ 轮廓这一判没跑成 ⇒ 按整画布挂: "
                       f"{type(exc).__name__}: {exc}")
            return (0, 0, int(w), int(h))
        if ow < _LAYER_MIN_SURFACE_EDGE or oh < _LAYER_MIN_SURFACE_EDGE:
            safe_print(f"[layer] ⚠ 轮廓只有 {ow}x{oh}（画布 {w}x{h}）⇒ 不认它是轮廓，按整画布挂")
            return (0, 0, int(w), int(h))
        if (ox, oy, ow, oh) == (0, 0, int(w), int(h)):
            return (0, 0, int(w), int(h))       # 画布本来就全不透明 ⇒ 一字未改
        safe_print(f"[layer] 挂载按轮廓请求 {ow}x{oh} @帧内({ox},{oy})"
                   f" ← 画布 {w}x{h}（省掉透明边 {w - ow}x{h - oh}）")
        return (ox, oy, ow, oh)

    def _layer_canvas_rect(self, sx: int, sy: int, sw: int, sh: int) -> QRect:
        """surface 矩形 → **画布**矩形。信念（`_proxy_rect`）吃的从来是画布，不是 surface。

        `live2d_widget._update_look_target` 用的是 `proxy.topLeft()` 再配 widget **自身**的宽高
        算视线中心，所以那个矩形必须是整画布在屏幕上的矩形；把 surface 矩形写进去，视线会朝
        桌宠外面看。origin=(0,0)、canvas=surface 时（丙之前、以及丙的每一条回退支）本式逐位
        退化成"原样"。
        """
        ox, oy = getattr(self, "_layer_frame_origin", (0, 0))
        cw, ch = getattr(self, "_layer_canvas_size", None) or (sw, sh)
        return QRect(QPoint(int(sx) - int(ox), int(sy) - int(oy)),
                     QSize(int(cw), int(ch)))

    def _layer_canvas_center(self, sx: int, sy: int, sw: int, sh: int) -> tuple[float, float]:
        """surface 矩形 → 画布**中心**（fidus 的 `Fix.center` 与先验都是画布中心的口径）。

        桥接层那个锚点是从帧几何定死的（`iter_candidates` 里 `anchor = 盒中心 − 帧中心`），
        它跟 surface 切到哪一块无关 ⇒ 谁拿读数反解矩形，谁就得用画布尺寸反解。
        """
        rect = self._layer_canvas_rect(sx, sy, sw, sh)
        return (rect.x() + rect.width() / 2.0, rect.y() + rect.height() / 2.0)

    def _do_layer_mount(self, x: int, y: int, w: int, h: int, *, measured: bool) -> None:
        """挂载那一段的单点：`enable` → 信念 → `hide` → 推帧 → 回读轮。

        `measured=True` 表示这个矩形是 fidus 量出来的，此后回读轮**只报数不改位**
        （裁决 2「测量赢」，见 `_layer_fit_service`），也不再起第二轮 fidus——20 s 预算
        装不下第二发（H25 口径：一发最坏 13.5 s，两发 22.8 s）。

        进来的 `(x,y,w,h)` 是**画布**矩形（信念或 `_rect_from_center` 的产物），发出去的是
        **surface** 矩形（画布里的轮廓那一块，见 `_layer_surface_region`）。两者只在这里分叉一次：
        信念仍写画布矩形（`_layer_canvas_rect` 的口径 ⇒ 丙之前是同一个对象），而回读轮、margin、
        推帧的裁都跟着 surface 走。
        """
        backend = self._layer_backend
        self._layer_frame_origin = (0, 0)   # 先复位：`enable` 抛了就挂着上一轮的偏移没挂东西
        self._layer_canvas_size = None
        ox, oy, ow, oh = self._layer_surface_region(w, h)
        self._layer_frame_origin = (ox, oy)
        self._layer_canvas_size = (int(w), int(h))
        sx, sy = x + ox, y + oy
        try:
            backend.enable(None, ow, oh, sx, sy)  # 每次都新建（全新 OVERLAY surface）
        except Exception as exc:
            safe_print(f"[layer] 创建 layer context 失败: {exc}")
            return

        widget = getattr(self, "sprite_label", None)
        if widget is not None:
            widget._proxy_rect = QRect(QPoint(x, y), QSize(w, h))

        self.hide()                                 # ③ 最后才隐藏
        self._layer_timer.start()
        canvas = f" ← 画布 {w}x{h}@({x},{y})" if (ox or oy) else ""
        safe_print(f"[layer] → 穿透模式 surface={ow}x{oh} @({sx},{sy}){canvas}"
                   f"{'（测量位）' if measured else ''}")
        # ④ 先问合成器"这块矩形你到底配成了多大"，再决定要不要摆。
        self._layer_fit_start(sx, sy, ow, oh, measured=measured)

    # ------------------------------------------------- 回读 configure 尺寸并据此摆位
    #
    # 失效模式（本机 niri + 一条 180 px 状态栏实测，正证见
    # `~/.Athena/projects/meapet/working/layer-configure-size-ignored.md`）：桥接层的
    # 像素门控只放行"提交尺寸 == 逻辑尺寸"的帧（spec §6.3），而逻辑尺寸是**合成器
    # configure 出来的事实**，不是我们请求的那个数。可用区被外部 exclusive_zone 扣掉
    # 一块之后，margin 越界的挂载请求会被夹小（请求 614 高、配出 445）⇒ 从此每一帧都被
    # 我们自己丢掉：人看不见，grim 也看不见，日志里一个字都没有。旧实现唯一的坐标来源
    # 是 Qt 的 availableGeometry，而它在 Wayland 下对那条带完全无感（恒等于整屏），
    # 所以"请求 614 高"这件事没有任何东西能反驳 —— 这一段就是那个反驳。
    #
    # 用的模型只有两条，都在 H16 探针上量过（N1b/N1h 两臂，残差 0 px）：
    #   configure 高 = min(请求高, 可用高 − margin_y)   ⇒  可用高 = configure 高 + margin_y
    #   真实落点    = 保留带厚度 + margin
    # 两条合起来给出"往回挪到 margin′ = 可用高 − 请求高，请求尺寸就装得下"。
    #
    # 模型错了会怎样：第一份"没按请求配"的读数会立刻触发那一发夹移，之后 12 拍内
    # 等不到等于请求的 configure ⇒ 走 _layer_fit_report 那条**出声**的出口（日志 + 气泡）。
    # 夹移只发一发，因为那一发已经是模型的全部结论；再发第二发就是拿猜测冒充测量。
    # 夹移后读到**夹移前**那个尺寸不算证据（configure 异步），所以旧读数只消耗预算、
    # 不触发判决——预算用完才判失败。这一段的全部意义是不再静默，
    # 所以出口本身静默是不允许的形态。
    #
    # 落定之后还有一段**稳态重查**（工作项 #89，人工裁决「带撤之后夹移无声错 b 即可」）：
    # 上面那一轮只在挂载那一刻开一次，之后 exclusive-zone 带**改厚度或再出现**时，
    # 那一发夹移吃的带厚就成了旧事实。修法是把手上那个量从"换算所得的 margin"改成
    # "带厚"本身（`_fit_band`），并在每次 configure 变化时重算：紧张相（配得比请求小）
    # 按新可用区往上/左夹一发，宽裕相（配得够大）把位置朝锚点收一发。两边都**只发一发，
    # 然后交回上面那一轮确认**——回读、预算、出声出口全部复用，不另造一套判决。
    # 锚点是"最后一次由本产品主动发出的请求"：夹移不搬动它（那是被迫的），`_fidus_place`
    # 搬动它（那是量出来的），所以 fidus 挪过之后回收的是 fidus 的位置。
    # fidus 正在跑时整段让路（见 `_layer_fit_watch`）。
    #
    # **有一相补不到，写在脸上**：夹移**已经落定**之后带整条撤走，configure 重发的
    # **数值不变**（`min(请求高, 可用高 − margin)` 早已等于请求），而桥接层只缓存那个
    # 数值、没有"configure 来过一发"的序号 ⇒ 宿主无从发现这一相，那 180 px 的无声错位
    # 照旧。补它要动 Rust 桥（第 13 枚符号），超出本次授权面（render_host.py）；已登记
    # `~/.Athena/projects/meapet/pending.md`，回归里也钉了一条同名用例
    # （`test_band_removal_after_a_successful_clamp_is_INVISIBLE`）防后来人误以为已修。

    def _layer_fit_start(self, x: int, y: int, w: int, h: int, *,
                         measured: bool = False) -> None:
        """挂载后开一轮"回读—夹移"；fidus 等它落定再启动。

        `measured=True`（位置来自 fidus 的屏幕读数）改变这一轮的**判决**：只报数、不改位
        （人工裁决 2026-10-02「测量赢」）。理由不是夹移算错，而是它按构造与测量无关——
        `ny = lh + y − h` 展开后等于"可用高 − 请求高"，与 `y` 来自信念还是测量完全无关，
        所以它一定会把量出来的那个位置搬走。顺带本轮不再排 fidus 校正（`_fit_fidus=None`）：
        20 s 预算装不下第二发（H25 口径：一发最坏 13.5 s，两发 22.8 s）。
        """
        pending = (int(x), int(y), int(w), int(h))
        self._fit_measured = bool(measured)
        self._fit_fidus = None if measured else pending
        # 锚点＝本轮请求。此后**只有 `_fidus_place` 搬它**（夹移是被迫的，不配改锚），
        # 宽裕相要回收的就是它。见 `_fidus_owns_placement`。
        self._fit_anchor = pending
        self._fit_margin = (int(x), int(y))
        self._fit_band = None       # 带厚：None＝这一相没证据说有带；夹过移才有整数值
        self._fit_size = None       # 最近一次读到的 configure（稳态重查的基准）
        self._layer_conform_reported = False   # 新一轮挂载，"第一次真裁"又是一条新闻
        self._fit_watch = _LAYER_FIT_WATCH_TICKS
        backend = getattr(self, "_layer_backend", None)
        if backend is None or not callable(getattr(backend, "logical_size", None)):
            # 没有回读通道（旧产物／假后端）＝这段无从判断，直接放行 fidus。
            # 顺带**不设锚**：稳态重查吃的就是这条通道，没有它就没有那一相（今天的行为）。
            self._fit_rect = None
            self._fit_anchor = None
            self._layer_fit_finish(pending)
            return
        self._fit_rect = pending
        self._fit_seen = None      # 只在 _fit_rect 非空时被读到，见 _layer_fit_service
        self._fit_wait = _LAYER_FIT_WAIT_TICKS

    def _layer_fit_cancel(self) -> None:
        """离开穿透模式：本轮回读作废，别把挂起的 fidus 启动留到下一次挂载。"""
        self._fit_rect = None
        self._fit_fidus = None
        self._fit_seen = None
        self._fit_band = None
        self._fit_measured = False
        self._fit_anchor = None
        self._fit_margin = None
        self._fit_size = None
        self._layer_frame_origin = (0, 0)   # 没挂 ⇒ 帧内没有"surface 子块"这回事
        self._layer_conform_reported = False

    def _layer_fit_service(self) -> bool:
        """每拍一次（骑在 `_layer_timer` 上，不新增定时器）。

        返回 True = 这一拍还在等回读／刚发出夹移请求，**别推帧**——推了也必被门控丢掉，
        白付一次 Live2D 离屏渲染。
        """
        rect = getattr(self, "_fit_rect", None)
        if rect is None:
            return self._layer_fit_watch()
        x, y, w, h = rect
        backend = self._layer_backend
        try:
            size = backend.logical_size()
        except Exception as exc:
            # 门面自己都不该抛；抛了就等于回读通道坏了 ⇒ 出声，别换成静默。
            self._layer_fit_finish(None, f"回读异常 {type(exc).__name__}: {exc}")
            return False
        if size is not None:
            size = (int(size[0]), int(size[1]))
            self._fit_size = size      # 稳态重查的基准：落定那一读就是它该认的第一份事实
            if size == (w, h):
                self._layer_fit_finish((x, y, w, h))
                return False
            if self._fit_seen is None:
                lw, lh = size
                if getattr(self, "_fit_measured", False):
                    # 位置是量出来的 ⇒ 不发夹移（裁决「测量赢」），只报数。
                    self._layer_fit_finish((x, y, w, h))
                    self._layer_report_measured_hold((x, y, w, h), size)
                    return False
                # 第一份"没按请求配"的读数 ⇒ 立刻发那一发按模型算出来的夹移。
                nx, ny = min(x, lw + x - w), min(y, lh + y - h)
                if (nx, ny) == (x, y):
                    # 尺寸对不上、按模型却已无处可挪 ⇒ 模型不适用（带不在顶／左，或该
                    # 合成器另有夹法）。不猜第二种解法，出声。
                    self._layer_fit_finish(None, f"合成器配出 {lw}x{lh}（请求 {w}x{h}），"
                                                 f"且按模型无处可挪")
                    return False
                safe_print(f"[layer] 合成器配出 {lw}x{lh}（请求 {w}x{h}）⇒ 可用区 "
                           f"{lw + x}x{lh + y}，回摆 ({x},{y})→({nx},{ny})")
                if not self._layer_fit_move(nx, ny):
                    self._layer_fit_finish(None, f"回摆 ({nx},{ny}) 请求发不出去")
                    return False
                self._fit_seen = size
                self._fit_rect = (nx, ny, w, h)
                self._fit_band = self._layer_band_thickness(lh + y)
                self._layer_fit_proxy_rect(nx, ny, w, h)
                self._fit_wait = _LAYER_FIT_WAIT_TICKS
                return True
        # 走到这里 = 本拍没有可行动的新证据（没读数，或已回摆在等那份新的 configure）
        self._fit_wait -= 1
        if self._fit_wait > 0:
            return True
        seen = self._fit_seen
        if seen is None:
            self._layer_fit_finish(None, f"请求 {w}x{h} 从未被 configure"
                                         f"（等了 {_LAYER_FIT_WAIT_TICKS} 拍）")
        else:
            now = ("仍没有任何 configure" if size is None
                   else f"读到 {size[0]}x{size[1]}")
            self._layer_fit_finish(None, f"按 {seen[0]}x{seen[1]} 回摆到 ({x},{y}) 后，"
                                         f"configure 仍未等于请求 {w}x{h}（{now}）")
        return False

    def _layer_fit_watch(self) -> bool:
        """稳态重查（#89）：带撤走／改厚度之后重算那一发夹移，别让它在无声里错 180 px。

        入口是 **configure 变化本身**：每 `_LAYER_FIT_WATCH_TICKS` 拍读一次桥接层缓存的
        最近一份 configure，只跟上一次读到的那份比。没有变化就没有新事实，也就不该有动作。
        这不是请求-应答式的往返（那才是 #78 禁止的每帧跨 FFI 回读），成本是一秒一次
        一个 `c_int` 写回——把 #78 §8.1 那条"稳态零次"进一步打折成"稳态一秒一次"，按打折记。

        两相：
          紧张相（配出的尺寸比请求小）⇒ 可用区缩了 ⇒ `U = 配高 + 当前 margin`，
            按模型往上/左夹一发。`min` 保证只会缩，所以永远不会把 fidus 挪走的方向扳回来。
          宽裕相（配出的尺寸够大）⇒ 帧在当前 margin 装得下 ⇒ 把位置朝锚点收一发。
            锚点此刻装不装得下**不可证明**（configure 等于请求只给 `U ≥ margin + 请求高`
            这个下界），所以那一发是**探针**：收过去之后交回 `_layer_fit_service` 确认，
            确认不了它会按新读数自己再夹回来——那条自愈路径是挂载那一轮现成的。

        两相都只发一发，且发完就把这一拍当作"在等回读"拦掉推帧（推了也必被尺寸门控丢掉）。

        fidus 正在跑（`_fidus_busy`）时整段让路：它和我都在写 `set_position`，而它的闭环
        吃的是"我请求 X、屏上是 Y"这个对应关系，中途插一发就把对方的读数打乱了。
        这一拍不重查，下一个周期再看——带不会在一秒内来回变。
        """
        anchor = getattr(self, "_fit_anchor", None)
        if anchor is None:
            return False
        if getattr(self, "_fidus_busy", False):
            return False
        self._fit_watch -= 1
        if self._fit_watch > 0:
            return False
        self._fit_watch = _LAYER_FIT_WATCH_TICKS
        backend = self._layer_backend
        try:
            size = backend.logical_size()
        except Exception as exc:
            # 稳态这一读坏了：一个只出声一次就再刷一遍的循环会淹掉日志，所以这里只报
            # 本周期，也**不撤锚**——撤锚等于把"这一段不再管事"当成结论，而实情是"这次没读到"。
            safe_print(f"[layer] ⚠ 稳态重查回读异常，本周期跳过: "
                       f"{type(exc).__name__}: {exc}")
            return False
        if size is None:
            return False
        size = (int(size[0]), int(size[1]))
        prev, self._fit_size = self._fit_size, size
        if prev is None or prev == size:
            return False                  # 还没有基准，或这一份不是"新事实"
        ax, ay, w, h = anchor
        mx, my = getattr(self, "_fit_margin", None) or (ax, ay)

        if size[0] < w or size[1] < h:
            # 紧张相：从**当前** margin 反解可用区（模型只在被夹这一支给出等式）。
            nw, nh = size
            if getattr(self, "_fit_measured", False):
                safe_print(f"[layer] configure {prev[0]}x{prev[1]}→{nw}x{nh} ⇒ 可用区缩了，"
                           f"但位置是量出来的 ⇒ 不回摆（裁决「测量赢」），margin=({mx},{my}) 保留")
                return False
            nx, ny = min(ax, nw + mx - w), min(ay, nh + my - h)
            if (nx, ny) == (mx, my):
                safe_print(f"[layer] configure {prev[0]}x{prev[1]}→{nw}x{nh}，"
                           f"按模型已在无处可挪（margin=({mx},{my})）⇒ 不动")
                return False
            band = self._layer_band_thickness(nh + my)
            safe_print(f"[layer] configure {prev[0]}x{prev[1]}→{nw}x{nh} ⇒ 可用高 "
                       f"{nh + my}（带厚 {band}），重摆 ({mx},{my})→({nx},{ny})")
            if not self._layer_fit_move(nx, ny):
                return False
            self._fit_band = band
            self._layer_fit_proxy_rect(nx, ny, w, h)
            # 这一份读数就是触发因，不是"夹移之前的旧证据" ⇒ _fit_seen 直接占上，
            # 确认轮里不再据它发第二发。
            self._fit_open_recheck((nx, ny, w, h), seen=size)
            return True

        # 宽裕相：帧在当前 margin 装得下 ⇒ 朝锚点收一发（探针，见 docstring）。
        if (mx, my) == (ax, ay):
            self._fit_band = None         # 已在锚点又装得下 ⇒ 没有夹移要记账
            return False
        safe_print(f"[layer] configure {prev[0]}x{prev[1]}→{size[0]}x{size[1]} ⇒ 宽裕，"
                   f"回收夹移 ({mx},{my})→({ax},{ay})（一发，待确认）")
        if not self._layer_fit_move(ax, ay):
            return False
        self._fit_band = None
        self._layer_fit_proxy_rect(ax, ay, w, h)
        # 这里 `seen=None`：锚点上那份"等于请求"的旧读数不是夹移前的证据，而确认轮
        # 需要有权对锚点上真正落下来的新 configure 再夹一发。
        self._fit_open_recheck((ax, ay, w, h), seen=None)
        return True

    def _fit_open_recheck(self, rect, seen) -> None:
        """把重查算出的那一发交给**挂载那一轮**确认：同一套预算、同一个出声出口。"""
        self._fit_rect = rect
        self._fit_seen = seen
        self._fit_wait = _LAYER_FIT_WAIT_TICKS
        # 重查落定不许再启一轮 fidus：#78 把它排在落定之后**一次**，两段各跑各的会互吃读数。
        self._fit_fidus = None

    def _layer_fit_move(self, x: int, y: int) -> bool:
        """本段的摆位出口，只碰 `set_position`。

        不复用 `_fidus_place`：它顺手写 `_fidus_surface`，而 fidus 可能整场都没启动
        （默认关）。与其让一段写另一段的状态，不如各记各的账。

        唯一的例外是 `_fit_margin`：那是"最后一次由本产品发出的 margin"这个**物理事实**，
        而两个作者都可能是写它的人（#89 的重查要从它反解可用区）。各记各的账在这里等于
        把同一个事实存两份、任其分叉，所以两处写、一处读。锚点另有其主，见
        `_fidus_owns_placement`。
        """
        setter = getattr(getattr(self, "_layer_backend", None), "set_position", None)
        if not callable(setter):
            return False
        try:
            setter(int(x), int(y))
        except Exception as exc:
            safe_print(f"[layer] ✗ 回摆 set_position 失败: {exc}")
            return False
        self._note_margin_request(int(x), int(y))
        return True

    def _note_margin_request(self, x: int, y: int) -> None:
        """记下"最后一次由本产品发出的 margin"——重查要从它反解可用区。

        **不动锚点**：夹移后的位置是"被迫的"，锚点必须留在人把桌宠放下的那个请求上，
        否则宽裕相会发现"当前 == 锚点"而什么都不回收，本件要修的正是这一格。
        """
        self._fit_margin = (int(x), int(y))

    def _fidus_owns_placement(self, x: int, y: int) -> None:
        """fidus 挪过之后，锚点改吃它那个位置。

        锚点的含义是"人／测量认定桌宠该在哪"，而 fidus 那一发是按屏幕读数算的**主动**摆放，
        比挂载请求更新。宽裕相若回收的是挂载那一刻，就会把 fidus 测出来的修正抹掉。
        """
        self._note_margin_request(x, y)
        anchor = getattr(self, "_fit_anchor", None)
        if anchor is not None:
            self._fit_anchor = (int(x), int(y)) + anchor[2:]

    def _layer_fit_proxy_rect(self, x: int, y: int, w: int, h: int) -> None:
        """把"我相信自己在屏幕上的哪块矩形"跟着夹移一起改——否则信念与实际分叉。

        margin 是**可用区内**的坐标，`_proxy_rect` 是屏幕坐标，差的就是保留带厚度。
        带厚取 `self._fit_band`——那是发这一发夹移时按**当时**的 configure 算出来并存下的
        量（#89 起它是这段的手持事实，不再是从 `usable_h` 当场换算的中间值）；不在这里
        重读回读通道：那一读可能已经等到夹移**之后**的新 configure，与正在写的这个 margin
        不是同一份事实。假设"带在顶上"——上面那两条模型本来就是这个形状，不成立时那一发
        夹移会被超时预算否掉。宽裕相回收那一发时 `_fit_band` 刚被撤成 None ⇒ 同一个"无带"假设。

        进来的 `(x,y,w,h)` 是 **surface** 矩形（丙之后它比画布小一圈），写出去的是**画布**矩形：
        信念的口径由 `_layer_canvas_rect` 单点决定，不许在这里各写各的。
        """
        widget = getattr(self, "sprite_label", None)
        if widget is None:
            return
        rect = self._layer_canvas_rect(x, y, w, h)
        rect.translate(0, self._fit_band or 0)
        widget._proxy_rect = rect

    def _layer_band_thickness(self, usable_h: int) -> int:
        """保留带厚度 = 屏幕高 − 可用高，本模块里唯一一处读屏幕尺寸的地方。

        整屏高取 Qt 的 `geometry()`——Wayland 下它报逻辑整屏（本机实测 1366x768），
        而 `availableGeometry()` 对外部带完全无感，那正是旧摆位偏 180 px 的来源。
        读不到屏幕就给 0：宁可不估，也不拿一个编出来的带厚去挪信念矩形。
        """
        try:
            screen = QApplication.primaryScreen()
            if screen is None:
                return 0
            return max(0, screen.geometry().height() - int(usable_h))
        except Exception:
            return 0

    def _layer_report_head_clip(self, rect, band: int | None = None) -> None:
        """夹移落定后单独问一次「不透明轮廓是否出屏」——出屏必须出声，**不改摆位**。

        那两条模型只优化"帧装进可用区"，于是它能自救成功（整块上抬）而把头顶推出屏幕上沿：
        本机 scale=1.5 实测 configure 461x512（请求 461x614）⇒ 夹移到 -102，轮廓行 41..570
        的顶端因此落在屏幕行 -61，61 px 的头没了而这一段自认正常（正证与三条出口见
        `~/.Athena/projects/meapet/working/fractional-scale-head-clip.md` §2/§3）。这里只取
        第一条出口；"按轮廓算夹移"那条不做——轮廓与帧的差是本机这一只模型的属性。

        默认只在真夹过移时判（`_fit_band` 是整数）：没夹 ⇒ configure 一开始就等于请求 ⇒ 帧整体在
        可用区内而轮廓是帧的子集，结构上不可能出屏，不为此多付一次离屏渲染。`band` 由调用方
        显式给时按给的数判——测量位挂载就是这一格：它没夹过移，但那个 margin 是屏幕上读出来的，
        完全可能本身就把头顶顶到屏外。横向不判：夹移只往上／左挪（`min`），且本模块没有横向的
        带厚可读。
        """
        x, y, _w, _h = rect
        band = self._fit_band if band is None else band
        if band is None:
            return          # 没夹过移 ⇒ 帧整体在可用区内，不必为不可能的出屏付一次渲染
        frame = self._fidus_current_frame()
        if frame is None:
            return
        try:
            import numpy as np

            from meapet.desktop.fidus_position import ALPHA_MIN

            rows = np.flatnonzero((frame[..., 3] > ALPHA_MIN).any(axis=1))
            if rows.size == 0:
                return
            by0, by1 = int(rows.min()), int(rows.max()) + 1   # 右开，与探针 opaque_bbox 同式
            # `rect` 是 surface 矩形：它的 margin 已经含了轮廓上界 oy，而 by0 是从**整帧**行数出来的，
            # 不减就把同一行计两次（丙之前 oy 恒 0，这一减逐位退化成原式）。减完得到画布顶的屏幕 y，
            # 与 `_layer_fit_proxy_rect` 里信念那个 y 同口径。
            _ox, oy = getattr(self, "_layer_frame_origin", (0, 0))
            screen_y = y - int(oy) + band
            head = -(screen_y + by0)
            if head <= 0:
                return
        except Exception as exc:
            safe_print(f"[layer] ⚠ 轮廓出屏这一判没跑成: {type(exc).__name__}: {exc}")
            return
        safe_print(f"[layer] ⚠ 不透明轮廓出屏：头顶被屏上沿切掉 "
                   f"{head} 逻辑 px（信念屏幕 y={screen_y}，帧内轮廓行 {by0}..{by1}，"
                   f"带厚 {band}）")
        self._show_bubble(
            "屏幕高度不够，桌宠的头会被屏上沿切掉一块\n"
            "（缩小画布或降低桌面缩放比例可避开）",
            bubble_duration_ms(self.config, "interaction"),
        )

    def _layer_report_measured_hold(self, rect, size) -> None:
        """测量位与"合成器给不出请求尺寸"并存时的出口：出声报数，**不改位**（裁决「测量赢」）。

        不复用 `_layer_fit_report`：那句写的是"摆位失败"，而这里摆位是成功的——位置按量出来的
        保留，坏的是尺寸。带厚从这一读反解（`可用高 = 配高 + margin`，模型只在被夹这一支给出
        等式）后交给削头那一判：它此前只在真夹过移时跑，而测量位没夹过移
        也可能把头顶顶到屏外。

        措辞走 `_layer_size_outcome`（唯一一处），不在这里另写一份：人工 2026-10-03 那一条
        `只配出 456x463（请求 512x512）` 里，旧句子说的"会保持空白"已经不是后果了——
        `_layer_conform_to_configure` 按 configure 裁着推 ⇒ 后果是**切掉一块**。
        """
        x, y, w, h = rect
        lw, lh = size
        safe_print(f"[layer] ⚠ 合成器只配出 {lw}x{lh}（请求 {w}x{h}）⇒ 按测量位 ({x},{y}) 保留，"
                   f"不回摆（裁决「测量赢」）")
        self._show_bubble(self._layer_size_outcome(rect),
                          bubble_duration_ms(self.config, "interaction"))
        self._layer_report_head_clip(rect, self._layer_band_thickness(lh + y))

    def _layer_size_outcome(self, rect) -> str:
        """「尺寸配不出来」这一后果的**唯一一处**措辞，按有没有读到 configure 分三支。

        读到过且配得比请求小 ⇒ `_layer_conform_to_configure` 按那份事实裁着推，人看见的是
        **切掉一块**；从没读到过 ⇒ 提交尺寸无从对齐，门控照丢 ⇒ 才是**整片空白**；配得比请求
        大 ⇒ 不在算过的模型里（`configure = min(请求, 剩余)` 只会往小夹），照原样提交并报数。
        这三支不许合成一句：把"空白"留在裁着显示那一支，就是拿一个不存在的症状引人去查。
        测量位那一出口（`_layer_report_measured_hold`）与信念位那几支出口（`_layer_fit_report`）
        共用这里，免得两处措辞分叉。
        """
        _x, _y, w, h = rect
        fit = getattr(self, "_fit_size", None)
        if not fit:
            return ("屏幕放不下这个尺寸的桌宠，穿透画面会保持空白\n"
                    "（缩小画布或收起状态栏再试）")
        fw, fh = int(fit[0]), int(fit[1])
        cut_w, cut_h = max(0, w - fw), max(0, h - fh)
        if cut_w > 0 or cut_h > 0:
            edges = "／".join(part for part in (
                f"右侧 {cut_w} px" if cut_w > 0 else "",
                f"下方 {cut_h} px" if cut_h > 0 else "") if part)
            return (f"屏幕放不下这个尺寸的桌宠：{edges}会被切掉\n"
                    f"（画面按合成器配出的 {fw}x{fh} 切着显示，缩小画布可少切一点）")
        return (f"合成器配出 {fw}x{fh}，比请求的 {w}x{h} 还大\n"
                "（这不在算过的模型里，按原位保留，不做第二种解法）")

    def _layer_fit_finish(self, rect, got: str | None = None) -> None:
        """落定：`rect=None` 表示没救回来（出声，且**不**启动 fidus——量一个不在屏上的
        surface 只会再补一条"没能量准位置"的误导气泡，真正的原因在尺寸上）。

        `pending is None` 是 #89 的重查轮：它照旧判削头（带厚变了，出屏这一判就变了），
        但**不再启一轮 fidus**——#78 把 fidus 排在落定之后一次，两段各跑各的会互吃读数。
        """
        pending = getattr(self, "_fit_fidus", None)
        last = getattr(self, "_fit_rect", None)
        self._fit_fidus = None
        self._fit_rect = None
        self._fit_watch = _LAYER_FIT_WATCH_TICKS   # 重查从落定那一拍起算一整周期
        if got is not None:
            self._layer_fit_report(last or pending, got)
        if rect is None:
            # 没救回来：`_fit_size` **留着**——超时那一拍读到的仍是当前事实，拿它当基准
            # 才能让稳态重查认出"下一次 configure 变了"。撤成 None 会把紧接而来的那一变
            # 当成"第一次读数"安静吞掉，而那恰好是本件要抓的那一格。
            return
        self._layer_report_head_clip(rect)
        if pending is not None:
            self._maybe_start_fidus_locate(*rect)

    def _layer_fit_report(self, rect, got: str) -> None:
        """放不下的那条出口：必须出声，并把桥接层的诊断一起端出来（§7.1 #11）。

        诊断只作为**附注**读：粘性错误按 spec 不参与任何判决（I6），这里也只是把它
        原样印给人看，不据它分支。旧产物没有 `last_error` 时就没有这一行，不编造。
        """
        x, y, w, h = rect
        backend = getattr(self, "_layer_backend", None)
        why = ""
        if backend is not None and callable(getattr(backend, "last_error", None)):
            text = backend.last_error()
            if text:
                why = f"｜桥接层诊断：{text}"
        safe_print(f"[layer] ✗ 摆位失败：请求 {w}x{h} @({x},{y})，{got}{why}")
        self._show_bubble(self._layer_size_outcome((x, y, w, h)),
                          bubble_duration_ms(self.config, "interaction"))


    # ------------------------------------------------------------ fidus 定位
    #
    # 顺序（2026-10-02 改判，见 `pool/layer-belief-first-mount-decision-error.md`）：
    # **量完再挂**。旧那段写在代码脸上的"必要偏差"——「surface 没挂上去就没有可量的东西，
    # 所以先按信念挂载 → 测 → 用测量值再摆一次」——的前提被 H23/H24/H25 推翻了：fidus 搜索的
    # 是**它自己抓的那一张屏**，我们的离屏帧只当模板源，而交互态下桌宠本来就是屏上的像素。
    # 于是"先挂"换来的不是可测性，只是**一次用户看得见的跳位**，而那次跳位的落点按构造就不是
    # 量出来的那个（决策失误登记的主罪）。现在：
    #   * `penetrate=True` 且 fidus 开着 ⇒ 先测（不喂先验、H 手、2 px 闭环），
    #     测出来才 `_do_layer_mount(..., measured=True)`；测不出来 ⇒ **不切**、留在交互态、出声；
    #   * fidus 没开 ⇒ 挂载照旧按信念，回读夹移照旧（`measured=False` 那一支一字未改语义）；
    #   * 挂载**之后**那一次 fidus 校正（`_maybe_start_fidus_locate`）现在只在 `measured=False`
    #     的挂载之后被排入，而那条路只在 fidus 关闭时才走 ⇒ 闸门第一句就返回。**这条路实际
    #     已不可达**，但码与回归都留着：`locate()` 的 post-mount 语义（B 语义、探针位移由
    #     紧接着那次摆位吃掉）是 H13/H15 的资产，删它不等于删一个死分支，而是删一份实测凭证。
    #
    # 引擎在 fidus 自己的线程上（见 fidus_position.request_locate 的线程亲和说明），
    # 因此这里所有跨线程接触都走"标志 + 引用"，由 _fidus_service 在 GUI 线程上落地：
    # Qt 对象与 layer 门面都只在 GUI 线程碰。一轮测量另带一个**轮次号**（`_fidus_round`）：
    # 超时/取消之后那一发可能还在 fidus 线程上跑，凭号丢弃它迟到的读数与迟到的位移请求，
    # 否则下一次的挂载位置会来自上一次的测量。

    def _fidus_enabled(self) -> bool:
        return bool((self.config.get("fidus") or {}).get("enabled", False))

    def _toggle_fidus_enabled(self) -> None:
        cfg = self.config.setdefault("fidus", {})
        cfg["enabled"] = not bool(cfg.get("enabled", False))
        self._save_config()
        self._show_bubble(
            "已启用 fidus 定位：下次切换穿透时先量位置再挂（第一次约需 2 秒校准）"
            if cfg["enabled"] else "已停用 fidus 定位，切换穿透回到只用请求坐标",
            bubble_duration_ms(self.config, "interaction"),
        )

    def _begin_fidus_round(self, x: int, y: int, w: int, h: int, *,
                           premount: bool) -> int:
        """开一轮测量：复位全部跨线程标志，回这一发的轮次号。

        `_fidus_surface` 在这一轮里是"位移的基准"，两种用法不同源：post-mount 它是
        当前 surface 的 margin 矩形，pre-mount 它只是那个**已知在撒谎**的信念矩形——
        预挂载的位移不走它，走 `_content_probe_shift`（H 手挪的是子控件，不是 surface）。
        """
        self._fidus_busy = True
        self._fidus_premount = premount
        self._fidus_finished = False
        self._fidus_result = None
        self._fidus_move_req = None
        self._fidus_move_ack = None
        self._fidus_settle = 0
        self._fidus_surface = (int(x), int(y), int(w), int(h))
        self._fidus_mount = (int(x), int(y), int(w), int(h))
        self._fidus_round = getattr(self, "_fidus_round", 0) + 1
        if getattr(self, "_fidus_timer", None) is None:
            self._fidus_timer = QTimer(self)
            self._fidus_timer.setInterval(_FIDUS_POLL_MS)
            self._fidus_timer.timeout.connect(self._fidus_service)
        self._fidus_timer.start()
        return self._fidus_round

    def _premount_refuse(self, why: str, bubble: str) -> None:
        """「量不出可信读数 ⇒ 不切」（人工裁决 2026-10-02）。

        静默不切与旧的静默挂载是同一种失效：人会点第二下、第三下，然后怀疑鼠标。
        报的原因要指向真正的缺口——"没带引擎"与"画面拿不到"与"没量准"是三件事，
        混成一句会把人引去等一次根本不会到来的校准。
        """
        safe_print(f"[layer] ✗ 没切穿透：{why}")
        self._show_bubble(bubble, bubble_duration_ms(self.config, "interaction"))

    def _content_probe_room(self) -> tuple[int, int, int, int]:
        """子控件在顶层窗口内的**四向空档**——H 手那一发摆得摆不下就看它。

        返回 `(右, 下, 左, 上)`，**逐位对应 `FP.PREMOUNT_MOVES` 的顺序**。人工裁决
        2026-10-02：「超出屏幕的抓不到，但是桌宠大概率在屏幕上，所以四角都要试」——
        平铺下窗口按合成器预设出现，画布往哪一角溢出没有一定，只看右/下会把另外
        两角的可用位移判成零。摆不下仍要响亮退回：把贴片切掉一半去跑闭环，
        量到的是"半个盒"的读数，绿不算。
        """
        widget = getattr(self, "sprite_label", None)
        if widget is None:
            return (0, 0, 0, 0)
        return (int(self.width() - (widget.x() + widget.width())),
                int(self.height() - (widget.y() + widget.height())),
                int(widget.x()), int(widget.y()))

    def _content_probe_shift(self, dx: float, dy: float) -> bool:
        """H 手：把 Live2D 子控件在顶层窗口里平移，**顶层窗口几何一码不动**。

        这只手是判据的载体，不是装饰：`pet.move()` 摆顶层窗口在 Wayland 下屏上 0 px
        （平铺与浮动都 0，H24 四场四发），`niri move-floating-window` 有效但产品没有那只手。
        而子控件自移经 H25 十一场实证——离屏帧逐字节恒等（`帧恒等=True`）、屏上真实位移
        (+48.00,+0.00) 全中 ⇒ 喂给引擎的输入没变、变的只有屏上 ⇒ 闭环是**外接见证**，
        不是"我把挪过的帧喂给它，它当然跟得上"那种自循环。
        """
        widget = getattr(self, "sprite_label", None)
        if widget is None:
            return False
        base = getattr(self, "_content_probe_base", None)
        if base is None:
            base = self._content_probe_base = (int(widget.x()), int(widget.y()))
        widget.move(base[0], base[1])       # 从原位出发才谈得上"已知位移"（幂等）
        shift_x, shift_y = int(round(dx)), int(round(dy))
        room = self._content_probe_room()   # 已回原位 ⇒ 这四数是相对原位的余量
        need = (max(0, shift_x), max(0, shift_y), max(0, -shift_x), max(0, -shift_y))
        # **只判真要位移的那几向**。`window_mask` 开着时画布比视口宽、往左溢出，
        # `_content_probe_room()` 的「左」因此恒为负（真机 2026-10-03：-123）——那是
        # 内容被裁的位置，不是这一向的缺口。拿 `need=0` 去比 `room=-123`，`0 > -123`
        # 为真，向右那一发被一个它根本不需要的位置否掉 ⇒ 四向全死、恒不通过。
        if any(n > r for n, r in zip(need, room) if n > 0):
            safe_print(f"[fidus] ✗ 四向空档 (右{room[0]},下{room[1]},左{room[2]},上{room[3]})"
                       f" 摆不出 ({shift_x},{shift_y}) ⇒ 换下一向")
            return False
        widget.move(base[0] + shift_x, base[1] + shift_y)
        return True

    def _restore_content_probe(self) -> None:
        """把探针位移吃掉（甲的实现约束 1）。

        `Fix.center` 取的是**位移之前**那一发 `a`（`fidus_position.py` 里 `locate` 的返回值
        由 `a` 算出），而子控件停在 +48 px。挂载吃的是 `a − anchor` 那个矩形，若就着挪过的
        子控件去挂，`_layer_geometry()` 读的 `widget.mapTo` 偏移还带着那 48 px ⇒ 同一个偏移
        既进挂载矩形又进下一次信念，两件事一起偏。post-mount 那一路不需要这一发：它靠紧接着
        的那次 `set_position` 把位移吃掉（`locate` 的 B 语义），两种用法别混。
        """
        base = getattr(self, "_content_probe_base", None)
        if base is None:
            return
        self._content_probe_base = None
        widget = getattr(self, "sprite_label", None)
        if widget is not None:
            widget.move(base[0], base[1])

    _PROBE_PUMP_TIMEOUT_MS = 500      # 等尺寸落地的上限：20 s 预算的 2.5%，只在 resize 之后跑
    _PROBE_PUMP_STEP_MS = 16
    _PROBE_ROOM_MARGIN_PX = 2         # 长尺寸时多要的两 px，见 `_request_probe_window`

    def _probe_slack_px(self) -> int:
        """探针要吃掉的四边余量 = `PREMOUNT_MOVES` 里最远那一发的绝对值（本机 48）。

        不另立常量：余量与位移必须**同源**，否则改了 `MOVE_PX` 会留下一个"窗口放得下、
        探针摆不出"或反过来的静默错配。
        """
        from meapet.desktop import fidus_position as FP

        return max(int(round(max(abs(dx), abs(dy)))) for dx, dy in FP.PREMOUNT_MOVES)

    def _pump_window_size(self, want: tuple[int, int]) -> tuple[int, int]:
        """GUI 线程上有界等尺寸落地，回**实得**的 (宽, 高)。

        四向空档是判据的**输入**，不是事后信息：拿请求值当真值，等于把"平铺会吃尺寸请求"
        重新变回一次信念——而 H23 给这类信念量出的偏差是 (−227,+342) px。所以这里等的是
        `self.width()` 追上请求；等不到就照实报数，由四向空档那道闸据此拒掉这一发。

        泵事件时**排除用户输入**：这半秒里面板收到一次点击，`_pending_mount`/`_fidus_busy`
        都还没置（它们在起工时才置），重入会把 `_probe_saved` 覆写成放大过的那一份，
        复原就只剩"缩到放大尺寸"——透明边从此留在交互态。
        """
        import time

        from PyQt5.QtCore import QElapsedTimer

        clock = QElapsedTimer()
        clock.start()
        while (self.width(), self.height()) != want:
            if QApplication.instance() is None:
                break                     # 假 host 的回归跑在这里：没有事件循环可泵
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            if clock.elapsed() >= self._PROBE_PUMP_TIMEOUT_MS:
                break
            time.sleep(self._PROBE_PUMP_STEP_MS / 1000.0)
        return (int(self.width()), int(self.height()))

    def _request_probe_window(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """量之前把顶层窗口**重新请求一次**，并只往右下长出探针缺的那一段——画布一动不动。

        治人工 2026-10-03 点出的两个毛病：

        ①「没有再次请求一个正确大小的 Qt 窗口」——平铺下 niri 按预设摆窗、客户端的尺寸请求
        被吃（`finished/layer-configure-size-ignored.md` 那一路的交互态对应物：实测平铺出
        659×736 而产品要 461×614）。所以先把产品自己那份 viewport 几何重发一遍；切成浮动之后
        这一次才拿得到正确大小。
        ②「四向空档恒为否」——常态下**窗口就是画布**（`window_mask` 关：`widget_x=0` 且
        `window_width == widget_width`）或画布的**裁剪**（`window_mask` 开：`widget_x=-crop_left < 0`
        且 `widget_width > window_width`）。把这两式代进 `_content_probe_room()`，右/下两数 ≤ 0
        ⇒ H 手连一发都摆不出。所以缺多少长多少，长出来的那一段在裁剪区外：既没东西可画
        （`WA_TranslucentBackground`，`app.py:258`）也不收点击，mask 因此一码不动。

        **为什么只往右下长，而不是「把桌宠摆中间、四边都留 slack」**（改判 ② 的字面读法）：
        把画布摆到窗口中间要 `widget.move(+slack,+slack)`，抵消它要 `self.move(-slack,-slack)`；
        后一发是 C 手，**Wayland 屏上 0 px**（H24 四场四发证死），且 `self.x()/self.y()` 在
        niri 下本来就不是事实（`_layer_geometry` 的 docstring 自己就这么写着）。所以抵消不掉，
        桌宠会在测量一开始就真跳 (+slack,+slack)，而 `Fix.center` 取的是**位移之前**那一帧
        ⇒ 跳完的读数拿去挂，surface 整体偏一份 slack。左/上两向因此常态摆不出，由 `locate`
        逐向跳过并各写一行（人工 ③「看哪个能用再采信哪个」），不靠假位移去凑。

        回 `(请求的 (宽,高), 实得的 (宽,高))`：差得多说明尺寸请求被吃了；**是否拒这一下**
        由 `_content_probe_room()` 四向**全**空那一支判，两个数原样写进日志，不靠这里的一句话。
        """
        widget = getattr(self, "sprite_label", None)
        if widget is None:
            return ((0, 0), (0, 0))
        if getattr(self, "_use_live2d", False):
            try:
                self._apply_live2d_viewport_geometry(self._size_factor)
            except Exception as exc:
                safe_print(f"[fidus] 重发 viewport 几何失败（照当前几何放大）: {exc}")
        slack = self._probe_slack_px() + self._PROBE_ROOM_MARGIN_PX
        # 多要那两个 px 不是余量洁癖：`window_mask` 开着时左/上恒为负，可用方向**只有右/下**，
        # 而这两数按上面那式**正好等于** `slack`——合成器少给 1 px（缩放档下尺寸按物理像素
        # 取整）就把仅有的两发同时判回"摆不出"，症状与刚才那条 bug 逐字相同（恒不通过）。
        ow, oh = int(self.width()), int(self.height())
        self._probe_saved = (ow, oh)
        want = (max(ow, int(widget.x()) + int(widget.width()) + slack),
                max(oh, int(widget.y()) + int(widget.height()) + slack))
        if want != (ow, oh):
            self.resize(*want)
        got = self._pump_window_size(want)
        return (want, got)

    def _restore_probe_window(self) -> None:
        """把窗口缩回常态尺寸，没放大过就是空转。

        收尾处处无脑调它：量准了要挂、量不准要拒、超时、切回交互态——留在一个放大过的
        窗口里，等于把「透明边也算热区」这件事偷偷留给用户（`#83/#88` 那条帧=热区的结论
        就是这么被撑大的）。

        这里**只**还尺寸：画布的偏移全程没碰，窗口也没挪（C 手在 Wayland 是 0 px，见
        `_request_probe_window`），mask 因此也跟着不动——长出去那一段是 `WA_TranslucentBackground`
        （`app.py:258`）下的透明像素，画不出东西；它收不收点击本机没测，但这一发只在测量那几秒里
        存在，收尾（挂／拒／超时／切回）一律把尺寸还回去，不留过夜。
        （旧写法在这里 `clearMask()` 之后调 `_apply_hit_region()` 想把它请回来，而 `MeaPet`
        上那个名字解析到 `app.py:602` 的点击穿透版，压根不改 mask：清了没还。）
        """
        saved = getattr(self, "_probe_saved", None)
        if saved is None:
            return
        self._probe_saved = None
        ow, oh = saved
        if (int(self.width()), int(self.height())) != (ow, oh):
            self.resize(ow, oh)
        if QApplication.instance() is not None:
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _confirm_float_first(self) -> bool:
        """量之前先要一次「窗口切成浮动了吗」的人工确认（裁决 2026-10-02「先让用户 Mod + V」）。

        平铺下 niri 按预设摆窗，**客户端请求的尺寸会被吃掉**（人工现测 2026-10-02；
        产品侧也从不回应 configure——`meapet/` 里没有任何顶层窗口尺寸变化的覆写）。于是
        交互态里 `_content_probe_room()` 读到的四向空档与桌宠在屏上的真实边界脱钩，
        探针位移摆不出可信的一发。把窗口转浮动是合成器那只手（`niri msg action
        toggle-window-floating`），产品这一侧够不到 ⇒ 只能请人按。勾「不再提示」写回
        `fidus.ask_float`，此后直接量（人自己负责窗口已经是浮动的）。
        """
        cfg = self.config.setdefault("fidus", {})
        if not cfg.get("ask_float", True):
            return True
        from PyQt5.QtWidgets import QMessageBox
        from meapet.message_dialog import MeaMessageDialog

        dialog = MeaMessageDialog(
            self,
            title="先把窗口切成浮动",
            text=(
                "平铺的窗口会按合成器预设出现，请求的大小拿不到——\n"
                "这样量出来的位置不可信。\n\n"
                "请先按 Mod+V 把这个窗口切成浮动，再点「继续」开始测量。"
            ),
            icon=QMessageBox.Question,
            buttons=int(QMessageBox.Yes | QMessageBox.No),
            default_button=int(QMessageBox.Yes),
            check_text="不再提示",
        )
        if dialog.exec_() != int(QMessageBox.Yes):
            return False
        if dialog.is_checked():
            cfg["ask_float"] = False
            self._save_config()
        return True

    def _start_premount_measure(self, x: int, y: int, w: int, h: int) -> None:
        """「量完再挂」那一发：不喂先验、H 手、2 px 闭环、**四向逐个试**（裁决 2026-10-02 点甲 + 四角裁决）。

        判据的强度不在这里另立：`FP.PREMOUNT_TOL_PX` / `FP.PREMOUNT_MIN_EDGE` /
        `FP.PREMOUNT_MOVES` 的出处写在 `fidus_position.py` 那三条常量上（H25 四格 +
        H14 撒谎档 + 「看哪个能用再采信哪个」）。

        四向把最坏成本从"一发"抬到"至多四发定住"：单发 `locate` 实测 0.9–9.4 s，四发**可能**
        越过 `_MOUNT_WAIT_MS=20_000`。这里**不偷偷放宽预算**（人工点定的 20 秒是宽口径），
        超时按裁决 1 收尾——不切 + 出声。命中即采信 ⇒ 正常场只需一向，成本与单向同阶。
        """
        from meapet.desktop import fidus_position as FP  # 延迟导入：关着就不付这份账

        if getattr(self, "_fidus_busy", False):
            self._premount_refuse("上一次定位仍在进行",
                                  "上一次定位还没跑完，这次没切穿透")
            return
        if not FP.have_engine():
            self._premount_refuse("这个环境没有 fidus（随包分发未落地？）",
                                  "这个版本没带定位引擎，量不了位置，没切穿透")
            return
        frame = self._fidus_current_frame()
        if frame is None:
            self._premount_refuse("拿不到当前离屏帧（离屏渲染不可用？）",
                                  "拿不到当前画面，量不了位置，没切穿透")
            return
        if not self._confirm_float_first():
            self._premount_refuse("人没确认窗口已切成浮动（平铺下量不准）",
                                  "要先按 Mod+V 把窗口切成浮动才量得准，没切穿透")
            return
        # 先把窗口放大成「画布 + 四边各 slack」再读空档：常态下窗口**就是**画布（或它的裁剪），
        # `_content_probe_room()` 四数恒 ≤0，不放大就没有任何一发摆得出去
        # （人工 2026-10-03「没有再次请求一个正确大小的 Qt 窗口」「四向空档恒为否」）。
        want, got = self._request_probe_window()
        room = self._content_probe_room()
        # 重发 viewport 几何（`_request_probe_window` 里那一发）可能把画布算大算小，而 fidus
        # 量的是**此刻屏上**那份内容 ⇒ 挂载矩形跟着实际几何走。留在按下去的那一数上，挂出来的
        # surface 就和量出来的中心差一份画布尺寸差。（只有尺寸会从这里漂：画布的偏移我们
        # 全程没碰，窗口也没挪——`self.move()` 在这条路上一次都不该出现。）
        now = self._layer_geometry()
        if now != (int(x), int(y), int(w), int(h)):
            safe_print(f"[fidus] 重发几何把画布改了：按下去 {(x, y, w, h)} → 现在 {now}"
                       " ⇒ 这一发按现在的挂")
            x, y, w, h = now
        step = int(round(FP.MOVE_PX))
        if all(r < step for r in room):
            # 四向**全**空才拒。常态左/上是 0（画布贴窗口左上角）不是缺口：摆不出的方向由
            # `locate` 逐向跳过并各写一行（人工 ③「看哪个能用再采信哪个」），这道闸没有理由
            # 替它判死。真判死的是"一发都没有"——那正是尺寸请求被吃、窗口又不大的样子。
            self._restore_probe_window()    # 拒了就不许留下一个放大过、mask 被清的窗口
            self._premount_refuse(
                f"窗口尺寸请求 {want[0]}×{want[1]} 实得 {got[0]}×{got[1]}，四向空档"
                f" (右{room[0]},下{room[1]},左{room[2]},上{room[3]}) 摆不出探针 {step} px"
                "（尺寸请求又被吃 ⇒ 窗口多半还平铺着）",
                "窗口拿不到放得下定位探针的尺寸，量不了位置，没切穿透"
                "（先按 Mod+V 把窗口切成浮动再试）")
            return
        safe_print(f"[fidus] 探针窗口 请求 {want[0]}×{want[1]} 实得 {got[0]}×{got[1]}"
                   f" ⇒ 四向空档 (右{room[0]},下{room[1]},左{room[2]},上{room[3]})")

        token = self._begin_fidus_round(x, y, w, h, premount=True)
        self._pending_mount = (int(x), int(y), int(w), int(h))
        self._content_probe_base = None
        if getattr(self, "_mount_timer", None) is None:
            self._mount_timer = QTimer(self)
            self._mount_timer.setSingleShot(True)
            self._mount_timer.setInterval(_MOUNT_WAIT_MS)
            self._mount_timer.timeout.connect(self._fidus_mount_timeout)
        self._mount_timer.start()
        self._show_bubble("正在定位…（第一次约 2 秒，桌宠会滑动几下）",
                          bubble_duration_ms(self.config, "interaction"))
        FP.request_locate(
            frame, None,                       # ← 不喂先验：信念已知在撒谎（H23 偏 342 px）
            move=lambda dx, dy: self._fidus_request_move(dx, dy, token),
            on_done=lambda fix: self._fidus_on_result(fix, token),
            log=lambda k, v: safe_print(f"[fidus] {k}: {v}"),
            tol_px=FP.PREMOUNT_TOL_PX,
            min_edge=FP.PREMOUNT_MIN_EDGE,
            moves=FP.PREMOUNT_MOVES,
        )

    def _fidus_mount_timeout(self) -> None:
        """20 s 到：按裁决 1 收尾。预算内**只许一发**，不拿超时兜重试。"""
        if getattr(self, "_pending_mount", None) is None:
            return
        # 作业可能还在 fidus 线程上跑：先作号，再收尾 ⇒ 它迟到的读数与迟到的位移请求
        # 都落不到下一次挂载上（否则下一次的位来自上一次）。
        self._fidus_round = getattr(self, "_fidus_round", 0) + 1
        self._finish_premount_measure(None, f"{_MOUNT_WAIT_MS // 1000} 秒内没等到读数")

    def _cancel_premount_measure(self) -> None:
        """测量在飞时切回交互态：作废这一发，**不留半个 surface**、不把 `_layer_timer` 起起来。"""
        if getattr(self, "_pending_mount", None) is None:
            return
        self._fidus_round = getattr(self, "_fidus_round", 0) + 1
        self._pending_mount = None
        for name in ("_mount_timer", "_fidus_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        self._fidus_busy = False
        self._fidus_premount = False
        self._fidus_finished = False
        self._fidus_result = None
        self._fidus_move_req = None
        ack, self._fidus_move_ack = self._fidus_move_ack, None
        if ack is not None:
            ack.set()                    # 放行在飞的那一发，别让它白等 3 秒
        self._fidus_settle = 0
        self._restore_content_probe()
        self._restore_probe_window()     # 放大过就得缩回去：透明边留在交互态是白送的热区
        safe_print("[layer] 交互模式请求到场：取消在飞的量完再挂")

    def _finish_premount_measure(self, fix, why: str | None = None) -> None:
        """预挂载那一发的收尾：先 `restore()`，量出来才挂，量不出来**不切**。"""
        timer = getattr(self, "_mount_timer", None)
        if timer is not None:
            timer.stop()
        timer = getattr(self, "_fidus_timer", None)
        if timer is not None:
            timer.stop()
        pending = getattr(self, "_pending_mount", None)
        self._pending_mount = None
        self._fidus_busy = False
        self._fidus_premount = False
        self._restore_content_probe()
        # 窗口也得缩回常态**在挂载之前**：`_pending_mount` 那份矩形是放大之前就取好的画布屏幕
        # 矩形，而 `_request_probe_window` 全程没动画布的屏幕位置，所以两者本来就一致；
        # 但 mask 与热区只有在缩回来之后才是用户认得的那个形状（没放大过则是空转）。
        self._restore_probe_window()
        if pending is None:
            return                      # 已被取消（切回交互态），结果无处安放
        if fix is None:
            self._premount_refuse(why or "位移闭环没过／没有可信读数（每条 log 都在上面）",
                                  "没量准位置，没切穿透（桌宠还看得见、还点得动）")
            return
        _x, _y, w, h = pending
        rect = _rect_from_center(fix.center[0], fix.center[1], w, h)
        safe_print(f"[fidus] 测量位 ({rect[0]},{rect[1]}) {w}x{h} ← 中心 "
                   f"({fix.center[0]:.2f},{fix.center[1]:.2f}) 贴片 {fix.edge}² "
                   f"conf={fix.conf:.4f} ceiling={fix.ceiling:.4f}")
        self._do_layer_mount(*rect, measured=True)

    def _maybe_start_fidus_locate(self, x: int, y: int, w: int, h: int) -> None:
        if not self._fidus_enabled():
            return
        if getattr(self, "_fidus_busy", False):
            safe_print("[fidus] 上一次定位仍在进行，本次只用请求坐标")
            return
        from meapet.desktop import fidus_position as FP  # 延迟导入：关着就不付这份账

        if not FP.have_engine():
            # 引擎压根不在场：这一轮开不了，也不该开（校准要 2 秒还会闪屏，换不来读数）。
            # 气泡说的是"没带"，不是"没量准"——后者会把人引去查屏幕，而缺口在打包面。
            safe_print("[fidus] ✗ 这个环境没有 fidus，定位不启动（随包分发未落地？）")
            self._show_bubble(
                "这个版本没带定位引擎，量不了位置",
                bubble_duration_ms(self.config, "interaction"),
            )
            return
        frame = self._fidus_current_frame()
        if frame is None:
            # 离屏帧拿不到：这一轮开不了。静默 return 是被禁的形态（出声闸）——
            # 症状是"启用了 fidus 但什么也不发生"，人无从下手：不知道该查渲染面
            # 还是查引擎。气泡说的是"画面拿不到"，不是"没量准"——后者会把人引去
            # 等校准，而缺口在渲染面这一侧。
            safe_print("[fidus] ✗ 拿不到当前离屏帧，定位不启动（离屏渲染不可用？）")
            self._show_bubble(
                "拿不到当前画面，定位不了位置",
                bubble_duration_ms(self.config, "interaction"),
            )
            return
        token = self._begin_fidus_round(x, y, w, h, premount=False)
        self._show_bubble(
            "正在定位…（第一次约 2 秒，窗口会闪一下）",
            bubble_duration_ms(self.config, "interaction"),
        )
        # 先验吃**画布**中心：桥接层那个锚点定死在帧中心（`iter_candidates`），而帧就是整画布，
        # 与 surface 切到哪一块无关。`origin=(0,0)` 时这一式逐位等于 `x + w/2, y + h/2`。
        FP.request_locate(
            frame, self._layer_canvas_center(x, y, w, h),
            move=lambda dx, dy: self._fidus_request_move(dx, dy, token),
            on_done=lambda fix: self._fidus_on_result(fix, token),
            log=lambda k, v: safe_print(f"[fidus] {k}: {v}"),
        )

    def _fidus_current_frame(self):
        """取当前离屏帧的 RGBA numpy 视图（副本）—— fidus 的模板与它同源。"""
        widget = getattr(self, "sprite_label", None)
        renderer = getattr(widget, "render_offscreen", None)
        if not callable(renderer):
            return None
        try:
            import numpy as np

            img = renderer()
            if img is None or img.isNull():
                return None
            if img.format() != QImage.Format_RGBA8888:
                img = img.convertToFormat(QImage.Format_RGBA8888)
            w, h = img.width(), img.height()
            buf = img.constBits()
            buf.setsize(w * h * 4)
            # 显式 copy：constBits() 只是 Qt 内存的窗口，img 一析构视图就悬垂
            return np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4).copy()
        except Exception as exc:
            safe_print(f"[fidus] 取帧失败: {exc}")
            return None

    def _fidus_request_move(self, dx: float, dy: float, token: int | None = None) -> bool:
        """在 fidus 线程上被调用：把位移请求交给 GUI 线程，等它落位并提交帧。

        回**这一发到底摆出去了没有**（四向判据的输入，人工裁决 2026-10-02「看哪个能用
        再采信哪个」）：摆不出的方向不该重注册、不该占 `_settle` 的账，直接换下一向。
        GUI 线程把成败写进结果盒再 `ev.set()`，所以 `ev.wait()` 返回后读它是安全的；
        超时或那一发已作废 ⇒ 盒里还是 `None`，按"没摆出去"处理。

        轮次号一起带过去：超时或取消之后这一发可能已经作废，迟到的位移请求不能再落到
        GUI 线程上——那摆的是**下一次**测量前的子控件，或下一次的 surface。
        """
        ev = threading.Event()
        result: list = [None]
        self._fidus_move_req = (dx, dy, ev, token, result)
        ev.wait(timeout=3.0)
        return bool(result[0])

    def _fidus_on_result(self, fix, token: int | None = None) -> None:
        """在 fidus 线程上被调用：只交一个引用，套用到 GUI 线程里做。"""
        current = getattr(self, "_fidus_round", 0)
        if token is not None and token != current:
            safe_print(f"[fidus] 第 {token} 轮的迟到读数撞上第 {current} 轮 ⇒ 丢弃")
            return
        self._fidus_result = fix
        self._fidus_finished = True

    def _fidus_service(self) -> None:
        """GUI 线程上的唯一接触点：搬运移请求、等合成器收帧、套用测量结果。"""
        if self._fidus_settle > 0:
            self._fidus_settle -= 1
            if self._fidus_settle == 0 and self._fidus_move_ack is not None:
                ev, self._fidus_move_ack = self._fidus_move_ack, None
                ev.set()
            return
        req = self._fidus_move_req
        if req is not None:
            self._fidus_move_req = None
            dx, dy, ev, token, result = req
            if token is not None and token != getattr(self, "_fidus_round", 0):
                ev.set()   # 作废那一发的探针位移不摆，但也不让 fidus 线程干等 3 秒
                return
            if getattr(self, "_fidus_premount", False):
                # 预挂载：顶层窗口还没挂上，能挪的只有窗口内的子控件（H 手）。
                # 摆不出就当场回 False ⇒ `locate` 换下一向，不重注册、不占定住的账。
                result[0] = self._content_probe_shift(dx, dy)
                if result[0]:
                    self._fidus_move_ack = ev
                    self._fidus_settle = _FIDUS_SETTLE_TICKS
                else:
                    ev.set()
                return
            cx, cy, _w, _h = self._fidus_surface
            result[0] = self._fidus_place(cx + int(round(dx)), cy + int(round(dy)))
            if result[0]:
                self._fidus_move_ack = ev
                self._fidus_settle = _FIDUS_SETTLE_TICKS
            else:
                ev.set()  # 摆不动也放行：下一发读数对不上，闭环自会判失败
            return
        if not self._fidus_finished:
            return
        fix = self._fidus_result
        self._fidus_finished = False
        self._fidus_result = None
        self._fidus_timer.stop()
        self._fidus_busy = False
        if getattr(self, "_fidus_premount", False):
            # 预挂载的收尾整个交给 `_finish_premount_measure`：这里不能报"沿用原来的位置"
            # ——没切穿透时桌宠本来就在原位，那句会把人引去找一次不存在的移动。
            self._finish_premount_measure(fix)
            return
        if fix is None:
            # `locate` 的读数取自探针位移**之前**，它自己不回摆：位移之后的每条退回出口
            # （重注册失败／定不住／闭环超容差）都会把桌宠留在偏 `MOVE_PX` 处，而气泡正
            # 要说"沿用原来的位置"。所以这句话得有动作撑着——把挂载位再请求一次。
            # 没动过时这一发幂等，但也不必发：先比一次位置（H15 A2 实测候选全拒时 0 摆位）。
            mx, my, mw, mh = self._fidus_mount
            if self._fidus_surface[:2] != (mx, my) and self._fidus_place(mx, my):
                widget = getattr(self, "sprite_label", None)
                if widget is not None:
                    widget._proxy_rect = self._layer_canvas_rect(mx, my, mw, mh)
            self._show_bubble("没能量准位置，沿用原来的位置",
                              bubble_duration_ms(self.config, "interaction"))
            return
        sx, sy, sw, sh = self._fidus_surface
        # `fix.center` 是**画布**中心的屏幕坐标（锚点按整帧定死，与 surface 切走的那圈透明边无关），
        # 所以反解要用画布尺寸；而 `set_position` 吃的是 surface 的 margin，要把帧内原点加回去。
        # `origin=(0,0)`、画布=surface 时这两步逐位退化成"用 w,h 反解、直接拿去摆"。
        ox, oy = getattr(self, "_layer_frame_origin", (0, 0))
        cw, ch = getattr(self, "_layer_canvas_size", None) or (sw, sh)
        cx, cy, _nw, _nh = _rect_from_center(fix.center[0], fix.center[1], cw, ch)
        nx, ny = cx + int(ox), cy + int(oy)
        if not self._fidus_place(nx, ny):
            return
        widget = getattr(self, "sprite_label", None)
        if widget is not None:
            # §5.1：_proxy_rect 与摆位请求吃同一个测量值，两件事不许分叉
            widget._proxy_rect = self._layer_canvas_rect(nx, ny, sw, sh)
        safe_print(f"[fidus] 校正 ({sx},{sy})→({nx},{ny}) 贴片 {fix.edge}² "
                   f"conf={fix.conf:.4f} ceiling={fix.ceiling:.4f}")
        self._show_bubble("已按屏幕真值校正位置",
                          bubble_duration_ms(self.config, "interaction"))

    def _fidus_place(self, x: int, y: int) -> bool:
        backend = getattr(self, "_layer_backend", None)
        setter = getattr(backend, "set_position", None)
        if not callable(setter):
            safe_print("[fidus] 后端不支持 set_position，定位作废")
            return False
        try:
            setter(int(x), int(y))
        except Exception as exc:
            safe_print(f"[fidus] set_position 失败: {exc}")
            return False
        _x, _y, w, h = self._fidus_surface
        self._fidus_surface = (int(x), int(y), w, h)
        # 锚点跟着走（#89）：fidus 那一发是按屏幕读数做的**主动**摆放，它才是此后
        # 宽裕相要回收的位置——回收挂载那一刻会把 fidus 测出来的修正抹掉。
        self._fidus_owns_placement(int(x), int(y))
        return True

    def _layer_conform_to_configure(self, img):
        """把**提交尺寸**对齐到合成器 configure 出来的事实（门控只放行两者相等的帧）。

        桥接层的像素门控（`native/layer_shell/src/state.rs:477`）比的是"这一帧提交的尺寸"
        与"逻辑尺寸＝configure 的事实"。挂载请求被合成器夹小过 ⇒ 两者不等 ⇒ **每一帧**被我
        自己丢掉，而丢掉的样子是整片空白，不是"缺一角"（人工 2026-10-03 那一条
        `只配出 456x463（请求 512x512）` 就是这个态）。裁决「测量赢 ⇒ 允许切头/切脚」要的
        就是"位置不动、按能放下的大小切"这一只手，此前只有前半句落地了，所以那一条只出声
        不显形。

        吃 `_fit_size` —— 回读轮已经缓存的那一份事实，**不新增跨 FFI 回读**（#78 稳态口径）。
        没读到过 configure（`None`／旧产物没有回读通道）⇒ 原样提交，行为逐位不变。
        """
        fit = getattr(self, "_fit_size", None)
        if not fit:
            return img
        w, h = int(fit[0]), int(fit[1])
        iw, ih = img.width(), img.height()
        ox, oy = getattr(self, "_layer_frame_origin", (0, 0))
        if (iw, ih) == (w, h) and (ox, oy) == (0, 0):
            return img                      # 常态：一次都不拷
        if w <= 0 or h <= 0:
            return img
        if w > iw or h > ih:
            # 合成器配的比帧还大。模型 `configure = min(请求, 剩余空间)` 只会往小夹，不会
            # 往大给 ⇒ 走到这里说明模型不适用，**不猜**第二种解法（补边要每帧起一次 QPainter，
            # 而那是在给一个没见过的态付固定成本）。原样提交：门控照丢，但报一次数。
            self._layer_conform_report(f"configure {w}x{h} 比帧 {iw}x{ih} 大 ⇒ 不裁不补（模型不适用）")
            return img
        # 原点夹回帧内：`_layer_frame_origin` 是挂载那一帧的轮廓左/上界，而帧尺寸可能在
        # 穿透期被改（换 size_factor）。夹回去保证 `copy` 取到的窗口整块在帧内——
        # QImage.copy 会静默裁掉越界部分，那交出去的尺寸又不等于 configure，白丢一帧。
        ox = max(0, min(int(ox), iw - w))
        oy = max(0, min(int(oy), ih - h))
        self._layer_conform_report(
            f"提交尺寸按 configure 对齐 {iw}x{ih}→{w}x{h}（帧内原点 ({ox},{oy})，"
            f"切掉右 {iw - ox - w}×下 {ih - oy - h}）")
        return img.copy(QRect(ox, oy, w, h))

    def _layer_conform_report(self, text: str) -> None:
        """对齐尺寸这件事只在**第一次**真要动刀时出声一次。

        它每帧都会成立（configure 不等于请求是个持续状态），连起来说会把日志淹掉；
        而"从没说过"又会让下一次排查的人以为没在裁。每轮挂载各重置一次。
        """
        if getattr(self, "_layer_conform_reported", False):
            return
        self._layer_conform_reported = True
        safe_print(f"[layer] ⚠ {text}")

    def _push_layer_frame(self) -> None:
        """把当前画面推到 layer surface（Live2D 走离屏渲染，PNG 交回它手上的整帧）。"""
        backend = getattr(self, "_layer_backend", None)
        if backend is None:
            return
        if self._layer_fit_service():
            return          # 本拍在等回读／刚发出夹移，推了也必被尺寸门控丢掉
        widget = getattr(self, "sprite_label", None)
        renderer = getattr(widget, "render_offscreen", None)
        if not callable(renderer):
            renderer = getattr(widget, "frame_image", None)
        if not callable(renderer):
            return
        try:
            img = renderer()
            if img is None or img.isNull():
                self._layer_fail = getattr(self, "_layer_fail", 0) + 1
                if self._layer_fail <= 3:
                    print(f"[layer] ✗ 第 {self._layer_fail} 次拿到空帧", flush=True)
                return
            backend.update_pixels(self._layer_conform_to_configure(img))
        except Exception as exc:
            print(f"[layer] ✗ 推帧异常: {type(exc).__name__}: {exc}", flush=True)



    # ------------------------------------------------------------ 画布尺寸工具

    def _read_canvas_size_from_model(self, model) -> tuple[int, int] | None:
        """从 Live2D 模型读取画布尺寸，带多层回退。

        优先使用 get_suggested_size()，然后对返回值做严格范围校验。
        如果 SDK 返回无效占位值（如 (1, 2)），依次尝试其他可能的 API。
        全部失败时返回 None，由调用方决定回退策略。
        """
        if model is None:
            return None

        # 第 1 层：get_suggested_size()
        try:
            w, h = model.get_suggested_size()
            w, h = int(w), int(h)
            if MIN_CANVAS_SIZE <= w <= MAX_CANVAS_SIZE and MIN_CANVAS_SIZE <= h <= MAX_CANVAS_SIZE:
                return w, h
            safe_print(f"[WARN] get_suggested_size 返回异常值 ({w}, {h})，尝试其他 API")
        except (AttributeError, TypeError, ValueError) as exc:
            safe_print(f"[WARN] get_suggested_size 调用失败: {exc}")

        # 第 2 层：尝试其他常见 API 名称
        for method_name in ("get_canvas_size", "GetCanvasSize", "getCanvasSize"):
            if hasattr(model, method_name):
                try:
                    func = getattr(model, method_name)
                    result = func() if callable(func) else func
                    if isinstance(result, (tuple, list)) and len(result) >= 2:
                        w, h = int(result[0]), int(result[1])
                    else:
                        w, h = int(result), int(result)
                    if MIN_CANVAS_SIZE <= w <= MAX_CANVAS_SIZE and MIN_CANVAS_SIZE <= h <= MAX_CANVAS_SIZE:
                        safe_print(f"[INFO] 通过 {method_name} 获取画布尺寸: {w}x{h}")
                        return w, h
                except Exception as exc:
                    safe_print(f"[WARN] {method_name} 调用失败: {exc}")

        # 第 3 层：尝试访问底层 SDK 模型的 canvas 属性
        sdk_model = getattr(model, "_model", None) or getattr(model, "model", None)
        if sdk_model is not None:
            for attr_pair in (
                ("GetCanvasWidth", "GetCanvasHeight"),
                ("getCanvasWidth", "getCanvasHeight"),
                ("canvasWidth", "canvasHeight"),
                ("width", "height"),
            ):
                try:
                    getter_w = getattr(sdk_model, attr_pair[0], None)
                    getter_h = getattr(sdk_model, attr_pair[1], None)
                    if getter_w is not None and getter_h is not None:
                        w = getter_w() if callable(getter_w) else int(getter_w)
                        h = getter_h() if callable(getter_h) else int(getter_h)
                        w, h = int(w), int(h)
                        if MIN_CANVAS_SIZE <= w <= MAX_CANVAS_SIZE and MIN_CANVAS_SIZE <= h <= MAX_CANVAS_SIZE:
                            safe_print(f"[INFO] 通过 sdk_model.{attr_pair[0]}/{attr_pair[1]} 获取画布尺寸: {w}x{h}")
                            return w, h
                except Exception:
                    continue

        return None

    def _live2d_base_size(self) -> tuple[int, int]:
        """返回 Live2D 模型画布的原始尺寸（无缩放），带多重回退。

        回退顺序：
        1. 从模型 SDK 读取（_read_canvas_size_from_model）
        2. 从 config.json 的 live2d.default_canvas_size 读取
        3. DEFAULT_CANVAS_SIZE 硬编码 (1024, 1024)
        """
        model = getattr(self, "_l2d_model", None)
        canvas = self._read_canvas_size_from_model(model)
        if canvas is not None:
            return canvas

        # 回退 2：配置文件
        l2d_cfg = self.config.get("live2d", {})
        default_size = l2d_cfg.get("default_canvas_size", None)
        if isinstance(default_size, (list, tuple)) and len(default_size) >= 2:
            try:
                w, h = int(default_size[0]), int(default_size[1])
                if MIN_CANVAS_SIZE <= w <= MAX_CANVAS_SIZE and MIN_CANVAS_SIZE <= h <= MAX_CANVAS_SIZE:
                    safe_print(f"[INFO] 使用配置中的 default_canvas_size: {w}x{h}")
                    return w, h
            except (TypeError, ValueError):
                pass

        # 回退 3：硬编码默认值
        w, h = DEFAULT_CANVAS_SIZE
        safe_print(f"[INFO] 使用硬编码默认画布尺寸: {w}x{h}")
        return w, h

    # ------------------------------------------------------------ 窗口尺寸调整

    def _fit_window_to_model(self):
        """首帧后按实际画布刷新视觉视口，并保持模型脚底位置。"""
        model = self._l2d_model
        if model is None:
            safe_print("[live2d] _fit_window_to_model: 模型未加载，跳过")
            return

        factor = float(getattr(self, "_size_factor", 1.0))
        before = QRect(self.x(), self.y(), self.width(), self.height())
        before_canvas = (
            QRect(self.sprite_label.geometry())
            if self.sprite_label is not None
            else None
        )
        layout = self._apply_live2d_viewport_geometry(factor)
        self._reanchor_after_resize(
            before,
            before_live2d_canvas=before_canvas,
        )

        safe_print(
            "[live2d] 窗口已适配视觉视口: "
            f"window={layout.window_width}x{layout.window_height} "
            f"canvas={layout.widget_width}x{layout.widget_height} "
            f"offset=({layout.widget_x},{layout.widget_y}) factor={factor}"
        )

    def _reveal_live2d_window(self):
        """刷新已经正常映射的 OpenGL 子控件，不重置顶层窗口。"""
        widget = self.sprite_label
        self.setWindowOpacity(1.0)
        if widget is not None:
            widget.show()
            widget.raise_()
            widget.update()

    def _on_live2d_initialization_failed(self, reason: str):
        if not self._l2d_pending:
            return
        self._fallback_to_png(reason or "unknown OpenGL error")

    def _on_live2d_startup_timeout(self):
        if (
            self._l2d_pending
            and self._live2d_startup_widget is self.sprite_label
        ):
            self._fallback_to_png("等待 Live2D 首帧超时")

    def _fallback_to_png(self, reason: str):
        """清理未就绪的 OpenGL 控件，并在同一最终位置显现 PNG。"""
        self._cancel_live2d_startup_timeout()
        safe_print(f"[pet] Live2D 不可用，回退 PNG: {reason}")
        old_widget = self.sprite_label
        if old_widget is not None and not isinstance(old_widget, SpriteCanvas):
            try:
                if hasattr(old_widget, "shutdown"):
                    old_widget.shutdown()
                old_widget.hide()
                old_widget.deleteLater()
            except Exception:
                pass
        self.sprite_label = None
        self._l2d_model = None
        self._live2d_startup_widget = None
        self._l2d_pending = False
        self._use_live2d = False
        self.renderer = None
        self._init_png_renderer()
        try:
            self._place_initial_position()
        except Exception as exc:
            safe_print(f"[pet] PNG fallback placement skipped: {exc}")
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self._mark_renderer_ready()

    def _init_live2d(self):
        from meapet.config.store import resolve_resource_path
        from meapet.desktop.live2d_widget import Live2DModel
        l2d_cfg = self.config.get("live2d", {})
        model_dir = resolve_resource_path(l2d_cfg.get("model_dir", ""))
        safe_print(f"[live2d] 开始初始化，model_dir={model_dir}")
        if not model_dir or not os.path.isdir(model_dir):
            safe_print("[live2d] 模型目录不存在，回退至 PNG")
            self._use_live2d = False
            return
        self._l2d_model = Live2DModel(model_dir)
        widget = self._l2d_model.create_widget(self)
        self.sprite_label = widget
        widget.head_patted.connect(self._on_head_patted)
        widget.lower_left_patted.connect(self._on_lower_left_patted)
        widget.lower_right_patted.connect(self._on_lower_right_patted)
        widget.chat_requested.connect(self._start_chat)
        widget.first_frame_ready.connect(self._on_live2d_first_frame)
        widget.initialization_failed.connect(
            self._on_live2d_initialization_failed
        )
        # 子控件保留完整模型画布，顶层窗口只显示配置的视觉视口。
        layout = self._apply_live2d_viewport_geometry(self._size_factor)
        widget.show()
        safe_print(
            "[live2d] 控件已创建，等待首帧: "
            f"window={layout.window_width}x{layout.window_height} "
            f"canvas={layout.widget_width}x{layout.widget_height}"
        )

    def _safe_renderer(self):
        if self._use_live2d and self._l2d_model:
            return self._l2d_model
        return self.renderer

    def _scaled_live2d_size(self, factor: float) -> tuple[int, int]:
        """返回裁去模型透明留白后的顶层窗口尺寸。"""
        layout = self._live2d_viewport_layout(factor)
        return layout.window_width, layout.window_height

    def _live2d_viewport_layout(self, factor: float) -> Live2DViewportLayout:
        """按当前模型和配置计算 Live2D 父子窗口几何。"""
        base_w, base_h = self._live2d_base_size()
        live2d = (getattr(self, "config", {}) or {}).get("live2d") or {}
        return calculate_live2d_viewport_layout(
            base_w,
            base_h,
            factor,
            live2d.get("window_mask"),
        )

    def _apply_live2d_viewport_geometry(
        self,
        factor: float,
    ) -> Live2DViewportLayout:
        """应用完整画布子控件与裁剪顶层窗口的几何。"""
        layout = self._live2d_viewport_layout(factor)
        widget = self.sprite_label
        if widget is not None:
            widget.setGeometry(
                layout.widget_x,
                layout.widget_y,
                layout.widget_width,
                layout.widget_height,
            )
        self.resize(layout.window_width, layout.window_height)
        self._apply_live2d_window_region(layout)
        return layout

    def _apply_live2d_window_region(
        self,
        layout: Live2DViewportLayout | None = None,
    ) -> bool:
        """应用可选静态窗口形状；无有效形状时恢复普通矩形窗口。"""
        if not getattr(self, "_use_live2d", False):
            self.clearMask()
            return False
        if layout is None:
            layout = self._live2d_viewport_layout(self._size_factor)
        live2d = (getattr(self, "config", {}) or {}).get("live2d") or {}
        region = calculate_live2d_window_region(
            layout,
            live2d.get("window_shape"),
        )
        if region is None:
            self.clearMask()
            return False
        self.setMask(region)
        return True

    def _apply_hit_region(self) -> None:
        """启动后或渲染模式变化时同步当前 Live2D 窗口形状。"""
        if getattr(self, "_use_live2d", False) and self.sprite_label is not None:
            self._apply_live2d_window_region()
        else:
            self.clearMask()

    def _apply_live2d_viewport_preference(self) -> bool:
        """热应用视觉视口与模型锚点，同时维持模型和气泡位置。"""
        if not getattr(self, "_use_live2d", False) or self.sprite_label is None:
            return False
        before = QRect(self.x(), self.y(), self.width(), self.height())
        before_canvas = QRect(self.sprite_label.geometry())
        self._apply_live2d_viewport_geometry(self._size_factor)
        self._reanchor_after_resize(
            before,
            before_live2d_canvas=before_canvas,
        )
        self._position_bubble()
        return True

    def _capture_live2d_viewport_preview(self):
        """抓取一次完整 Live2D 帧供配置页框选；异常或超大画布返回 None。"""
        widget = getattr(self, "sprite_label", None)
        if not getattr(self, "_use_live2d", False) or widget is None:
            return None
        grab = getattr(widget, "grabFramebuffer", None)
        if not callable(grab):
            return None
        try:
            width = max(0, int(widget.width()))
            height = max(0, int(widget.height()))
            dpr_getter = getattr(widget, "devicePixelRatioF", None)
            dpr = float(dpr_getter()) if callable(dpr_getter) else 1.0
            source_pixels = width * height * max(1.0, dpr) ** 2
            if (
                width <= 0
                or height <= 0
                or source_pixels > LIVE2D_PREVIEW_MAX_SOURCE_PIXELS
            ):
                return None
            image = grab()
            if image is None or image.isNull():
                return None
            image = image.copy()
            if max(image.width(), image.height()) > LIVE2D_PREVIEW_MAX_EDGE:
                image = image.scaled(
                    LIVE2D_PREVIEW_MAX_EDGE,
                    LIVE2D_PREVIEW_MAX_EDGE,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            return image
        except Exception as exc:
            safe_print(f"[live2d] 配置预览抓取失败: {type(exc).__name__}")
            return None

    def _safe_set_mood(self, mood: str):
        r = self._safe_renderer()
        if r:
            r.set_mood(mood)

    def _safe_set_expression(self, expr: str):
        r = self._safe_renderer()
        if r:
            r.set_expression(expr)

    def _update_sprite(self):
        if self._use_live2d:
            return
        pixmap = self.renderer.get_current_pixmap()
        if pixmap.isNull():
            return
        target_w = int(pixmap.width() * self._scale * self._size_factor)
        target_h = int(pixmap.height() * self._scale * self._size_factor)
        if hasattr(self.renderer, "get_scaled_pixmap"):
            scaled = self.renderer.get_scaled_pixmap(target_w, target_h)
        else:
            scaled = pixmap.scaled(
                target_w,
                target_h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        target_size = scaled.size()
        if self.sprite_label.pos() != QPoint(0, 0):
            self.sprite_label.move(0, 0)
        if self.sprite_label.size() != target_size:
            self.sprite_label.resize(target_size)
        if self.size() != target_size:
            self.resize(target_size)
        if hasattr(self.sprite_label, "set_frame"):
            self.sprite_label.set_frame(scaled)
        else:
            self.sprite_label.setPixmap(scaled)

    def _on_sprite_changed(self, code: str):
        self._update_sprite()

    def _set_size_factor(self, factor: float):
        """按百分比档位直接应用窗口大小并写回配置（右键菜单快捷项）。"""
        safe_print(f"[SET_SIZE_FACTOR] input={factor}, type={type(factor).__name__}")
        new_factor = normalize_pet_size_factor(factor)
        safe_print(f"[SET_SIZE_FACTOR] normalized={new_factor}")

        # 关键修复：只调用一次 _size_factor_preview（原代码调用了两次）
        try:
            self._size_factor_preview(new_factor)
            safe_print(f"[SET_SIZE_FACTOR] after preview: size={self.width()}x{self.height()}")
        except Exception as exc:
            import traceback
            safe_print(f"[SET_SIZE_FACTOR] preview failed: {exc}")
            traceback.print_exc()

        self.config.setdefault("display", {})["size_factor"] = new_factor
        self._save_config()
        show = getattr(self, "_show_bubble", None)
        if callable(show):
            show(
                status_language.window_size_applied(new_factor),
                bubble_duration_ms(self.config, "interaction"),
                mood=None,
            )

    def _apply_display_preference(self):
        """把 `display.size_factor` 应用到当前窗口（配置页保存后立即生效）。"""
        display = (getattr(self, "config", {}) or {}).get("display") or {}
        factor = normalize_pet_size_factor(display.get("size_factor", 1.0))
        if abs(factor - float(getattr(self, "_size_factor", 1.0))) < 1e-3:
            return
        self._size_factor_preview(factor)

    def _size_factor_preview(self, factor: float):
        """预览 size_factor 变化：调整窗口大小，保持位置合理。"""
        factor = normalize_pet_size_factor(factor)
        safe_print(f"[PREVIEW] factor={factor}, use_live2d={self._use_live2d}, model={self._l2d_model is not None}")

        before = QRect(self.x(), self.y(), self.width(), self.height())
        before_canvas = (
            QRect(self.sprite_label.geometry())
            if self._use_live2d and self.sprite_label is not None
            else None
        )
        self._size_factor = factor

        if self._use_live2d and self.sprite_label:
            layout = self._apply_live2d_viewport_geometry(factor)
            safe_print(
                "[PREVIEW] Live2D viewport: "
                f"window=({layout.window_width},{layout.window_height}) "
                f"canvas=({layout.widget_width},{layout.widget_height}) "
                f"offset=({layout.widget_x},{layout.widget_y})"
            )
        else:
            if self.renderer is None:
                safe_print("[PREVIEW] no renderer available, skip")
                return
            pixmap = self.renderer.get_current_pixmap()
            if not pixmap.isNull():
                new_w = max(80, int(pixmap.width() * self._scale * factor))
                new_h = max(80, int(pixmap.height() * self._scale * factor))
                safe_print(f"[PREVIEW] PNG resize: ({new_w},{new_h})")
                self.resize(new_w, new_h)
            self._update_sprite()

        self._reanchor_after_resize(
            before,
            before_live2d_canvas=before_canvas,
        )
        self._position_bubble()

    def _reanchor_after_resize(
        self,
        before: QRect,
        *,
        before_live2d_canvas: QRect | None = None,
    ):
        """按模型锚点或 PNG 底部中心重定位，并夹回屏幕可用区域。"""
        width = self.width()
        height = self.height()
        widget = getattr(self, "sprite_label", None)
        if (
            before_live2d_canvas is not None
            and getattr(self, "_use_live2d", False)
            and widget is not None
        ):
            live2d = (getattr(self, "config", {}) or {}).get("live2d") or {}
            anchor = normalize_live2d_placement_anchor(
                live2d.get("placement_anchor"),
                live2d.get("window_mask"),
            )
            target = calculate_live2d_anchor_preserving_position(
                before.topLeft(),
                before_live2d_canvas,
                QRect(widget.geometry()),
                anchor,
            )
        elif before.width() == width and before.height() == height:
            safe_print(f"[REANCHOR] 尺寸未变化 ({width}x{height})，跳过")
            return
        else:
            target = QPoint(
                before.center().x() - width // 2,
                before.bottom() + 1 - height,
            )
        area = available_geometry_for(before)
        if area is not None:
            target = clamp_position(target, QSize(width, height), area, margin=0)
        if target != QPoint(self.x(), self.y()):
            safe_print(f"[REANCHOR] move ({self.x()},{self.y()}) -> ({target.x()},{target.y()})")
            self.move(target)
        else:
            safe_print(f"[REANCHOR] 位置无需调整")

    def _open_size_dialog(self):
        dialog = SizeScaleDialog(self._size_factor, self)
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        area = available_geometry_for(pet_rect)
        if area is not None:
            dialog.move(
                calculate_centered_position(pet_rect, widget_size(dialog), area)
            )
        if dialog.exec_() == QDialog.Accepted:
            new_factor = normalize_pet_size_factor(dialog.get_value())
            self._size_factor = new_factor
            self.config.setdefault("display", {})["size_factor"] = new_factor
            self._save_config()

    def _position_bubble(self, *, animate: bool = False):
        stack = getattr(self, "_bubble_stack", None)
        if stack is not None:
            bubbles = tuple(
                bubble for bubble in stack.bubbles if bubble.isVisible()
            )
        else:
            bubble = getattr(self, "bubble", None)
            bubbles = (
                (bubble,)
                if bubble is not None and bubble.isVisible()
                else ()
            )
        if not bubbles:
            return

        pet_window_rect = QRect(
            self.x(),
            self.y(),
            self.width(),
            self.height(),
        )
        # 顶层窗口已经是稳定视觉视口，不再查询动态 mask。
        pet_rect = pet_window_rect
        screen = QApplication.screenAt(pet_rect.center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        avoid_rects = []
        chat_input = getattr(self, "_chat_input", None)
        try:
            if chat_input is not None and chat_input.isVisible():
                avoid_rects.append(QRect(chat_input.frameGeometry()))
        except RuntimeError:
            pass

        positions = calculate_bubble_stack_positions(
            pet_rect,
            tuple(bubble.size() for bubble in bubbles),
            screen.availableGeometry(),
            avoid_rects=tuple(avoid_rects),
        )
        opacities = calculate_bubble_stack_opacities(len(bubbles))
        for bubble, position, opacity in zip(bubbles, positions, opacities):
            bubble_rect = QRect(position, bubble.size())
            set_tail = getattr(bubble, "set_tail", None)
            if callable(set_tail):
                side, anchor = calculate_bubble_tail(pet_rect, bubble_rect)
                set_tail(side, anchor)
            animate_to = getattr(bubble, "animate_to", None)
            if callable(animate_to):
                animate_to(position, opacity, animate=animate)
            else:
                bubble.move(position)

    # ------------------------------------------------------------ 屏幕变化
    def _init_screen_guard(self):
        """监听分辨率 / 显示器变化，变化后把桌宠拉回可视范围。"""
        app = QApplication.instance()
        if app is None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._ensure_on_screen)
        self._screen_guard_timer = timer

        for signal_name in ("screenAdded", "screenRemoved", "primaryScreenChanged"):
            signal = getattr(app, signal_name, None)
            if signal is None:
                continue
            handler = (
                self._on_screen_added
                if signal_name == "screenAdded"
                else self._on_screen_layout_changed
            )
            try:
                signal.connect(handler)
            except (AttributeError, TypeError):
                pass
        for screen in app.screens():
            self._watch_screen(screen)

    def _watch_screen(self, screen):
        if screen is None:
            return
        for signal_name in (
            "geometryChanged",
            "availableGeometryChanged",
            "logicalDotsPerInchChanged",
        ):
            signal = getattr(screen, signal_name, None)
            if signal is None:
                continue
            try:
                signal.connect(self._on_screen_layout_changed)
            except (AttributeError, TypeError, RuntimeError):
                pass

    def _on_screen_added(self, screen):
        self._watch_screen(screen)
        self._on_screen_layout_changed()

    def _on_screen_layout_changed(self, *_args):
        """一次分辨率变更会连发多个信号，且窗口管理器还在重排，去抖后再处理。"""
        timer = getattr(self, "_screen_guard_timer", None)
        if timer is None:
            self._ensure_on_screen()
            return
        try:
            timer.start(SCREEN_GUARD_DEBOUNCE_MS)
        except RuntimeError:
            self._ensure_on_screen()

    def _ensure_on_screen(self):
        """桌宠露出得太少时拉回屏幕可用区域，并同步浮层位置。"""
        if getattr(self, "_dragging", False):
            return
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        bounds = screen_bounds_for_rect(pet_rect)
        if bounds is None:
            return
        geometry, available = bounds
        if not is_sufficiently_visible(
            pet_rect, geometry, ratio=PET_MIN_VISIBLE_RATIO
        ):
            position = clamp_position(
                pet_rect.topLeft(), pet_rect.size(), available, margin=0
            )
            if position != pet_rect.topLeft():
                self.move(position)
                safe_print(
                    f"[screen] 屏幕变化后回到可视范围: "
                    f"({pet_rect.x()},{pet_rect.y()}) -> "
                    f"({position.x()},{position.y()}) "
                    f"available=({available.x()},{available.y()},"
                    f"{available.width()}x{available.height()})"
                )
        self._ensure_overlays_on_screen()
        self._position_bubble()

    def _ensure_overlays_on_screen(self):
        """把仍然打开的浮层拉回屏幕：输入面板重新贴着桌宠，其余就地钳制。"""
        composer = getattr(self, "_chat_input", None)
        place_chat_input = getattr(self, "_place_chat_input", None)
        if composer is not None and callable(place_chat_input):
            try:
                if composer.isVisible():
                    place_chat_input()
            except RuntimeError:
                self._chat_input = None
        for name in (
            "_status_panel",
            "_menu_window",
            "_timeline_dialog",
            "_timeline_turn_dialog",
        ):
            window = getattr(self, name, None)
            if window is None:
                continue
            try:
                if window.isVisible():
                    move_within_screen(window, window.pos())
            except RuntimeError:
                setattr(self, name, None)

    def _save_pet_position(self) -> bool:
        """把当前桌宠左上角保存到独立本机状态文件。"""
        path = str(getattr(self, "_window_state_path", "") or "")
        if not path:
            return False
        return save_pet_position(path, self.x(), self.y())

    def _restore_pet_position(self) -> bool:
        """恢复桌宠位置；显示器变化后自动夹回可用区域。"""
        path = str(getattr(self, "_window_state_path", "") or "")
        if not path:
            return False
        saved = load_pet_position(path)
        if saved is None:
            return False
        position = QPoint(saved["x"], saved["y"])
        size = QSize(max(1, self.width()), max(1, self.height()))
        area = available_geometry_for(QRect(position, size))
        if area is not None:
            position = clamp_position(position, size, area, margin=0)
        self.move(position)
        safe_print(
            f"[place] restored pos=({position.x()},{position.y()}) "
            f"size={size.width()}x{size.height()}"
        )
        return True

    def _place_initial_position(self) -> None:
        """优先恢复上次位置，首次运行才放到主屏右下角。"""
        if not self._restore_pet_position():
            self._place_bottom_right()

    def _place_bottom_right(self):
        """放到主屏右下角，并钳制在可见区域内（防止多屏/DPI 导致"消失"）。"""
        screen = QApplication.primaryScreen().availableGeometry()
        w = max(self.width(), 80)
        h = max(self.height(), 80)
        x = screen.right() - w - 50
        y = screen.bottom() - h - 10
        x = max(screen.left(), min(x, screen.right() - max(80, w // 5)))
        y = max(screen.top(), min(y, screen.bottom() - max(80, h // 5)))
        self.move(x, y)
        safe_print(
            f"[place] screen=({screen.x()},{screen.y()},{screen.width()}x{screen.height()}) "
            f"-> pos=({x},{y}) size={w}x{h}"
        )

    def _toggle_standby(self):
        self._standby = not self._standby
        if self._standby:
            self._watcher_timer.stop()
            self._safe_set_expression("011")
            self._show_bubble(status_language.standby_on(), 0)
            self._position_bubble()
            self._set_standby_click_through(True)
        else:
            self._set_standby_click_through(False)
            self._safe_set_expression("001")
            clear_bubbles = getattr(self, "_clear_bubbles", None)
            if callable(clear_bubbles):
                clear_bubbles()
            elif hasattr(self, "bubble") and self.bubble:
                self.bubble.hide()
            self._show_bubble(
                status_language.standby_off(),
                bubble_duration_ms(self.config, "interaction"),
            )
            self._position_bubble()
            self._start_watcher_timer()
        refresh_tray = getattr(self, "_refresh_tray_state", None)
        if callable(refresh_tray):
            refresh_tray()

    def _set_standby_click_through(self, enabled: bool) -> None:
        """Enable/disable OS click-through + right-click poll + bubble passthrough."""
        if enabled:
            self._ensure_standby_click_through()
            return
        self._stop_standby_right_click_monitor()
        state = getattr(self, "_click_through_state", None)
        if state is not None:
            disable_click_through(state)
        self._click_through_state = ClickThroughState()
        self._set_bubbles_mouse_passthrough(False)
        if getattr(self, "_qt_transparent_for_input", False):
            try:
                self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            except Exception:
                pass
            self._qt_transparent_for_input = False

    def _ensure_standby_click_through(self) -> None:
        """(Re)apply click-through on current winId — safe to call after mode switch."""
        prev = getattr(self, "_click_through_state", None)
        if prev is not None and prev.active:
            disable_click_through(prev)

        try:
            hwnd = int(self.winId())
        except Exception:
            hwnd = 0
        width = max(0, int(self.width()))
        height = max(0, int(self.height()))
        state = enable_click_through(hwnd, width=width, height=height)
        self._click_through_state = state
        if not state.active:
            safe_print(
                "[click_through] native pass-through inactive; "
                "standby still suppresses pet interactions"
            )
        self._set_bubbles_mouse_passthrough(True)
        self._start_standby_right_click_monitor()

    def _start_standby_right_click_monitor(self) -> None:
        timer = getattr(self, "_standby_rc_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(50)
            timer.timeout.connect(self._poll_standby_right_click)
            self._standby_rc_timer = timer
        detector = getattr(self, "_standby_rc_detector", None)
        if detector is None:
            detector = RightClickEdgeDetector()
            self._standby_rc_detector = detector
        else:
            detector.reset()
        if not timer.isActive():
            timer.start()

    def _stop_standby_right_click_monitor(self) -> None:
        timer = getattr(self, "_standby_rc_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        detector = getattr(self, "_standby_rc_detector", None)
        if detector is not None:
            detector.reset()

    def _poll_standby_right_click(self) -> None:
        if not getattr(self, "_standby", False):
            return
        if getattr(self, "_standby_menu_open", False):
            return
        if not self.isVisible():
            return
        try:
            from PyQt5.QtGui import QCursor

            global_pos = QCursor.pos()
            geo = self.frameGeometry()
            cursor_in_pet = geo.contains(global_pos)
            button_down = is_right_button_down()
            detector = getattr(self, "_standby_rc_detector", None)
            if detector is None:
                detector = RightClickEdgeDetector()
                self._standby_rc_detector = detector
            if detector.update(cursor_in_pet=cursor_in_pet, button_down=button_down):
                local = self.mapFromGlobal(global_pos)
                self._open_standby_context_menu(local)
        except Exception as exc:
            safe_print(f"[click_through] right-click poll error: {exc}")

    def _open_standby_context_menu(self, local_pos) -> None:
        """Show context menu while standby: temporarily accept mouse input."""
        if getattr(self, "_standby_menu_open", False):
            return
        self._standby_menu_open = True
        detector = getattr(self, "_standby_rc_detector", None)
        if detector is not None:
            detector.was_down = True
        self._set_standby_click_through(False)
        try:
            show_menu = getattr(self, "_show_context_menu", None)
            if callable(show_menu):
                show_menu(local_pos)
        finally:
            self._standby_menu_open = False
            if getattr(self, "_standby", False):
                self._set_standby_click_through(True)

    def _set_bubbles_mouse_passthrough(self, enabled: bool) -> None:
        stack = getattr(self, "_bubble_stack", None)
        bubbles = []
        if stack is not None:
            bubbles = list(getattr(stack, "bubbles", ()) or ())
        else:
            bubble = getattr(self, "bubble", None)
            if bubble is not None:
                bubbles = [bubble]
        for bubble in bubbles:
            setter = getattr(bubble, "set_mouse_passthrough", None)
            if callable(setter):
                try:
                    setter(bool(enabled))
                except Exception:
                    pass

    def _toggle_render_mode(self):
        # 测量在飞时切模式：被删的是它正在量那只 widget，读数与 `_pending_mount` 那份矩形
        # 会落到切换后的新画布上。先作废这一发（没有待挂时它就是 no-op）。
        self._cancel_premount_measure()
        if self._use_live2d:
            self._cancel_live2d_startup_timeout()
            if self.sprite_label:
                self.sprite_label.shutdown()
                self.sprite_label.hide()
                self.sprite_label.deleteLater()
                self.sprite_label = None
            self._l2d_model = None
            self._use_live2d = False
            self._l2d_pending = False
            self._live2d_startup_widget = None
            self._init_png_renderer()
            self._show_bubble(
                status_language.render_png_enabled(),
                bubble_duration_ms(self.config, "interaction"),
            )
            self.config.setdefault("live2d", {})["enabled"] = False
            self._save_config()
            if getattr(self, "_standby", False):
                self._ensure_standby_click_through()
        else:
            if self.renderer:
                self.renderer.stop_blink_animation()
                self.renderer = None
            if self.sprite_label:
                self.sprite_label.hide()
                self.sprite_label.deleteLater()
                self.sprite_label = None
            self.renderer = None
            self.setWindowOpacity(1.0)
            self._renderer_ready = False
            self.config.setdefault("live2d", {})["enabled"] = True
            self._save_config()
            try:
                self._start_live2d_renderer()
                self._place_initial_position()
            except Exception as exc:
                self._fallback_to_png(str(exc))

            def announce_mode_change():
                if self._use_live2d:
                    self._show_bubble(
                        status_language.render_live2d_enabled(),
                        bubble_duration_ms(self.config, "interaction"),
                    )
                else:
                    self._show_bubble(
                        status_language.render_live2d_failed(),
                        bubble_duration_ms(self.config, "default"),
                    )
                if getattr(self, "_standby", False):
                    self._ensure_standby_click_through()

            self.when_renderer_ready(announce_mode_change)

    def closeEvent(self, event):
        """取消未完成的启动回调，避免关闭后被超时回退重新显示。"""
        self._cancel_live2d_startup_timeout()
        super().closeEvent(event)
