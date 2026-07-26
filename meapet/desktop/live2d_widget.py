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
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QOpenGLWidget
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
try:
    import live2d.v3 as live2d
    LIVE2D_AVAILABLE = True
except ImportError:  # optional dependency
    live2d = None  # type: ignore
    LIVE2D_AVAILABLE = False
_LIVE2D_INITIALIZED = False
from PyQt5.QtCore import QEvent
from PyQt5.QtGui import QSurfaceFormat

from meapet.desktop.render_host import calculate_drag_position
from meapet.config.store import (
    DEFAULT_LIVE2D_WINDOW_MASK,
    normalize_live2d_window_mask,
)
from meapet.log import get_color_logger

log = get_color_logger("live2d_widget")

# 在 Windows 下可选导入 win32api（DLL 缺失时不阻塞启动）
if sys.platform == "win32":
    try:
        import win32api
        import win32con
    except Exception:
        win32api = None
        win32con = None


_ELLIPSE_STENCIL_SEGMENTS = 64

_STENCIL_VERT_SRC = """
#version 330 core
layout(location = 0) in vec2 aPos;
void main() {
    gl_Position = vec4(aPos, 0.0, 1.0);
}
"""

_STENCIL_FRAG_SRC = """
#version 330 core
out vec4 fragColor;
void main() {
    fragColor = vec4(0.0);
}
"""


