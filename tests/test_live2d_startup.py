"""Live2D 启动连续性与回退路径的回归测试。"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ── live2d 可用性检测与假模块注入 ──────────────────────────────────
# live2d_widget.py 在模块级别引用 live2d.LAppModel（类型注解求值），
# 如果 live2d 不可用，需要在导入 live2d_widget 之前注入一个假模块，
# 否则整个模块导入会失败，导致所有测试都无法加载。
try:
    import live2d  # noqa: F401
    HAVE_LIVE2D = True
except ImportError:
    HAVE_LIVE2D = False
    # 创建假 live2d 模块，使 live2d_widget.py 能正常导入
    _fake = ModuleType("live2d")
    _fake.LAppModel = type("LAppModel", (), {})  # 空类占位
    _fake.LAppLive2DManager = type("LAppLive2DManager", (), {})
    sys.modules["live2d"] = _fake
    # 如果 live2d.v3 也被引用，同样提供
    _fake_v3 = ModuleType("live2d.v3")
    _fake_v3.LAppModel = _fake.LAppModel
    sys.modules["live2d.v3"] = _fake_v3

from PyQt5.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QMouseEvent, QPixmap, QRegion  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from meapet.desktop.chat_flow import PetChatFlowMixin  # noqa: E402
from meapet.desktop.render_host import PetRenderHostMixin  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# 测试辅助类
# ════════════════════════════════════════════════════════════════════

class _SignalStub:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


class _Live2DWidgetStub(QWidget):
    """替代真实 Live2DWidget 的轻量桩。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.head_patted = _SignalStub()
        self.tail_patted = _SignalStub()
        self.lower_left_patted = _SignalStub()
        self.lower_right_patted = _SignalStub()
        self.first_frame_ready = _SignalStub()
        self.initialization_failed = _SignalStub()
        self.chat_requested = _SignalStub()
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


class _Live2DModelStub:
    """替代真实 Live2DModel 的桩，不需要 live2d 库。"""

    created = 0

    def __init__(self, _model_dir: str) -> None:
        type(self).created += 1
        self.widget = None
        self._model_dir = _model_dir

    def create_widget(self, parent=None):
        self.widget = _Live2DWidgetStub(parent)
        return self.widget

    def get_suggested_size(self):
        return (525, 735)

    def get_model(self):
        return None


class _InteractiveLive2DModelStub:
    """使用真实 Live2DWidget，但不初始化模型或 OpenGL。"""

    def __init__(self, _model_dir: str) -> None:
        self.model = None

    def create_widget(self, parent=None):
        from meapet.desktop.live2d_widget import Live2DWidget
        return Live2DWidget(self, parent)


class _SpriteRendererStub:
    created = 0

    def __init__(self, *_args) -> None:
        type(self).created += 1
        self.expression_changed = _SignalStub()
        self._pixmap = QPixmap(80, 120)
        self._pixmap.fill(Qt.transparent)

    def get_current_pixmap(self):
        return self._pixmap

    def start_blink_animation(self) -> None:
        pass

    def stop_blink_animation(self) -> None:
        pass


class _RenderHost(PetRenderHostMixin, QWidget):
    """将 PetRenderHostMixin 混入 QWidget，便于单元测试。"""

    def __init__(self, model_dir: str) -> None:
        super().__init__()
        self.config = {
            "character": {"default_outfit": "01", "default_direction": "A"},
            "display": {"scale": 0.5, "size_factor": 1.0},
            "live2d": {"enabled": True, "model_dir": model_dir},
        }
        self.hit_region_updates = 0
        self.placements = 0
        self._l2d_model = None  # 供 _size_factor_preview 调试打印使用

    def init_renderer(self) -> None:
        self._init_renderer()

    def _on_sprite_changed(self, _code: str) -> None:
        self._update_sprite()

    def _on_head_patted(self) -> None:
        pass

    def _on_tail_patted(self) -> None:
        pass

    def _on_lower_left_patted(self) -> None:
        pass

    def _on_lower_right_patted(self) -> None:
        pass

    def _start_chat(self) -> None:
        pass

    def _apply_hit_region(self) -> None:
        self.hit_region_updates += 1

    def _place_bottom_right(self) -> None:
        self.placements += 1

    def _position_bubble(self) -> None:
        pass


class _ChatRenderHost(PetChatFlowMixin, _RenderHost):
    def __init__(self, model_dir: str) -> None:
        super().__init__(model_dir)
        self.bubble = mock.Mock()

    def _on_input_submit(self, _text: str) -> None:
        pass


# ════════════════════════════════════════════════════════════════════
# 测试用例
# ════════════════════════════════════════════════════════════════════

