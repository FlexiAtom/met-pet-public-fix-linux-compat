"""Live2D 窗口视口编辑器与热应用的用户路径回归测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5.QtGui import QColor, QImage  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from meapet.desktop.config_bridge import PetConfigBridgeMixin  # noqa: E402
from meapet.desktop.render_host import PetRenderHostMixin  # noqa: E402
from wizard.live2d_viewport import (  # noqa: E402
    Live2DViewportSettings,
    constrain_viewport_edges,
    viewport_edges_to_window_mask,
    window_mask_to_viewport_edges,
)


class _RuntimeConfigHost(PetConfigBridgeMixin):
    """只保留配置热应用所需接口的桌宠替身。"""

    def __init__(self) -> None:
        self.config = {
            "display": {"size_factor": 1.0},
            "live2d": {
                "enabled": True,
                "window_mask": {
                    "enabled": True,
                    "cx": 0.50,
                    "cy": 0.50,
                    "rw": 0.40,
                    "rh": 0.40,
                },
            },
        }
        self._size_factor = 1.0
        self.viewport_apply_count = 0

    def _invalidate_active_conversation(self) -> None:
        pass

    def _stop_control(self) -> None:
        pass

    def _disconnect_watcher_signals(self) -> None:
        pass

    def _apply_motion_preference(self) -> None:
        pass

    def _apply_display_preference(self) -> None:
        pass

    def _apply_live2d_viewport_preference(self) -> None:
        self.viewport_apply_count += 1

    def _init_tts(self) -> None:
        pass

    def _init_chat(self) -> None:
        pass

    def _init_watcher(self) -> None:
        pass

    def _init_control(self) -> None:
        pass


class Live2DViewportEditorTests(unittest.TestCase):
    """用户可框选透明画布范围，同时保持配置和运行时链路稳定。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._widgets = []

    def tearDown(self) -> None:
        for widget in reversed(self._widgets):
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        QApplication.processEvents()

    def _track(self, widget):
        self._widgets.append(widget)
        return widget

    def test_historical_mask_round_trips_through_rectangle_edges(self) -> None:
        mask = {
            "enabled": True,
            "cx": 0.54,
            "cy": 0.40,
            "rw": 0.30,
            "rh": 0.40,
        }

        edges = window_mask_to_viewport_edges(mask)

        self.assertEqual(edges, (0.24, 0.0, 0.84, 0.8))
        self.assertEqual(
            viewport_edges_to_window_mask(*edges, enabled=True),
            mask,
        )

    def test_viewport_edges_stay_inside_canvas_with_a_safe_minimum_span(self) -> None:
        left, top, right, bottom = constrain_viewport_edges(
            0.96,
            0.93,
            1.40,
            1.20,
        )

        self.assertGreaterEqual(left, 0.0)
        self.assertGreaterEqual(top, 0.0)
        self.assertLessEqual(right, 1.0)
        self.assertLessEqual(bottom, 1.0)
        self.assertGreaterEqual(right - left, 0.20)
        self.assertGreaterEqual(bottom - top, 0.20)

    def test_settings_support_mouse_preview_and_keyboard_numeric_alternative(self) -> None:
        preview = QImage(320, 480, QImage.Format_ARGB32_Premultiplied)
        preview.fill(QColor(0, 0, 0, 0))
        settings = self._track(Live2DViewportSettings(preview=preview))
        settings.set_window_mask(
            {
                "enabled": True,
                "cx": 0.54,
                "cy": 0.40,
                "rw": 0.30,
                "rh": 0.40,
            }
        )

        self.assertTrue(settings.crop_enabled.isChecked())
        self.assertAlmostEqual(settings.left_input.value(), 24.0)
        self.assertAlmostEqual(settings.top_input.value(), 0.0)
        self.assertAlmostEqual(settings.right_input.value(), 84.0)
        self.assertAlmostEqual(settings.bottom_input.value(), 80.0)
        self.assertTrue(settings.editor.has_preview())
        self.assertTrue(settings.editor.accessibleName())
        self.assertIn("方向键", settings.editor.accessibleDescription())

        # 数值输入是拖拽框选的等价键盘路径。
        settings.left_input.setValue(30.0)
        saved = settings.window_mask()
        self.assertAlmostEqual(saved["cx"], 0.57)
        self.assertAlmostEqual(saved["rw"], 0.27)
        self.assertEqual(saved["cy"], 0.40)
        self.assertEqual(saved["rh"], 0.40)

    def test_full_canvas_action_is_explicit_and_reversible(self) -> None:
        settings = self._track(Live2DViewportSettings())
        settings.set_window_mask(
            {
                "enabled": True,
                "cx": 0.50,
                "cy": 0.50,
                "rw": 0.25,
                "rh": 0.30,
            }
        )

        settings.full_canvas_button.click()

        self.assertEqual(
            settings.window_mask(),
            {
                "enabled": True,
                "cx": 0.50,
                "cy": 0.50,
                "rw": 0.50,
                "rh": 0.50,
            },
        )
        self.assertIn("完整画布", settings.status_label.text())

    def test_wizard_patches_only_live2d_window_mask(self) -> None:
        from wizard.app import SetupWizard

        initial = {
            "display": {"size_factor": 1.0},
            "live2d": {
                "enabled": True,
                "model_dir": "D:/models/mea",
                "custom_live2d_key": "keep-me",
                "window_mask": {
                    "enabled": True,
                    "cx": 0.54,
                    "cy": 0.40,
                    "rw": 0.30,
                    "rh": 0.40,
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            wizard = self._track(
                SetupWizard(
                    config_path=Path(directory) / "config.json",
                    initial_config=initial,
                )
            )
            wizard._load_timer.stop()
            wizard.env_page._check_timer.stop()
            for timer in wizard.tts_page._startup_timers:
                timer.stop()

            wizard.live2d_viewport_settings.left_input.setValue(28.0)
            config = wizard.collect_config()

        self.assertEqual(config["live2d"]["model_dir"], "D:/models/mea")
        self.assertEqual(config["live2d"]["custom_live2d_key"], "keep-me")
        self.assertAlmostEqual(config["live2d"]["window_mask"]["cx"], 0.56)
        self.assertAlmostEqual(config["live2d"]["window_mask"]["rw"], 0.28)

    def test_runtime_reapplies_viewport_when_only_mask_changes(self) -> None:
        host = _RuntimeConfigHost()
        updated = {
            "display": {"size_factor": 1.0},
            "live2d": {
                "enabled": True,
                "window_mask": {
                    "enabled": True,
                    "cx": 0.54,
                    "cy": 0.40,
                    "rw": 0.30,
                    "rh": 0.40,
                },
            },
        }

        self.assertTrue(host._apply_runtime_config(updated))
        self.assertEqual(host.viewport_apply_count, 1)

    def test_render_host_captures_a_bounded_full_canvas_preview(self) -> None:
        frame = QImage(200, 300, QImage.Format_ARGB32_Premultiplied)
        frame.fill(QColor(255, 157, 190, 160))

        class WidgetStub:
            def width(self) -> int:
                return frame.width()

            def height(self) -> int:
                return frame.height()

            def grabFramebuffer(self):
                return frame

        host = type(
            "PreviewHost",
            (),
            {"_use_live2d": True, "sprite_label": WidgetStub()},
        )()

        captured = PetRenderHostMixin._capture_live2d_viewport_preview(host)

        self.assertIsNotNone(captured)
        self.assertEqual((captured.width(), captured.height()), (200, 300))
        self.assertIsNot(captured, frame)


if __name__ == "__main__":
    unittest.main()
