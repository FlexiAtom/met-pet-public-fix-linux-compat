"""
梅尔桌宠 - Live2D 渲染模块
基于 live2d-py (Cubism 3+) + QOpenGLWidget 透明窗口
"""
from __future__ import annotations

import os
import sys
import math
import time
import traceback

from PyQt5.QtWidgets import QApplication, QOpenGLWidget
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from meapet.live2d_api import (
    LIVE2D_AVAILABLE,
    LIVE2D_UNAVAILABLE_REASON,
    live2d,
)

_LIVE2D_INITIALIZED = False
from PyQt5.QtCore import QEvent
from PyQt5.QtGui import QImage, QSurfaceFormat

from meapet.desktop.render_host import calculate_drag_position
from meapet.log import get_color_logger

log = get_color_logger("live2d_widget")

#: 进分区判定那两条门票（单位 px / 秒）。名字与数值都放在**这里**而不是内联在 `mouseReleaseEvent`
#: 里，是因为 `meapet --doctor` 要把它们和 Qt 自己的 `startDragDistance()`/`doubleClickInterval()`
#: 并排印出来——「点一下为什么没反应」判的就是这两组数谁先立起（拖窗标志的门槛通常远小于 35 px）。
#: doctor 读这两个名字，不在它那边重抄一遍数字：抄了就是第二套真值，改一处漏一处。
ZONE_CLICK_MAX_DISTANCE_PX = 35
ZONE_CLICK_MAX_HOLD_SECONDS = 0.4

#: 画布归一的参照长边（px）。产品拿 `get_suggested_size()` 的产物去算 viewport 几何，
#: 而 live2d-py 的 `GetCanvasSize()` 返回的是**模型单位**——本机 mea_live2d 实测
#: `(1.7241379, 2.4137931)`，`GetPixelsPerUnit()=2900.0`，`GetCanvasSizePixel()=(5000.0, 7000.0)`。
#: 直接 `int()` 就是 `(1, 2)`，被 `MIN_CANVAS_SIZE` 判死 ⇒ 画布从来没被读到过，每一步几何
#: 都落在硬编码方画布上（人工 2026-10-15「PNG切换到Live2D需要重新请求窗口大小」的根因）。
#: 参照边取 1024 是为了与那份被替换掉的硬编码值**同高**：`model.Resize(w, h)` 只按高缩放
#: （实测 widget 366x512 与 512x512 画出的立绘同高 353 px），所以高不变＝屏上人物一寸不变，
#: 换掉的只是那个假方形-vs-真 5:7 的宽高比。
CANVAS_REFERENCE_EDGE = 1024

#: 模型还没加载时的占位画布（`.moc3` 的画布尺寸要到 `initializeGL` 里加载完才读得到）。
#: 525x735 与本机那份真画布的 5:7 同形，所以首帧前后只是等比放大，不改变形状。
PLACEHOLDER_CANVAS = (525, 735)


def normalize_canvas_to_reference_edge(w: float, h: float,
                                       edge: int = CANVAS_REFERENCE_EDGE) -> tuple[int, int]:
    """把画布按**长边**缩到参照边长，宽高比原样带走。"""
    if w <= 0 or h <= 0:
        return (edge, edge)
    scale = edge / max(w, h)
    return (max(1, round(w * scale)), max(1, round(h * scale)))