class Live2DStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        _Live2DModelStub.created = 0
        _SpriteRendererStub.created = 0
        self._hosts = []

    def tearDown(self) -> None:
        for host in self._hosts:
            chat_input = getattr(host, "_chat_input", None)
            if chat_input is not None:
                chat_input.close()
            host.close()

    def _host(self, model_dir: str) -> _RenderHost:
        host = _RenderHost(model_dir)
        self._hosts.append(host)
        return host

    @staticmethod
    def _patch_renderers():
        """统一 patch 三个关键依赖。"""
        return (
            mock.patch(
                "meapet.desktop.render_host.SpriteRenderer", _SpriteRendererStub
            ),
            mock.patch(
                "meapet.desktop.live2d_widget.Live2DModel",
                _Live2DModelStub,
            ),
            mock.patch("meapet.desktop.live2d_widget.init_live2d"),
        )

    # ── 核心启动流程 ────────────────────────────────────────────────

    def test_live2d_is_the_only_startup_renderer_until_its_first_frame(self) -> None:
        """启动阶段只创建 Live2D，首帧就绪后才标记 renderer_ready。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host.init_renderer()

            self.assertEqual(_SpriteRendererStub.created, 0)
            self.assertEqual(_Live2DModelStub.created, 1)
            self.assertTrue(host._use_live2d)
            self.assertTrue(host._l2d_pending)
            self.assertFalse(host._renderer_ready)
            self.assertEqual(host.windowOpacity(), 1.0)

            ready = []
            host.when_renderer_ready(lambda: ready.append("ready"))
            host.sprite_label.first_frame_ready.emit()
            QApplication.processEvents()

            self.assertEqual(ready, ["ready"])
            self.assertTrue(host._renderer_ready)
            self.assertFalse(host._l2d_pending)
            self.assertEqual(host.windowOpacity(), 1.0)
            self.assertEqual(host.placements, 0)

            # 首帧信号只应触发一次
            host.sprite_label.first_frame_ready.emit()
            QApplication.processEvents()
            self.assertEqual(ready, ["ready"])

    def test_closing_host_cancels_pending_live2d_startup_timeout(self) -> None:
        """关闭宿主时，待处理的 Live2D 启动定时器应被取消。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host.init_renderer()

            timer = host._live2d_startup_timer
            self.assertIsNotNone(timer)
            self.assertTrue(timer.isActive())

            host.close()
            QApplication.processEvents()

            self.assertFalse(timer.isActive())

    # ── 窗口状态保持 ────────────────────────────────────────────────

    def test_windows_live2d_stays_mapped_without_opacity_or_visibility_reset(self) -> None:
        """首帧就绪后不应 hide/show/raise，也不应改变 opacity。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host.init_renderer()
                self.assertEqual(host.windowOpacity(), 1.0)

                with (
                    mock.patch.object(host, "hide") as hide,
                    mock.patch.object(host, "show") as show,
                    mock.patch.object(host, "raise_") as raise_window,
                    mock.patch.object(host.sprite_label, "show") as show_widget,
                    mock.patch.object(host.sprite_label, "update") as update_widget,
                ):
                    host.sprite_label.first_frame_ready.emit()
                    QApplication.processEvents()

                hide.assert_not_called()
                show.assert_not_called()
                raise_window.assert_not_called()
                show_widget.assert_called_once_with()
                update_widget.assert_called_once_with()
                self.assertEqual(host.windowOpacity(), 1.0)

    # ── 回退路径 ────────────────────────────────────────────────────

    def test_live2d_initialization_failure_reveals_png_fallback(self) -> None:
        """Live2D 初始化失败时，应自动回退到 PNG 渲染器。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host.init_renderer()
                host.sprite_label.initialization_failed.emit("OpenGL context failed")
                QApplication.processEvents()

            self.assertEqual(_Live2DModelStub.created, 1)
            self.assertEqual(_SpriteRendererStub.created, 1)
            self.assertFalse(host._use_live2d)
            self.assertTrue(host._renderer_ready)
            self.assertEqual(host.windowOpacity(), 1.0)
            self.assertEqual(host.placements, 1)

    def test_force_png_skips_live2d_and_is_ready_immediately(self) -> None:
        """设置 MEAPET_FORCE_PNG=1 时，应跳过 Live2D 直接走 PNG。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with (
                sprite_patch,
                model_patch,
                init_patch,
                mock.patch.dict(os.environ, {"MEAPET_FORCE_PNG": "1"}),
            ):
                host.init_renderer()

            self.assertEqual(_Live2DModelStub.created, 0)
            self.assertEqual(_SpriteRendererStub.created, 1)
            self.assertFalse(host._use_live2d)
            self.assertTrue(host._renderer_ready)
            self.assertEqual(host.windowOpacity(), 1.0)

    # ── PNG 渲染器单元测试 ──────────────────────────────────────────

    def test_png_frames_are_cached_and_reused_across_blinks(self) -> None:
        """眨眼动画应在打开/闭合帧之间正确切换并缓存。"""
        from meapet.desktop.renderer import SpriteRenderer

        loaded_frames = []

        def load_pixmap(path):
            frame = object()
            loaded_frames.append((path, frame))
            return frame

        with (
            mock.patch(
                "meapet.desktop.renderer.os.path.exists",
                return_value=True,
            ),
            mock.patch(
                "meapet.desktop.renderer.QPixmap",
                side_effect=load_pixmap,
            ),
        ):
            renderer = SpriteRenderer("/sprites")
            open_frame = renderer.get_current_pixmap()
            self.assertIs(renderer.get_current_pixmap(), open_frame)

            renderer._is_blinking = True
            closed_frame = renderer.get_current_pixmap()
            self.assertIs(renderer.get_current_pixmap(), closed_frame)

        self.assertIsNot(open_frame, closed_frame)
        self.assertEqual(len(loaded_frames), 2)

    def test_png_canvas_replaces_the_complete_frame_atomically(self) -> None:
        """SpriteCanvas 设置帧后应完整替换像素，不留残影。"""
        from meapet.desktop.renderer import SpriteCanvas

        canvas = SpriteCanvas()
        self._hosts.append(canvas)
        canvas.resize(24, 16)
        canvas.show()

        open_frame = QPixmap(canvas.size())
        open_frame.fill(QColor("#E7B9AD"))
        closed_frame = QPixmap(canvas.size())
        closed_frame.fill(QColor("#20233D"))

        canvas.set_frame(open_frame)
        QApplication.processEvents()
        canvas.set_frame(closed_frame)
        QApplication.processEvents()

        rendered = canvas.grab().toImage()
        expected = QColor("#20233D").rgba()
        self.assertTrue(
            all(
                rendered.pixel(x, y) == expected
                for y in range(rendered.height())
                for x in range(rendered.width())
            )
        )

    # ── 尺寸联动 ────────────────────────────────────────────────────

    def test_live2d_uses_the_model_suggested_aspect_ratio(self) -> None:
        """窗口初始尺寸应与模型画布尺寸一致，缩放后按比例变化。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host.init_renderer()

            # 初始尺寸 = 模型建议尺寸 (525, 735)
            self.assertEqual((host.width(), host.height()), (525, 735))
            self.assertEqual(
                (host.sprite_label.width(), host.sprite_label.height()),
                (525, 735),
            )

            # 注入桩模型，使 _size_factor_preview 的调试打印能正常工作
            host._l2d_model = _Live2DModelStub("unused")
            host._size_factor_preview(1.2)

            # 525 * 1.2 = 630, 735 * 1.2 = 882
            self.assertEqual((host.width(), host.height()), (630, 882))
            self.assertEqual(
                (host.sprite_label.width(), host.sprite_label.height()),
                (630, 882),
            )

    # ── 需要真实 live2d 库的测试 ────────────────────────────────────

    @unittest.skipIf(not HAVE_LIVE2D, "live2d library not available")
    def test_live2d_left_double_click_emits_chat_request(self) -> None:
        """左键双击 Live2DWidget 应发出 chat_requested 信号。"""
        from meapet.desktop.live2d_widget import Live2DWidget

        widget = Live2DWidget(SimpleNamespace(model=None))
        self._hosts.append(widget)
        requested = []
        widget.chat_requested.connect(lambda: requested.append(True))
        event = QMouseEvent(
            QEvent.MouseButtonDblClick,
            QPointF(120, 120),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

        widget.mouseDoubleClickEvent(event)

        self.assertEqual(requested, [True])
        self.assertTrue(event.isAccepted())

    def test_live2d_chat_request_is_connected_to_the_render_host(self) -> None:
        """Live2DWidget 的 chat_requested 信号应连接到宿主的 _start_chat。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            host._start_chat = mock.Mock()
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host.init_renderer()
                host.sprite_label.chat_requested.emit()

            host._start_chat.assert_called_once_with()

    @unittest.skipIf(not HAVE_LIVE2D, "live2d library not available")
    def test_live2d_double_click_opens_a_visible_chat_input_end_to_end(self) -> None:
        """端到端：双击 → 弹出聊天输入框 → 气泡隐藏。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = _ChatRenderHost(model_dir)
            self._hosts.append(host)
            with (
                mock.patch(
                    "meapet.desktop.live2d_widget.Live2DModel",
                    _InteractiveLive2DModelStub,
                ),
                mock.patch("meapet.desktop.live2d_widget.init_live2d"),
            ):
                host.init_renderer()

            event = QMouseEvent(
                QEvent.MouseButtonDblClick,
                QPointF(120, 120),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
            QApplication.sendEvent(host.sprite_label, event)
            QApplication.processEvents()

            self.assertTrue(event.isAccepted())
            self.assertTrue(hasattr(host, "_chat_input"))
            self.assertTrue(host._chat_input.isVisible())
            host.bubble.hide.assert_called_once_with()

    # ── app.py 静态检查 ─────────────────────────────────────────────

    def test_app_keeps_splash_until_renderer_reports_ready(self) -> None:
        """app.py 应使用 when_renderer_ready，而非硬编码的 QTimer.singleShot。"""
        source = (
            Path(__file__).resolve().parents[1]
            / "meapet" / "desktop" / "app.py"
        ).read_text(encoding="utf-8")

        self.assertIn("when_renderer_ready", source)
        self.assertNotIn("QTimer.singleShot(200, _ensure_visible)", source)


if __name__ == "__main__":
    unittest.main()