def ellipse_stencil_ndc_vertices(
    cx: float,
    cy: float,
    rw: float,
    rh: float,
    segments: int = _ELLIPSE_STENCIL_SEGMENTS,
) -> list[tuple[float, float]]:
    """按与 setMask 相同的 0–1 比例，生成 NDC 下 TRIANGLE_FAN 顶点。

    输入为窗口归一化椭圆（Qt 顶为 y=0）；输出 OpenGL NDC（y 向上）。
    顶点 0 为中心，其后为 segments+1 个边界点（首尾闭合）。
    """
    segs = max(8, int(segments))
    ndc_cx = 2.0 * float(cx) - 1.0
    ndc_cy = 1.0 - 2.0 * float(cy)
    ndc_rw = 2.0 * float(rw)
    ndc_rh = 2.0 * float(rh)
    verts: list[tuple[float, float]] = [(ndc_cx, ndc_cy)]
    for i in range(segs + 1):
        angle = 2.0 * math.pi * i / segs
        # Qt y 向下：边界点 y = cy + rh*sin → NDC 需取反 sin 项
        verts.append(
            (
                ndc_cx + ndc_rw * math.cos(angle),
                ndc_cy - ndc_rh * math.sin(angle),
            )
        )
    return verts


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

    def get_model(self) -> live2d.LAppModel:
        return self.model

    def get_suggested_size(self) -> tuple:
        """返回建议显示尺寸（模型加载后）"""
        # 模型比例 5000:7000 = 5:7，目标宽度匹配 PNG 立绘
        return (525, 735)

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
        fmt.setStencilBufferSize(8)     # 椭圆视觉裁剪用 stencil
        fmt.setRenderableType(QSurfaceFormat.OpenGL)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)
        self.setFormat(fmt)


        self.l2d = l2d_model

        # 1. Qt 自身的透明设置
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True) # 确保在最上层渲染
        self.setStyleSheet("background: transparent; border: none;")

        # 鼠标追踪
        self.setMouseTracking(True)
        self._ready = False
        self._initialization_error = ""
        self._frame_drawn = False
        self._first_frame_emitted = False
        self._drag_target = (0.0, 0.0)  # 眼球追踪坐标（每帧更新）
        self._global_filter_installed = False
        self._stencil_clip_available = None  # None=未探测, True/False
        self._stencil_program = 0
        self._stencil_vao = 0
        self._stencil_vbo = 0
        self._stencil_vertex_count = 0
        self._stencil_logged_skip = False
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

        self.resize(525, 735)

        self.installEventFilter(self)


    def _parent_in_standby(self) -> bool:
        parent = self.parentWidget()
        return bool(parent is not None and getattr(parent, "_standby", False))

    def eventFilter(self, obj, event):
        # 椭圆 mask 由宿主 render_host 统一管理；此处仅做软点击过滤。
        if obj == self and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.RightButton:
                return False
            # 待机：吞掉左键等，避免偶发事件仍触发互动（穿透由宿主原生后端负责）。
            if self._parent_in_standby():
                return True
            x, y = event.x(), event.y()
            w, h = self.width(), self.height()
            if w * 0.15 < x < w * 0.85 and 0 < y < h * 0.9:
                return False
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

            # OpenGL context 出现后的第一条颜色状态就是全透明，避免默认白底。
            glClearColor(0.0, 0.0, 0.0, 0.0)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

            # 探测 stencil；打包后 QOpenGLWidget 常不吃 Qt setMask 的绘制裁剪，
            # 椭圆外透明依赖 paintGL 内 stencil（见 _apply_ellipse_stencil_clip）。
            try:
                stencil_bits = int(self.format().stencilBufferSize())
            except Exception:
                stencil_bits = 0
            self._stencil_clip_available = stencil_bits > 0
            if self._stencil_clip_available:
                try:
                    self._init_stencil_resources()
                except Exception as exc:
                    self._stencil_clip_available = False
                    log.warning(
                        f"[live2d] stencil resources init failed, "
                        f"visual ellipse clip disabled: {exc}"
                    )
            else:
                log.debug(
                    "[live2d] no stencil buffer; visual ellipse clip disabled "
                    "(hit mask still via Qt setMask)"
                )

            model = live2d.LAppModel()
            model.LoadModelJson(self.l2d._model_json)
            model.SetAutoBlinkEnable(True)
            model.SetAutoBreathEnable(True)
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
        """用 Resize（max 逻辑填满窗口）+ 补偿透明边距"""
        model.Resize(self.width(), self.height())

    def resizeGL(self, w, h):
        try:
            from OpenGL.GL import glViewport
        except Exception as exc:
            self._report_initialization_failure(exc)
            return
        # 高DPI下需要用物理像素设置视口
        dpr = self.devicePixelRatio()
        glViewport(0, 0, int(w * dpr), int(h * dpr))
        if self.l2d.model and w > 0 and h > 0:
            self._fit_model_to_window(self.l2d.model)

    def paintGL(self):
        try:
            from OpenGL.GL import (
                GL_COLOR_BUFFER_BIT,
                GL_DEPTH_BUFFER_BIT,
                GL_STENCIL_BUFFER_BIT,
                GL_STENCIL_TEST,
                glClear,
                glClearColor,
                glDisable,
            )
        except Exception as exc:
            self._report_initialization_failure(exc)
            return

        # 即使模型尚未 ready，也先把 framebuffer 清成透明，绝不提交白色空帧。
        # 上一帧可能把 stencil mask 置 0，清屏前必须恢复，否则 STENCIL clear 无效。
        try:
            from OpenGL.GL import glStencilMask

            glStencilMask(0xFF)
        except Exception:
            pass
        glClearColor(0.0, 0.0, 0.0, 0.0)
        glClear(
            GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT
        )
        if not self._ready or not self.l2d.model:
            # 记录为什么没渲染
            if not hasattr(self, '_dbg_skip'):
                self._dbg_skip = 0
            self._dbg_skip += 1
            if self._dbg_skip <= 3:
                log.debug(f"[paint] SKIP _ready={self._ready} model={self.l2d.model is not None}")
            return

        # 每 2400 帧输出一次心跳
        if not hasattr(self, '_dbg_frame'):
            self._dbg_frame = 0
        self._dbg_frame += 1
        if self._dbg_frame % 2400 == 0:
            log.debug(f"[paint] frame={self._dbg_frame} alive")

        live2d.clearBuffer()

        # 每帧从系统获取光标全局坐标，映射后驱动眼球+身体追踪
        from PyQt5.QtGui import QCursor
        gp = QCursor.pos()
        wp = self.mapToGlobal(self.rect().topLeft())
        w, h = self.width(), self.height()
        if (
            not self._dragging_window
            and w > 0
            and h > 0
            and self.l2d.model
        ):
            # 鼠标相对窗口中心，归一化到[-1,1]
            cx = (gp.x() - wp.x() - w / 2) / (w / 2)
            cy = (gp.y() - wp.y() - h / 2) / (h / 2)
            cx = max(-1.0, min(1.0, cx))
            cy = max(-1.0, min(1.0, cy))
            # 直接用 SetParameterValue 驱动追踪参数（权重1.0=立即生效）
            self.l2d.model.SetParameterValue("ParamAngleX", cx * 30, 1.0)
            self.l2d.model.SetParameterValue("ParamAngleY", -cy * 30, 1.0)
            self.l2d.model.SetParameterValue("ParamBodyAngleZ", cx * 10, 1.0)
            self.l2d.model.SetParameterValue("ParamAngleZ", cx * 10, 1.0)

        stencil_on = self._apply_ellipse_stencil_clip()
        try:
            self.l2d.model.Update()
            self.l2d.model.Draw()
        finally:
            if stencil_on:
                try:
                    from OpenGL.GL import glColorMask, glStencilMask

                    glDisable(GL_STENCIL_TEST)
                    glStencilMask(0xFF)
                    glColorMask(True, True, True, True)
                except Exception:
                    pass
        self._frame_drawn = True

    def _window_mask_params(self) -> dict:
        """与宿主 setMask 共用同一套归一化椭圆参数。"""
        parent = self.parentWidget()
        getter = getattr(parent, "_live2d_window_mask_params", None) if parent else None
        if callable(getter):
            try:
                return normalize_live2d_window_mask(getter())
            except Exception:
                pass
        return dict(DEFAULT_LIVE2D_WINDOW_MASK)

    def _init_stencil_resources(self):
        """Core Profile 下用极简 shader + VAO 画 stencil 椭圆。"""
        import ctypes
        from array import array

        from OpenGL.GL import (
            GL_ARRAY_BUFFER,
            GL_COMPILE_STATUS,
            GL_FALSE,
            GL_FLOAT,
            GL_FRAGMENT_SHADER,
            GL_LINK_STATUS,
            GL_STATIC_DRAW,
            GL_VERTEX_SHADER,
            glAttachShader,
            glBindBuffer,
            glBindVertexArray,
            glBufferData,
            glCompileShader,
            glCreateProgram,
            glCreateShader,
            glDeleteShader,
            glEnableVertexAttribArray,
            glGenBuffers,
            glGenVertexArrays,
            glGetProgramiv,
            glGetShaderiv,
            glLinkProgram,
            glShaderSource,
            glVertexAttribPointer,
        )

        def _compile(src: str, shader_type: int) -> int:
            shader = glCreateShader(shader_type)
            glShaderSource(shader, src)
            glCompileShader(shader)
            if not glGetShaderiv(shader, GL_COMPILE_STATUS):
                from OpenGL.GL import glGetShaderInfoLog

                raise RuntimeError(
                    f"stencil shader compile failed: {glGetShaderInfoLog(shader)}"
                )
            return shader

        vert = _compile(_STENCIL_VERT_SRC, GL_VERTEX_SHADER)
        frag = _compile(_STENCIL_FRAG_SRC, GL_FRAGMENT_SHADER)
        program = glCreateProgram()
        glAttachShader(program, vert)
        glAttachShader(program, frag)
        glLinkProgram(program)
        glDeleteShader(vert)
        glDeleteShader(frag)
        if not glGetProgramiv(program, GL_LINK_STATUS):
            from OpenGL.GL import glGetProgramInfoLog

            raise RuntimeError(
                f"stencil program link failed: {glGetProgramInfoLog(program)}"
            )

        vao = glGenVertexArrays(1)
        vbo = glGenBuffers(1)
        # 占位缓冲，首帧按 mask 参数上传真实顶点
        placeholder = array("f", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        glBindVertexArray(vao)
        glBindBuffer(GL_ARRAY_BUFFER, vbo)
        glBufferData(
            GL_ARRAY_BUFFER,
            len(placeholder) * 4,
            (ctypes.c_float * len(placeholder))(*placeholder),
            GL_STATIC_DRAW,
        )
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 8, ctypes.c_void_p(0))
        glBindVertexArray(0)
        glBindBuffer(GL_ARRAY_BUFFER, 0)

        self._stencil_program = int(program)
        self._stencil_vao = int(vao)
        self._stencil_vbo = int(vbo)
        self._stencil_vertex_count = 0

    def _upload_ellipse_stencil_geometry(self, params: dict) -> int:
        """按当前 mask 参数上传 NDC 椭圆扇，返回顶点数量。"""
        from OpenGL.GL import (
            GL_ARRAY_BUFFER,
            GL_DYNAMIC_DRAW,
            glBindBuffer,
            glBufferData,
        )
        import ctypes

        verts = ellipse_stencil_ndc_vertices(
            float(params["cx"]),
            float(params["cy"]),
            float(params["rw"]),
            float(params["rh"]),
        )
        flat = []
        for x, y in verts:
            flat.append(float(x))
            flat.append(float(y))
        count = len(verts)
        buf = (ctypes.c_float * len(flat))(*flat)
        glBindBuffer(GL_ARRAY_BUFFER, self._stencil_vbo)
        glBufferData(GL_ARRAY_BUFFER, len(flat) * 4, buf, GL_DYNAMIC_DRAW)
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        self._stencil_vertex_count = count
        return count

    def _apply_ellipse_stencil_clip(self) -> bool:
        """写入椭圆 stencil 并启用 EQUAL 测试；成功返回 True（调用方须 disable）。"""
        if not self._stencil_clip_available:
            return False
        if not self._stencil_program or not self._stencil_vao:
            return False

        params = self._window_mask_params()
        if not params.get("enabled", True):
            return False

        try:
            from OpenGL.GL import (
                GL_ALWAYS,
                GL_EQUAL,
                GL_KEEP,
                GL_REPLACE,
                GL_STENCIL_TEST,
                GL_TRIANGLE_FAN,
                glBindVertexArray,
                glColorMask,
                glDisable,
                glDrawArrays,
                glEnable,
                glStencilFunc,
                glStencilMask,
                glStencilOp,
                glUseProgram,
            )
        except Exception as exc:
            if not self._stencil_logged_skip:
                self._stencil_logged_skip = True
                log.debug(f"[live2d] stencil imports failed: {exc}")
            return False

        try:
            count = self._upload_ellipse_stencil_geometry(params)
            if count < 3:
                return False

            # 1) 只写 stencil：椭圆内 = 1
            glEnable(GL_STENCIL_TEST)
            glStencilMask(0xFF)
            glStencilFunc(GL_ALWAYS, 1, 0xFF)
            glStencilOp(GL_KEEP, GL_KEEP, GL_REPLACE)
            glColorMask(False, False, False, False)

            glUseProgram(self._stencil_program)
            glBindVertexArray(self._stencil_vao)
            # VBO 已在 upload 时绑定属性；VAO 记录了 attrib 0
            glDrawArrays(GL_TRIANGLE_FAN, 0, count)
            glBindVertexArray(0)
            glUseProgram(0)

            # 2) 后续 Draw 仅通过 stencil==1
            glColorMask(True, True, True, True)
            glStencilFunc(GL_EQUAL, 1, 0xFF)
            glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP)
            glStencilMask(0x00)
            return True
        except Exception as exc:
            if not self._stencil_logged_skip:
                self._stencil_logged_skip = True
                log.warning(f"[live2d] ellipse stencil clip failed: {exc}")
            try:
                glColorMask(True, True, True, True)
                glDisable(GL_STENCIL_TEST)
            except Exception:
                pass
            return False

    def _on_frame_swapped(self):
        """只在 Qt 确认首帧已交换到屏幕后通知宿主显现。"""
        if self._frame_drawn and not self._first_frame_emitted:
            self._first_frame_emitted = True
            self.first_frame_ready.emit()

    def _on_timer(self):
        self.update()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self._parent_in_standby():
            self._press_pos = None
            self._dragging_window = False
            self._drag_pointer_origin = None
            self._drag_window_origin = None
            return
        # 仅当左键按下时，记录初始位置
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

        # 1. 处理窗口拖拽逻辑
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

                if dist < 35 and press_duration < 0.4:
                    w, h = self.width(), self.height()
                    if w <= 0 or h <= 0:
                        self._press_pos = None
                        event.accept()
                        return

                    # 归一化坐标 [-1, 1]，Y 翻转：Qt 顶部=0，Live2D 底部=-1
                    nx = (event.x() / w) * 2.0 - 1.0
                    ny = -((event.y() / h) * 2.0 - 1.0)

                    # 超出模型渲染区域 → 不触发
                    if nx < -0.35 or nx > 0.55 or ny < -0.45 or ny > 0.85:
                        self._press_pos = None
                        event.accept()
                        return

                    if ny > 0.00:
                        self.l2d.model.StartMotion("Idle", 0, live2d.MotionPriority.FORCE)
                        self.head_patted.emit()
                    elif nx < 0.0:
                        self.l2d.model.StartMotion("Angry", 0, live2d.MotionPriority.FORCE)
                        self.lower_left_patted.emit()
                    else:
                        self.l2d.model.StartMotion("Angry", 0, live2d.MotionPriority.FORCE)
                        self.lower_right_patted.emit()

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
        self._ready = False
        self._stencil_clip_available = False
        self._stencil_program = 0
        self._stencil_vao = 0
        self._stencil_vbo = 0
        self._stencil_vertex_count = 0


# ====== 工具函数 ======

def init_live2d():
    """初始化全局 Live2D runtime；重复切换渲染模式时保持幂等。"""
    global _LIVE2D_INITIALIZED
    if not LIVE2D_AVAILABLE:
        raise RuntimeError("live2d package not installed")
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