class Live2DModel:
    """Live2D 模型控制器，提供与 SpriteRenderer 兼容的接口"""

    def __init__(self, model_dir: str):
        """
        model_dir: 包含 .model3.json 的目录
        """
        self.model_dir = model_dir
        self.model = None
        self.widget = None  # Live2DWidget 引用
        self._loaded = False
        self._current_expression = "001"  # 兼容接口

        # 找 model3.json
        self._model_json = None
        for f in os.listdir(model_dir):
            if f.endswith('.model3.json') or f.endswith('.model.json'):
                self._model_json = os.path.join(model_dir, f)
                break
        if not self._model_json:
            raise FileNotFoundError(f"在 {model_dir} 中找不到 .model3.json")

        self._name = os.path.splitext(os.path.basename(self._model_json))[0]

    def create_widget(self, parent=None):
        """创建并返回 Live2DWidget"""
        self.widget = Live2DWidget(self, parent)
        return self.widget

    def get_model(self):
        """原生模型对象（live2d-py 1.0 的 `live2d.Model` 实例）。"""
        return self.model

    def get_suggested_size(self) -> tuple:
        """建议显示尺寸（模型加载后）：真画布**像素**尺寸归一到参照长边。

        读的是 `GetCanvasSizePixel()`；`GetCanvasSize()` 那一对是模型单位，拿它当像素用会读出
        (1, 2)（见 `CANVAS_REFERENCE_EDGE` 上那段）。读不到像素口径就退回占位值，让调用方
        照旧走配置/硬编码那两层回退。
        """
        if self.model is None:
            return PLACEHOLDER_CANVAS
        try:
            canvas_w, canvas_h = self.model.GetCanvasSizePixel()
        except Exception as exc:
            print(f"[live2d] ✗ GetCanvasSizePixel 读不到画布: {type(exc).__name__}: {exc}",
                  flush=True)
            return PLACEHOLDER_CANVAS
        return normalize_canvas_to_reference_edge(canvas_w, canvas_h)

    # ====== 兼容 SpriteRenderer 的接口 ======

    def set_mood(self, mood: str):
        """设置情绪表情"""
        self._current_expression = mood
        if self.model:
            # 根据 mood 播放对应 motion
            if mood in ("happy", "curious"):
                # 眯眼 motion
                self.model.StartMotion("Idle", 0, live2d.MotionPriority.NORMAL)
            elif mood in ("annoyed", "angry"):
                # 生气 motion
                self.model.StartMotion("Angry", 0, live2d.MotionPriority.NORMAL)
            elif mood == "sad" or mood == "melancholy":
                # 可扩展，暂无对应 motion
                pass

    def set_expression(self, expr: str):
        """设置差分表情（兼容接口）"""
        self._current_expression = expr
        # Live2D 没有差分表情，映射到 mood
        if expr == "011" or expr == "012":
            # 闭眼 → 保持当前，不额外动作
            pass
        elif expr == "001":
            # 默认睁眼
            pass

    def start_blink_animation(self):
        """眨眼由 Live2D SDK 自动处理，这里不需要做任何事"""
        pass

    def stop_blink_animation(self):
        pass

    def expression_changed(self):
        """无操作（Live2D 是连续的）"""
        pass

    def get_current_expression(self) -> str:
        return self._current_expression

    def get_current_pixmap(self):
        """无操作"""
        return None

    def set_size(self, width: int, height: int):
        """按目标尺寸等比缩放模型"""
        if self.model:
            scale_w = width / self.model.GetCanvasSize()[0]
            scale_h = height / self.model.GetCanvasSize()[1]
            scale = min(scale_w, scale_h)
            self.model.SetScale(scale)


