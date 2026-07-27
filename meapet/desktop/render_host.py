"""PNG / Live2D render host: switch modes, size, hit region, standby."""
from __future__ import annotations

import os
from collections.abc import Callable

from PyQt5.QtWidgets import QApplication, QDialog
from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt5.QtGui import QRegion

from meapet.desktop.renderer import SpriteCanvas, SpriteRenderer
from meapet.config.store import (
    DEFAULT_LIVE2D_WINDOW_MASK,
    normalize_live2d_window_mask,
)
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
from meapet.desktop.window_shape import (
    apply_ellipse_window_shape,
    clear_window_shape,
)
from meapet.ui_theme import normalize_pet_size_factor
from meapet.utils import safe_print


BUBBLE_SCREEN_MARGIN = 24
BUBBLE_PET_GAP = 12
BUBBLE_STACK_GAP = 8
BUBBLE_HEAD_ANCHOR_RATIO = 0.16
BUBBLE_TAIL_CORNER_INSET = 36
LIVE2D_STARTUP_TIMEOUT_MS = 5000
# 一次分辨率变更会连发多个屏幕信号，合并后再校正位置。
SCREEN_GUARD_DEBOUNCE_MS = 400
# 桌宠宽高各至少露出这么多比例才算「还看得见」，否则拉回可用区域。
PET_MIN_VISIBLE_RATIO = 0.5


def calculate_drag_position(
    window_origin: QPoint,
    pointer_origin: QPoint,
    current_pointer: QPoint,
) -> QPoint:
    """根据一次按下时的固定全局锚点计算窗口位置，避免增量累计漂移。"""
    return window_origin + current_pointer - pointer_origin


