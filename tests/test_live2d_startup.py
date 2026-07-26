"""Live2D 启动连续性与回退路径的回归测试。"""

from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QMouseEvent, QPixmap, QRegion  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from meapet.desktop.chat_flow import PetChatFlowMixin  # noqa: E402
from meapet.desktop.render_host import PetRenderHostMixin  # noqa: E402


class _SignalStub:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


class _Live2DWidgetStub(QWidget):
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
    created = 0

    def __init__(self, _model_dir: str) -> None:
        type(self).created += 1
        self.widget = None

    def create_widget(self, parent=None):
        self.widget = _Live2DWidgetStub(parent)
        return self.widget

    def get_suggested_size(self):
        return (525, 735)


class _InteractiveLive2DModelStub:
    """使用真实 Live2DWidget，但不初始化模型或 OpenGL 的交互测试替身。"""

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
    def __init__(self, model_dir: str) -> None:
        super().__init__()
        self.config = {
            "character": {"default_outfit": "01", "default_direction": "A"},
            "display": {"scale": 0.5, "size_factor": 1.0},
            "live2d": {"enabled": True, "model_dir": model_dir},
        }
        self.hit_region_updates = 0
        self.placements = 0

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
        return (
            mock.patch("meapet.desktop.render_host.SpriteRenderer", _SpriteRendererStub),
            mock.patch(
                "meapet.desktop.live2d_widget.Live2DModel",
                _Live2DModelStub,
            ),
            mock.patch("meapet.desktop.live2d_widget.init_live2d"),
        )

    def test_live2d_is_the_only_startup_renderer_until_its_first_frame(self) -> None:
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

            # OpenGL 可能继续交换很多帧，但启动完成逻辑只能运行一次。
            host.sprite_label.first_frame_ready.emit()
            QApplication.processEvents()
            self.assertEqual(ready, ["ready"])

    def test_closing_host_cancels_pending_live2d_startup_timeout(self) -> None:
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

    def test_windows_live2d_stays_mapped_without_opacity_or_visibility_reset(self) -> None:
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            # OS shape 调用与平台无关（ctypes 封装在 window_shape）；此处不 patch platform。
            with sprite_patch, model_patch, init_patch:
                host.init_renderer()
                self.assertEqual(host.windowOpacity(), 1.0)

                with (
                    mock.patch.object(host, "hide") as hide,
                    mock.patch.object(host, "show") as show,
                    mock.patch.object(host, "raise_") as raise_window,
                    mock.patch.object(host.sprite_label, "show") as show_widget,
                    mock.patch.object(host.sprite_label, "update") as update_widget,
                    mock.patch(
                        "meapet.desktop.render_host.apply_ellipse_window_shape",
                        return_value=True,
                    ),
                ):
                    host.sprite_label.first_frame_ready.emit()
                    QApplication.processEvents()

                hide.assert_not_called()
                show.assert_not_called()
                raise_window.assert_not_called()
                show_widget.assert_called_once_with()
                update_widget.assert_called_once_with()
                self.assertEqual(host.windowOpacity(), 1.0)

    def test_live2d_initialization_failure_reveals_png_fallback(self) -> None:
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

    def test_png_frames_are_cached_and_reused_across_blinks(self) -> None:
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

    def test_live2d_uses_the_model_suggested_aspect_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host.init_renderer()

            self.assertEqual((host.width(), host.height()), (525, 735))
            self.assertEqual(
                (host.sprite_label.width(), host.sprite_label.height()),
                (525, 735),
            )

            host._size_factor_preview(1.2)

            self.assertEqual((host.width(), host.height()), (630, 882))
            self.assertEqual(
                (host.sprite_label.width(), host.sprite_label.height()),
                (630, 882),
            )

    def test_ellipse_mask_region_uses_normalized_ratios(self) -> None:
        from meapet.desktop.render_host import ellipse_mask_region

        region = ellipse_mask_region(
            200,
            400,
            {"enabled": True, "cx": 0.50, "cy": 0.50, "rw": 0.25, "rh": 0.40},
        )
        self.assertFalse(region.isEmpty())
        bounds = region.boundingRect()
        # Qt ellipse boundingRect 可能比构造矩形略收缩 1px，只校验大致几何。
        self.assertAlmostEqual(bounds.center().x(), 100, delta=1)
        self.assertAlmostEqual(bounds.center().y(), 200, delta=1)
        self.assertAlmostEqual(bounds.width(), 100, delta=2)
        self.assertAlmostEqual(bounds.height(), 320, delta=2)

    def test_live2d_hit_region_applies_ellipse_mask_when_enabled(self) -> None:
        host = self._host("")
        host._use_live2d = True
        host._size_factor = 1.12
        host.config["live2d"]["window_mask"] = {
            "enabled": True,
            "cx": 0.54,
            "cy": 0.41,
            "rw": 0.26,
            "rh": 0.38,
        }
        host.resize(500, 700)
        host.sprite_label = QWidget(host)
        host.sprite_label.resize(500, 700)

        PetRenderHostMixin._apply_hit_region(host)

        self.assertFalse(host.mask().isEmpty())
        bounds = host.mask().boundingRect()
        # 2*round(0.26*500)=260, 2*round(0.38*700)=532
        self.assertAlmostEqual(bounds.width(), 260, delta=2)
        self.assertAlmostEqual(bounds.height(), 532, delta=2)
        self.assertFalse(host.sprite_label.mask().isEmpty())

    def test_live2d_hit_region_clears_mask_when_disabled(self) -> None:
        host = self._host("")
        host._use_live2d = True
        host.config["live2d"]["window_mask"] = {
            "enabled": False,
            "cx": 0.54,
            "cy": 0.41,
            "rw": 0.26,
            "rh": 0.38,
        }
        host.resize(448, 739)
        host.setMask(QRegion(74, 82, 300, 441))
        self.assertFalse(host.mask().isEmpty())

        PetRenderHostMixin._apply_hit_region(host)

        self.assertTrue(host.mask().isEmpty())

    def test_png_mode_clears_ellipse_mask(self) -> None:
        host = self._host("")
        host._use_live2d = False
        host.resize(200, 300)
        host.setMask(QRegion(10, 10, 80, 120))

        PetRenderHostMixin._apply_hit_region(host)

        self.assertTrue(host.mask().isEmpty())

    def test_size_factor_preview_reapplies_ellipse_mask(self) -> None:
        host = self._host("")
        host._use_live2d = True
        host._size_factor = 1.0
        host._l2d_model = _Live2DModelStub("unused")
        host.sprite_label = QWidget(host)
        host.config["live2d"]["window_mask"] = {
            "enabled": True,
            "cx": 0.50,
            "cy": 0.50,
            "rw": 0.25,
            "rh": 0.25,
        }
        # 用真实 mixin 方法（host 默认 stub 掉了 _apply_hit_region）
        host._apply_hit_region = lambda: PetRenderHostMixin._apply_hit_region(host)

        PetRenderHostMixin._size_factor_preview(host, 1.0)
        bounds_a = host.mask().boundingRect()
        self.assertEqual((host.width(), host.height()), (525, 735))
        self.assertAlmostEqual(bounds_a.width(), 262, delta=2)
        self.assertAlmostEqual(bounds_a.height(), 368, delta=2)

        PetRenderHostMixin._size_factor_preview(host, 2.0)
        bounds_b = host.mask().boundingRect()
        self.assertEqual((host.width(), host.height()), (1050, 1470))
        self.assertAlmostEqual(bounds_b.width(), 524, delta=2)
        self.assertAlmostEqual(bounds_b.height(), 736, delta=2)

    def test_clear_window_region_clears_os_shape(self) -> None:
        host = self._host("")
        host.resize(200, 300)
        clear_shape = mock.Mock(return_value=True)

        with mock.patch(
            "meapet.desktop.render_host.clear_window_shape", clear_shape
        ):
            host._clear_window_region()

        clear_shape.assert_called_once()
        args, kwargs = clear_shape.call_args
        self.assertEqual(args[0], int(host.winId()))
        self.assertEqual(kwargs.get("width"), 200)
        self.assertEqual(kwargs.get("height"), 300)

    def test_apply_hit_region_sets_os_ellipse_shape(self) -> None:
        host = self._host("")
        host._use_live2d = True
        host.resize(500, 700)
        host.sprite_label = QWidget(host)
        host.sprite_label.resize(500, 700)
        host.config["live2d"]["window_mask"] = {
            "enabled": True,
            "cx": 0.54,
            "cy": 0.41,
            "rw": 0.26,
            "rh": 0.38,
        }
        apply_shape = mock.Mock(return_value=True)

        with mock.patch(
            "meapet.desktop.render_host.apply_ellipse_window_shape", apply_shape
        ):
            PetRenderHostMixin._apply_hit_region(host)

        apply_shape.assert_called_once()
        args, kwargs = apply_shape.call_args
        self.assertEqual(args[0], int(host.winId()))
        self.assertEqual(args[1], 500)
        self.assertEqual(args[2], 700)
        self.assertFalse(host.mask().isEmpty())

    def test_ellipse_physical_bounds_scales_with_dpr(self) -> None:
        from meapet.desktop.window_shape import ellipse_physical_bounds

        left, top, right, bottom = ellipse_physical_bounds(
            200,
            400,
            {"enabled": True, "cx": 0.50, "cy": 0.50, "rw": 0.25, "rh": 0.40},
            dpr=1.0,
        )
        self.assertEqual((left, top, right, bottom), (50, 40, 150, 360))

        left2, top2, right2, bottom2 = ellipse_physical_bounds(
            200,
            400,
            {"enabled": True, "cx": 0.50, "cy": 0.50, "rw": 0.25, "rh": 0.40},
            dpr=2.0,
        )
        # physical size 400x800 → half-width 100, half-height 320
        self.assertEqual((left2, top2, right2, bottom2), (100, 80, 300, 720))

    def test_ellipse_scanline_rects_stay_inside_bounds(self) -> None:
        from meapet.desktop.window_shape import ellipse_scanline_rects

        rects = ellipse_scanline_rects(
            100,
            100,
            {"enabled": True, "cx": 0.5, "cy": 0.5, "rw": 0.4, "rh": 0.3},
            dpr=1.0,
        )
        self.assertTrue(rects)
        for x, y, w, h in rects:
            self.assertGreaterEqual(w, 1)
            self.assertEqual(h, 1)
            self.assertGreaterEqual(y, 20)  # cy-rh = 20
            self.assertLess(y, 80)

    def test_png_to_live2d_clears_the_previous_window_mask_first(self) -> None:
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            host._use_live2d = False
            host._l2d_pending = False
            host._renderer_ready = True
            host._renderer_ready_callbacks = []
            host._scale = 0.5
            host._size_factor = 1.0
            host.renderer = _SpriteRendererStub()
            host.sprite_label = QWidget(host)
            host.resize(80, 120)
            host.setMask(QRegion(0, 0, 40, 60))
            host._save_config = mock.Mock()

            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host._toggle_render_mode()

            # 启动阶段先清旧 mask；椭圆 mask 等到首帧 _apply_hit_region。
            self.assertTrue(host.mask().isEmpty())
            self.assertTrue(host._use_live2d)
            self.assertTrue(host._l2d_pending)

    def test_live2d_to_png_clears_ellipse_mask(self) -> None:
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            host._use_live2d = True
            host._l2d_pending = False
            host._scale = 0.5
            host._size_factor = 1.0
            host._l2d_model = _Live2DModelStub(model_dir)
            old_widget = _Live2DWidgetStub(host)
            host.sprite_label = old_widget
            host.resize(200, 300)
            host.setMask(QRegion(20, 20, 100, 150))
            host._save_config = mock.Mock()
            host._show_bubble = mock.Mock()
            host.renderer = None

            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host._apply_hit_region = (
                    lambda: PetRenderHostMixin._apply_hit_region(host)
                )
                host._toggle_render_mode()

            self.assertFalse(host._use_live2d)
            self.assertTrue(host.mask().isEmpty())
            self.assertTrue(old_widget.shutdown_called)

    def test_widget_reports_first_frame_and_initialization_failure(self) -> None:
        from meapet.desktop.live2d_widget import Live2DWidget

        self.assertTrue(hasattr(Live2DWidget, "first_frame_ready"))
        self.assertTrue(hasattr(Live2DWidget, "initialization_failed"))
        paint_source = inspect.getsource(Live2DWidget.paintGL)
        self.assertIn("glClearColor(0.0, 0.0, 0.0, 0.0)", paint_source)
        self.assertIn("_apply_ellipse_stencil_clip", paint_source)
        init_source = inspect.getsource(Live2DWidget.__init__)
        self.assertIn("setStencilBufferSize", init_source)

    def test_ellipse_stencil_ndc_vertices_match_normalized_ellipse(self) -> None:
        from meapet.desktop.live2d_widget import ellipse_stencil_ndc_vertices

        # 中心在窗口中心、半宽半高 0.25 → NDC 中心 (0,0)，半径 0.5
        verts = ellipse_stencil_ndc_vertices(0.5, 0.5, 0.25, 0.25, segments=8)
        self.assertEqual(verts[0], (0.0, 0.0))
        self.assertEqual(verts[0], verts[0])
        # 闭合：首尾边界点应重合
        self.assertAlmostEqual(verts[1][0], verts[-1][0], places=5)
        self.assertAlmostEqual(verts[1][1], verts[-1][1], places=5)
        # 右端点 angle=0 → (0.5, 0)
        self.assertAlmostEqual(verts[1][0], 0.5, places=5)
        self.assertAlmostEqual(verts[1][1], 0.0, places=5)
        xs = [v[0] for v in verts[1:]]
        ys = [v[1] for v in verts[1:]]
        self.assertAlmostEqual(max(xs), 0.5, places=5)
        self.assertAlmostEqual(min(xs), -0.5, places=5)
        self.assertAlmostEqual(max(ys), 0.5, places=5)
        self.assertAlmostEqual(min(ys), -0.5, places=5)

    def test_ellipse_stencil_ndc_respects_qt_y_down_center(self) -> None:
        from meapet.desktop.live2d_widget import ellipse_stencil_ndc_vertices

        # cy=0.25（偏上）→ NDC y = 1 - 2*0.25 = 0.5
        verts = ellipse_stencil_ndc_vertices(0.54, 0.25, 0.1, 0.1, segments=4)
        self.assertAlmostEqual(verts[0][0], 2.0 * 0.54 - 1.0, places=5)
        self.assertAlmostEqual(verts[0][1], 0.5, places=5)

    def test_live2d_left_double_click_emits_chat_request(self) -> None:
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
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            host._start_chat = mock.Mock()
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with sprite_patch, model_patch, init_patch:
                host.init_renderer()
                host.sprite_label.chat_requested.emit()

            host._start_chat.assert_called_once_with()

    def test_live2d_double_click_opens_a_visible_chat_input_end_to_end(self) -> None:
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

    def test_app_keeps_splash_until_renderer_reports_ready(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "meapet" / "desktop" / "app.py"
        ).read_text(encoding="utf-8")

        self.assertIn("when_renderer_ready", source)
        self.assertNotIn("QTimer.singleShot(200, _ensure_visible)", source)


if __name__ == "__main__":
    unittest.main()
