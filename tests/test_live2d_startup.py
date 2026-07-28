"""Live2D 启动连续性与回退路径的回归测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QColor, QPixmap  # noqa: E402
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
        self._l2d_model = None  # 供 _size_factor_preview 调试输出使用

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

class PNGStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
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

    # ── 回退路径 ────────────────────────────────────────────────────

    def test_force_png_skips_live2d_and_is_ready_immediately(self) -> None:
        """设置 MEAPET_FORCE_PNG=1 时，应跳过 Live2D 直接走 PNG。"""
        with tempfile.TemporaryDirectory() as model_dir:
            host = self._host(model_dir)
            with mock.patch.dict(os.environ, {"MEAPET_FORCE_PNG": "1"}):
                host.init_renderer()

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