def calculate_bubble_anchor_rect(
    pet_window_rect: QRect,
    visible_local_rect: QRect | None = None,
) -> QRect:
    """把桌宠窗口内的可见区域转换成用于气泡定位的全局矩形。

    Live2D 宿主窗口通常包含大块透明留白；若仍以完整窗口为锚点，代码
    计算出的 12px 间距会变成“透明留白 + 12px”。Qt mask 与实际窗口
    命中区域共用，因此它的包围盒是最稳定的视觉锚点。
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
    # 桌宠在屏幕右半侧时气泡优先放左边；在左半侧时优先放右边。
    # 上下方仅作为两侧空间不足或被其他浮层占用时的回退位置。
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

    # 候选点不完整可见时先钳制，再找一个没有碰撞的位置。
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

    # 首选锚点被聊天框等浮层占用时，优先水平让开，保持气泡仍在角色上部。
    # 如果水平空间不足，再尝试沿垂直方向避让。
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
    """按“最旧到最新”返回气泡位置，最新靠近角色、旧消息向上堆叠。"""
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

    # 极窄屏幕或额外浮层占满两侧时，沿用单气泡的安全回退方向。
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


def ellipse_mask_region(
    width: int,
    height: int,
    params: dict | None = None,
) -> QRegion:
    """按窗口像素尺寸与归一化参数生成椭圆 mask（纯函数，便于单测）。"""
    mask = normalize_live2d_window_mask(params or DEFAULT_LIVE2D_WINDOW_MASK)
    w = max(1, int(width))
    h = max(1, int(height))
    cx_px = int(round(float(mask["cx"]) * w))
    cy_px = int(round(float(mask["cy"]) * h))
    rw_px = max(1, int(round(float(mask["rw"]) * w)))
    rh_px = max(1, int(round(float(mask["rh"]) * h)))
    return QRegion(
        cx_px - rw_px,
        cy_px - rh_px,
        rw_px * 2,
        rh_px * 2,
        QRegion.Ellipse,
    )


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
        force_png = os.environ.get("MEAPET_FORCE_PNG", "").strip().lower()
        live2d_requested = (
            force_png not in ("1", "true", "yes")
            and l2d_cfg.get("enabled", False)
            and bool(model_dir)
            and os.path.isdir(model_dir)
        )

        if live2d_requested:
            # 保持顶层窗口正常映射；背景和未完成 framebuffer 本身透明。
            # Windows 的 QOpenGLWidget 不应以 0 opacity 首次映射，否则可能
            # 永久丢失 DWM 合成表面。
            self.setWindowOpacity(1.0)
            try:
                self._start_live2d_renderer()
                return
            except Exception as exc:
                safe_print(f"[pet] Live2D 初始化失败，使用 PNG: {exc}")
                self._fallback_to_png(str(exc))
                return

        if force_png in ("1", "true", "yes"):
            safe_print("[toggle] MEAPET_FORCE_PNG=1, skip Live2D")
        elif l2d_cfg.get("enabled", False) and not (model_dir and os.path.isdir(model_dir)):
            safe_print(f"[live2d] 模型目录不存在，使用 PNG: {model_dir}")

        self._init_png_renderer()
        self.setWindowOpacity(1.0)
        self._mark_renderer_ready()

    def _init_png_renderer(self):
        """创建 PNG 渲染器；仅用于明确选择 PNG 或 Live2D 失败回退。"""
        char = self.config.get("character", {})
        sprite_dir = self.config.get(
            "sprite_dir",
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sprites"),
        )
        # Prefer project sprites via config default; fall back to PROJECT_ROOT
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

        self._clear_window_region()
        init_live2d()
        self._use_live2d = True
        self._l2d_pending = True
        self._renderer_ready = False
        self._init_live2d()
        widget = self.sprite_label
        if widget is None:
            raise RuntimeError("Live2D widget not created")
        self._live2d_startup_widget = widget
        self._ensure_live2d_startup_timer().start(
            LIVE2D_STARTUP_TIMEOUT_MS
        )

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

    def _on_live2d_first_frame(self):
        """首帧已经绘制并提交后，仅做一次显现，不再改变尺寸或位置。"""
        if not self._l2d_pending or not self._use_live2d:
            return
        self._cancel_live2d_startup_timeout()
        self._l2d_pending = False
        self._live2d_startup_widget = None
        try:
            self._apply_hit_region()
        except Exception as exc:
            safe_print(f"[live2d] hit region skipped: {exc}")
        self._reveal_live2d_window()
        self._mark_renderer_ready()
        safe_print(
            f"[pet] Live2D 首帧就绪 size={self.width()}x{self.height()} "
            f"pos=({self.x()},{self.y()})"
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
            self._place_bottom_right()
            self._apply_hit_region()
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
        w0, h0 = self._scaled_live2d_size(self._size_factor)
        widget.move(0, 0)
        widget.resize(w0, h0)
        self.resize(w0, h0)
        widget.show()
        safe_print(f"[live2d] 控件已创建，等待首帧: {w0}x{h0}")

    def _safe_renderer(self):
        if self._use_live2d and self._l2d_model:
            return self._l2d_model
        return self.renderer

    def _live2d_base_size(self) -> tuple[int, int]:
        model = getattr(self, "_l2d_model", None)
        if model is not None:
            try:
                width, height = model.get_suggested_size()
                width = int(width)
                height = int(height)
                if width > 0 and height > 0:
                    return width, height
            except (AttributeError, TypeError, ValueError):
                pass
        return 525, 735

    def _scaled_live2d_size(self, factor: float) -> tuple[int, int]:
        base_w, base_h = self._live2d_base_size()
        return (
            max(80, round(base_w * factor)),
            max(80, round(base_h * factor)),
        )

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
        new_factor = normalize_pet_size_factor(factor)
        self._size_factor_preview(new_factor)
        self.config.setdefault("display", {})["size_factor"] = new_factor
        self._save_config()
        show = getattr(self, "_show_bubble", None)
        if callable(show):
            show(status_language.window_size_applied(new_factor), 2500, mood=None)

    def _apply_display_preference(self):
        """把 `display.size_factor` 应用到当前窗口（配置页保存后立即生效）。"""
        display = (getattr(self, "config", {}) or {}).get("display") or {}
        factor = normalize_pet_size_factor(display.get("size_factor", 1.0))
        if abs(factor - float(getattr(self, "_size_factor", 1.0))) < 1e-3:
            return
        self._size_factor_preview(factor)

    def _size_factor_preview(self, factor: float):
        factor = normalize_pet_size_factor(factor)
        before = QRect(self.x(), self.y(), self.width(), self.height())
        self._size_factor = factor
        if self._use_live2d and self.sprite_label:
            self._clear_window_region()
            new_w, new_h = self._scaled_live2d_size(factor)
            self.sprite_label.resize(new_w, new_h)
            self.resize(new_w, new_h)
            self._apply_hit_region()
            QApplication.processEvents()
        else:
            if self.renderer is None:
                return
            pixmap = self.renderer.get_current_pixmap()
            if not pixmap.isNull():
                new_w = max(80, int(pixmap.width() * self._scale * factor))
                new_h = max(80, int(pixmap.height() * self._scale * factor))
                self.resize(new_w, new_h)
            self._update_sprite()
            self._apply_hit_region()
            QApplication.processEvents()
        self._reanchor_after_resize(before)
        self._position_bubble()

    def _reanchor_after_resize(self, before: QRect):
        """缩放以「底部中心」为锚点，并保证新尺寸仍落在屏幕可用区域内。

        直接 resize 会固定左上角，桌宠放大时会往右下角长出去（贴边时直接出屏）；
        以脚下为锚点更符合「桌宠站在桌面上」的直觉。
        """
        width = self.width()
        height = self.height()
        if before.width() == width and before.height() == height:
            return
        target = QPoint(
            before.center().x() - width // 2,
            before.bottom() + 1 - height,
        )
        area = available_geometry_for(before)
        if area is not None:
            target = clamp_position(target, QSize(width, height), area, margin=0)
        if target != QPoint(self.x(), self.y()):
            self.move(target)

    def _open_size_dialog(self):
        dialog = SizeScaleDialog(self._size_factor, self)
        # 以桌宠所在屏幕（而非主屏）为准，多显示器下才不会弹到另一块屏外面。
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

    def _apply_hit_region(self):
        """Live2D 椭圆窗口外形 + 命中；PNG 始终清空。

        分层：
        1. 原生窗形（ctypes SetWindowRgn / XShape）—— 去掉透明矩形碰撞箱
        2. Qt setMask（宿主 + 子）—— 命中一致
        3. Live2D paintGL stencil —— OpenGL 内容椭圆裁剪
        不依赖 pywin32。
        """
        use_live2d = bool(getattr(self, "_use_live2d", False))
        params = self._live2d_window_mask_params()
        if not use_live2d or not params.get("enabled", True):
            self._clear_window_region()
            return

        try:
            dpr = float(self.devicePixelRatioF())
        except Exception:
            dpr = float(self.devicePixelRatio() or 1.0) if hasattr(self, "devicePixelRatio") else 1.0
        if dpr <= 0:
            dpr = 1.0

        # 1) OS 椭圆外形（打包后去掉透明矩形窗）
        try:
            hwnd = int(self.winId())
            apply_ellipse_window_shape(
                hwnd,
                self.width(),
                self.height(),
                params,
                dpr=dpr,
            )
        except Exception as e:
            safe_print(f"[WARN] OS ellipse window shape failed: {e}")

        # 2) Qt mask：命中 + 部分平台辅助
        region = ellipse_mask_region(self.width(), self.height(), params)
        self.setMask(region)
        widget = getattr(self, "sprite_label", None)
        if widget is not None:
            try:
                widget.setMask(
                    ellipse_mask_region(widget.width(), widget.height(), params)
                )
            except Exception as e:
                safe_print(f"[WARN] Live2D child mask failed: {e}")

    def _live2d_window_mask_params(self) -> dict:
        live2d = (getattr(self, "config", {}) or {}).get("live2d") or {}
        return normalize_live2d_window_mask(live2d.get("window_mask"))

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
        visible_local_rect = None
        try:
            visible_region = self.mask()
            if not visible_region.isEmpty():
                visible_local_rect = visible_region.boundingRect()
        except RuntimeError:
            pass
        pet_rect = calculate_bubble_anchor_rect(
            pet_window_rect,
            visible_local_rect,
        )
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
        """监听分辨率 / 显示器变化，变化后把桌宠拉回可视范围。

        改分辨率（尤其是调小）后，桌宠原来的坐标可能整块落在新桌面之外；
        窗口是无边框置顶的 Tool 窗，没有任务栏入口，用户会以为它「消失」了。
        """
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
            # 拖动中不抢用户的手，松手后的下一次屏幕事件再处理。
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
                # 窗口已被销毁，清掉悬空引用。
                setattr(self, name, None)

    def _place_bottom_right(self):
        """放到主屏右下角，并钳制在可见区域内（防止多屏/DPI 导致“消失”）。"""
        screen = QApplication.primaryScreen().availableGeometry()
        w = max(self.width(), 80)
        h = max(self.height(), 80)
        x = screen.right() - w - 50
        y = screen.bottom() - h - 10
        # 钳制：至少 80% 窗口在主屏内
        x = max(screen.left(), min(x, screen.right() - max(80, w // 5)))
        y = max(screen.top(), min(y, screen.bottom() - max(80, h // 5)))
        self.move(x, y)
        safe_print(
            f"[place] screen=({screen.x()},{screen.y()},{screen.width()}x{screen.height()}) "
            f"-> pos=({x},{y}) size={w}x{h}"
        )

    def _clear_window_region(self):
        """移除 Qt mask 与原生椭圆窗形，恢复全矩形客户区。"""
        self.clearMask()
        widget = getattr(self, "sprite_label", None)
        if widget is not None:
            try:
                widget.clearMask()
            except Exception:
                pass
        try:
            hwnd = int(self.winId())
            clear_window_shape(
                hwnd,
                width=max(1, self.width()),
                height=max(1, self.height()),
            )
        except Exception as e:
            safe_print(f"[WARN] OS window shape clear failed: {e}")

    def _toggle_standby(self):
        self._standby = not self._standby
        if self._standby:
            self._watcher_timer.stop()
            self._safe_set_expression("011")
            self._show_bubble(status_language.standby_on(), 0)
            self._position_bubble()
            self._apply_hit_region()
            self._set_standby_click_through(True)
        else:
            # 先关穿透，避免离开过程中菜单/气泡仍被忽略输入。
            self._set_standby_click_through(False)
            self._safe_set_expression("001")
            clear_bubbles = getattr(self, "_clear_bubbles", None)
            if callable(clear_bubbles):
                clear_bubbles()
            elif hasattr(self, "bubble") and self.bubble:
                self.bubble.hide()
            self._show_bubble(status_language.standby_off(), 2500)
            self._position_bubble()
            self._apply_hit_region()
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
        # Drop Qt flag fallback if it was used.
        if getattr(self, "_qt_transparent_for_input", False):
            try:
                self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            except Exception:
                pass
            self._qt_transparent_for_input = False

    def _ensure_standby_click_through(self) -> None:
        """(Re)apply click-through on current winId — safe to call after mode switch."""
        # Tear down any previous native state first (HWND may have changed).
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
            # Best-effort: still block Qt-level mouse on this widget tree when
            # native pass-through is unavailable (e.g. Wayland). Does NOT pass
            # clicks to windows below — only prevents pet interactions.
            try:
                # Do not set WA_TransparentForMouseEvents on the top-level pet:
                # that would also kill the out-of-band right-click path's ability
                # to show a menu after temporarily disabling native pass-through.
                # Guards in mouse handlers cover interaction suppression.
                pass
            except Exception:
                pass
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
        # Pause edge detector so the physical RMB hold doesn't re-fire.
        detector = getattr(self, "_standby_rc_detector", None)
        if detector is not None:
            detector.was_down = True
        # Temporarily disable native pass-through so the menu can take the click.
        self._set_standby_click_through(False)
        try:
            show_menu = getattr(self, "_show_context_menu", None)
            if callable(show_menu):
                show_menu(local_pos)
        finally:
            self._standby_menu_open = False
            # 菜单现在是独立顶层窗口（非阻塞 popup），本体可以立即恢复穿透，
            # 菜单窗口自身不穿透，依然可以点击和拖动。
            # Only re-enable if still in standby (user may have left via menu).
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
        self._clear_window_region()
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
            self._apply_hit_region()
            self._show_bubble("已切回 PNG 立绘喵", 2500)
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
            # 先写 config，异常退出后下次仍会尝试用户明确选择的 Live2D。
            self.config.setdefault("live2d", {})["enabled"] = True
            self._save_config()
            try:
                self._start_live2d_renderer()
                # 在透明阶段确定最终位置；首帧回调只负责显现。
                self._place_bottom_right()
            except Exception as exc:
                self._fallback_to_png(str(exc))

            def announce_mode_change():
                if self._use_live2d:
                    self._show_bubble("已切换到 Live2D 喵", 2500)
                else:
                    self._show_bubble("Live2D 加载失败，已切回 PNG 喵", 3000)
                if getattr(self, "_standby", False):
                    self._ensure_standby_click_through()

            self.when_renderer_ready(announce_mode_change)

    def closeEvent(self, event):
        """取消未完成的启动回调，避免关闭后被超时回退重新显示。"""
        self._cancel_live2d_startup_timeout()
        super().closeEvent(event)