class Live2DWidget(QOpenGLWidget):
    """透明 Live2D 渲染窗口"""

    # 信号：触摸分区（上半 / 左下 / 右下）
    head_patted = pyqtSignal()
    lower_left_patted = pyqtSignal()
    lower_right_patted = pyqtSignal()
    chat_requested = pyqtSignal()
    first_frame_ready = pyqtSignal()
    initialization_failed = pyqtSignal(str)

    def __init__(self, l2d_model: Live2DModel, parent=None):
        super().__init__(parent)

        # 必须在初始化 QOpenGLWidget 之前设置
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)       # 分配 8 位 Alpha 通道
        fmt.setRenderableType(QSurfaceFormat.OpenGL)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)
        self.setFormat(fmt)

        self.l2d = l2d_model

        # Qt 自身的透明设置
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True)
        self.setStyleSheet("background: transparent; border: none;")

        # 鼠标追踪
        self.setMouseTracking(True)
        self._ready = False
        self._initialization_error = ""
        self._frame_drawn = False
        self._first_frame_emitted = False
        self._drag_target = (0.0, 0.0)
        self._global_filter_installed = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self.frameSwapped.connect(self._on_frame_swapped)

        # 鼠标位置与拖拽
        self._mouse_x = 0
        self._mouse_y = 0
        self._press_pos = None
        self._press_time = 0.0
        self._dragging_window = False
        self._drag_pointer_origin = None
        self._drag_window_origin = None
        # 单击/双击竞态：分区信号延到 doubleClickInterval 之后再发（动作仍即时）。
        # Qt 的双击序列是 press→release→DblClick→release，第一次 release 排出去的那一发
        # 必须能在 DblClick 到达时撤回，否则点开会聊天窗的同时又挨一次摸头反馈。
        self._zone_click_timer = QTimer(self)
        self._zone_click_timer.setSingleShot(True)
        self._zone_click_timer.timeout.connect(self._on_zone_click_timeout)
        self._pending_zone_signal = None
        # Phase 3: 离屏渲染（穿透模式下窗口隐藏，paintGL 不再被调用）
        self._fbo = None
        self._fbo_size = (0, 0)
        self._proxy_rect = None  # 由 render_host 注入的真实屏幕矩形

        # 初始大小：优先使用模型画布大小
        init_w, init_h = self.l2d.get_suggested_size()
        self.resize(init_w, init_h)

        self.installEventFilter(self)

    def _parent_in_standby(self) -> bool:
        parent = self.parentWidget()
        return bool(parent is not None and getattr(parent, "_standby", False))

    def eventFilter(self, obj, event):
        # 不再做椭圆软过滤；仅保留待机吞事件逻辑。
        if obj == self and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.RightButton:
                return False
            if self._parent_in_standby():
                return True
        return super().eventFilter(obj, event)

    def initializeGL(self):
        try:
            live2d.glInit()

            from OpenGL.GL import (
                GL_BLEND,
                GL_ONE_MINUS_SRC_ALPHA,
                GL_SRC_ALPHA,
                glBlendFunc,
                glClearColor,
                glEnable,
            )

            # OpenGL context 出现后的第一条颜色状态就是全透明
            glClearColor(0.0, 0.0, 0.0, 0.0)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

            model = live2d.Model()
            model.LoadModelJson(self.l2d._model_json)
            model.SetAutoBlink(True)
            model.SetAutoBreath(True)
            self._fit_model_to_window(model)
            self.l2d.model = model
            self.l2d._loaded = True
            self._ready = True
            self._timer.start(16)
        except Exception as exc:
            self._report_initialization_failure(exc)

    def _report_initialization_failure(self, exc: Exception):
        """把 Qt/OpenGL 初始化异常转换成一次可恢复的宿主信号。"""
        self._ready = False
        if self._initialization_error:
            return
        self._initialization_error = f"{type(exc).__name__}: {exc}"
        log.error(f"[live2d] OpenGL 初始化失败: {self._initialization_error}")
        log.debug(traceback.format_exc())
        QTimer.singleShot(
            0,
            lambda reason=self._initialization_error: self.initialization_failed.emit(
                reason
            ),
        )

    def _fit_model_to_window(self, model):
        """把完整模型画布适配到 OpenGL 子控件；父窗口负责矩形视口裁剪。"""
        model.Resize(self.width(), self.height())

    def resizeGL(self, w, h):
        try:
            from OpenGL.GL import glViewport
        except Exception as exc:
            self._report_initialization_failure(exc)
            return
        dpr = self.devicePixelRatio()
        glViewport(0, 0, int(w * dpr), int(h * dpr))
        if self.l2d.model and w > 0 and h > 0:
            self._fit_model_to_window(self.l2d.model)

    def paintGL(self):
        try:
            from OpenGL.GL import (
                GL_COLOR_BUFFER_BIT,
                GL_DEPTH_BUFFER_BIT,
                glClear,
                glClearColor,
            )
        except Exception as exc:
            self._report_initialization_failure(exc)
            return

        # 清屏为全透明
        glClearColor(0.0, 0.0, 0.0, 0.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        if not self._ready or not self.l2d.model:
            if not hasattr(self, '_dbg_skip'):
                self._dbg_skip = 0
            self._dbg_skip += 1
            if self._dbg_skip <= 3:
                log.debug(f"[paint] SKIP _ready={self._ready} model={self.l2d.model is not None}")
            return

        if not hasattr(self, '_dbg_frame'):
            self._dbg_frame = 0
        self._dbg_frame += 1
        if self._dbg_frame % 2400 == 0:
            log.debug(f"[paint] frame={self._dbg_frame} alive")

        live2d.clearBuffer()
        self._draw_model()
        self._frame_drawn = True

    # ====== Phase 3: 离屏渲染 ======

    def _update_look_target(self):
        """用光标全局坐标驱动眼球 / 身体追踪。

        穿透模式下 Qt 窗口被隐藏，mapToGlobal 失效，
        因此优先使用 render_host 注入的 _proxy_rect（真实显示位置）。
        """
        if self._dragging_window or not self.l2d.model:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        from PyQt5.QtGui import QCursor
        gp = QCursor.pos()
        proxy = getattr(self, "_proxy_rect", None)
        if proxy is not None:
            wp = proxy.topLeft()
        else:
            wp = self.mapToGlobal(self.rect().topLeft())

        cx = (gp.x() - wp.x() - w / 2) / (w / 2)
        cy = (gp.y() - wp.y() - h / 2) / (h / 2)
        cx = max(-1.0, min(1.0, cx))
        cy = max(-1.0, min(1.0, cy))
        # 写参数走 1.0 那一代的 `SetParamById`（0.7 的 `SetParameterValue` 按下标取参，传 id 会
        # TypeError；1.0.0 已删掉旧名，见 meapet/live2d_api.py）。
        model = self.l2d.model
        model.SetParamById("ParamAngleX", cx * 30, 1.0)
        model.SetParamById("ParamAngleY", -cy * 30, 1.0)
        model.SetParamById("ParamBodyAngleZ", cx * 10, 1.0)
        model.SetParamById("ParamAngleZ", cx * 10, 1.0)

    def _draw_model(self):
        """绘制模型当前状态，供 paintGL 与离屏渲染共用。"""
        self._update_look_target()
        self.l2d.model.Update()
        self.l2d.model.Draw()

    def _ensure_fbo(self, w: int, h: int) -> bool:
        """确保离屏 FBO 存在且尺寸匹配。"""
        if w <= 0 or h <= 0:
            return False
        if self._fbo is not None and self._fbo_size == (w, h):
            return True
        try:
            from PyQt5.QtGui import (
                QOpenGLFramebufferObject,
                QOpenGLFramebufferObjectFormat,
            )
        except Exception:
            return False
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.CombinedDepthStencil)
        self._fbo = QOpenGLFramebufferObject(w, h, fmt)
        self._fbo_size = (w, h)
        return True

    def render_offscreen(self):
        """离屏渲染模型并返回 QImage；不依赖窗口可见性。

        穿透模式下 Qt 顶层窗口被 hide()，Qt 不再派发 paintGL，
        必须靠这里主动 makeCurrent + 渲染到 FBO + glReadPixels 取帧。
        """
        if not self._ready or not self.l2d.model:
            print("[live2d] ✗ 离屏: 未就绪或无模型", flush=True)
            return None
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            print(f"[live2d] ✗ 离屏: 尺寸非法 {w}x{h}", flush=True)
            return None
        try:
            from OpenGL.GL import (
                GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_RGBA,
                GL_UNSIGNED_BYTE, glClear, glClearColor, glFinish,
                glReadPixels, glViewport,
            )
        except Exception as exc:
            print(f"[live2d] ✗ 离屏: OpenGL 导入失败 {exc}", flush=True)
            return None

        try:
            self.makeCurrent()
        except Exception as exc:
            print(f"[live2d] ✗ 离屏: makeCurrent 失败（窗口隐藏导致？）{exc}", flush=True)
            return None

        try:
            if not self._ensure_fbo(w, h):
                print("[live2d] ✗ 离屏: FBO 创建失败", flush=True)
                return None
            if not self._fbo.bind():
                print("[live2d] ✗ 离屏: FBO bind 失败", flush=True)
                return None

            glViewport(0, 0, w, h)
            glClearColor(0.0, 0.0, 0.0, 0.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            self._draw_model()
            glFinish()

            img = QImage(w, h, QImage.Format_RGBA8888)
            self._read_pixels_into(img, w, h)
            self._fbo.release()
            return img.mirrored(False, True)
        except Exception as exc:
            print(f"[live2d] ✗ 离屏: 渲染异常 {type(exc).__name__}: {exc}", flush=True)
            return None
        finally:
            try:
                self.doneCurrent()
            except Exception:
                pass

    def _read_pixels_into(self, img, w: int, h: int):
        """用 numpy 或 ctypes 作为 glReadPixels 的目标缓冲。

        PyOpenGL 不接受 PyQt5 的 sip.voidptr，必须转成 numpy 数组
        或 ctypes 数组，否则抛 "No array-type handler"。
        """
        from OpenGL.GL import GL_RGBA, GL_UNSIGNED_BYTE, glReadPixels

        nbytes = w * h * 4
        ptr = img.bits()
        ptr.setsize(nbytes)

        # 方案 A：numpy（PyOpenGL 原生支持，最快）
        try:
            import numpy as np
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, w, 4)
            glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, arr)
            return
        except ImportError:
            pass
        except Exception as exc:
            print(f"[live2d] numpy 读像素失败，退到 ctypes: {exc}", flush=True)

        # 方案 B：ctypes（无需 numpy）
        import ctypes
        addr = int(ptr)
        buf = (ctypes.c_ubyte * nbytes).from_address(addr)
        glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, buf)

    def _on_frame_swapped(self):
        """只在 Qt 确认首帧已交换到屏幕后通知宿主显现。"""
        if self._frame_drawn and not self._first_frame_emitted:
            self._first_frame_emitted = True
            self.first_frame_ready.emit()

    def _on_timer(self):
        self.update()

    def _schedule_zone_click(self, signal):
        """排一次分区反馈；已有一发待发就换掉它，不叠。"""
        self._pending_zone_signal = signal
        self._zone_click_timer.start(QApplication.doubleClickInterval())

    def _cancel_zone_click(self):
        self._pending_zone_signal = None
        self._zone_click_timer.stop()

    def _on_zone_click_timeout(self):
        signal, self._pending_zone_signal = self._pending_zone_signal, None
        if signal is not None:
            signal.emit()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self._parent_in_standby():
            self._press_pos = None
            self._dragging_window = False
            self._drag_pointer_origin = None
            self._drag_window_origin = None
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = (event.x(), event.y())
            self._press_time = time.time()
            parent = self.parentWidget()
            self._drag_pointer_origin = event.globalPos()
            self._drag_window_origin = parent.pos() if parent is not None else None
            self._dragging_window = False
            event.accept()
        else:
            self._press_pos = None

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self._parent_in_standby():
            return

        # 窗口拖拽逻辑
        if (
            event.buttons() & Qt.LeftButton
            and self._drag_pointer_origin is not None
            and self._drag_window_origin is not None
        ):
            distance = (event.globalPos() - self._drag_pointer_origin).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._dragging_window = True
                parent = self.parentWidget()
                if parent is not None:
                    target = calculate_drag_position(
                        self._drag_window_origin,
                        self._drag_pointer_origin,
                        event.globalPos(),
                    )
                    queue_move = getattr(parent, "_queue_drag_position", None)
                    if callable(queue_move):
                        queue_move(target)
                    else:
                        parent.move(target)
                event.accept()
                return

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._parent_in_standby():
            self._press_pos = None
            self._dragging_window = False
            self._drag_pointer_origin = None
            self._drag_window_origin = None
            return

        if event.button() == Qt.LeftButton:
            parent = self.parentWidget()
            flush_move = getattr(parent, "_flush_drag_position", None)
            if callable(flush_move):
                flush_move()
            self._drag_pointer_origin = None
            self._drag_window_origin = None

            # 如果发生了窗口拖拽，不触发互动
            if self._dragging_window:
                self._dragging_window = False
                self._press_pos = None
                event.accept()
                return

            # 有效的点击（非拖拽）→ 分区判定
            if self.l2d.model and self._press_pos is not None:
                px, py = self._press_pos
                dist = math.sqrt((event.x() - px)**2 + (event.y() - py)**2)
                press_duration = time.time() - self._press_time

                if (
                    dist < ZONE_CLICK_MAX_DISTANCE_PX
                    and press_duration < ZONE_CLICK_MAX_HOLD_SECONDS
                ):
                    w, h = self.width(), self.height()
                    if w <= 0 or h <= 0:
                        self._press_pos = None
                        event.accept()
                        return

                    # 归一化坐标 [-1, 1]，Y 翻转：Qt 顶部=0，Live2D 底部=-1
                    nx = (event.x() / w) * 2.0 - 1.0
                    ny = -((event.y() / h) * 2.0 - 1.0)

                    # 分区判定（全窗口范围，不再做椭圆裁剪过滤）
                    if ny > 0.00:
                        self.l2d.model.StartMotion("Idle", 0, live2d.MotionPriority.FORCE)
                        self._schedule_zone_click(self.head_patted)
                    elif nx < 0.0:
                        self.l2d.model.StartMotion("Angry", 0, live2d.MotionPriority.FORCE)
                        self._schedule_zone_click(self.lower_left_patted)
                    else:
                        self.l2d.model.StartMotion("Angry", 0, live2d.MotionPriority.FORCE)
                        self._schedule_zone_click(self.lower_right_patted)

            self._press_pos = None
            event.accept()

        self._press_pos = None

    def mouseDoubleClickEvent(self, event):
        """Live2D 子控件消费鼠标事件时，显式把左键双击转成聊天请求。"""
        if self._parent_in_standby():
            return
        if event.button() == Qt.LeftButton:
            self._dragging_window = False
            self._drag_pointer_origin = None
            self._drag_window_origin = None
            self._press_pos = None
            self._cancel_zone_click()
            self.chat_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def play_motion(self, motion_name: str, priority=3):
        """播放指定 motion"""
        if self.l2d.model:
            self.l2d.model.StartMotion(motion_name, 0, priority)

    def shutdown(self):
        self._timer.stop()
        self._cancel_zone_click()
        self._ready = False
        self._fbo = None
        self._fbo_size = (0, 0)


# ====== 工具函数 ======

def init_live2d():
    """初始化全局 Live2D runtime；重复切换渲染模式时保持幂等。"""
    global _LIVE2D_INITIALIZED
    if not LIVE2D_AVAILABLE:
        raise RuntimeError(LIVE2D_UNAVAILABLE_REASON)
    if not _LIVE2D_INITIALIZED:
        live2d.init()
        _LIVE2D_INITIALIZED = True


def dispose_live2d():
    """程序退出时释放 Live2D"""
    global _LIVE2D_INITIALIZED
    try:
        if LIVE2D_AVAILABLE and _LIVE2D_INITIALIZED:
            live2d.dispose()
            _LIVE2D_INITIALIZED = False
    except Exception:
        pass

