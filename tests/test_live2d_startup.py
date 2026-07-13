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
from PyQt5.QtGui import QMouseEvent, QPixmap  # noqa: E402
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

    def test_windows_live2d_stays_mapped_without_opacity_or_visibility_reset(self) -> None:
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            sprite_patch, model_patch, init_patch = self._patch_renderers()
            with (
                sprite_patch,
                model_patch,
                init_patch,
                mock.patch("meapet.desktop.render_host.sys.platform", "win32"),
            ):
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

    def test_widget_reports_first_frame_and_initialization_failure(self) -> None:
        from meapet.desktop.live2d_widget import Live2DWidget

        self.assertTrue(hasattr(Live2DWidget, "first_frame_ready"))
        self.assertTrue(hasattr(Live2DWidget, "initialization_failed"))
        paint_source = inspect.getsource(Live2DWidget.paintGL)
        self.assertIn("glClearColor(0.0, 0.0, 0.0, 0.0)", paint_source)

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
