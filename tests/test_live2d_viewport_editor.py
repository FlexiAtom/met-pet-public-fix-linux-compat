"""Live2D 窗口视口编辑器与热应用的用户路径回归测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QImage, QMouseEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

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

    @staticmethod
    def _drag(widget, start: QPoint, end: QPoint) -> None:
        QApplication.sendEvent(
            widget,
            QMouseEvent(
                QEvent.MouseButtonPress,
                QPointF(start),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            ),
        )
        QApplication.sendEvent(
            widget,
            QMouseEvent(
                QEvent.MouseMove,
                QPointF(end),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            ),
        )
        QApplication.sendEvent(
            widget,
            QMouseEvent(
                QEvent.MouseButtonRelease,
                QPointF(end),
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            ),
        )

    @staticmethod
    def _click(widget, point: QPoint) -> None:
        QTest.mouseClick(
            widget,
            Qt.LeftButton,
            Qt.NoModifier,
            point,
        )

    def _draw_shape_contour(
        self,
        settings,
        operation: str,
        points: tuple[tuple[float, float], ...],
    ) -> None:
        button = (
            settings.shape_add_button
            if operation == "add"
            else settings.shape_subtract_button
        )
        button.click()
        for x, y in points:
            self._click(
                settings.editor,
                settings.editor._canvas_point(QPointF(x, y)).toPoint(),
            )
        self.assertTrue(settings.shape_finish_button.isEnabled())
        settings.shape_finish_button.click()

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

    def test_placement_anchor_defaults_to_the_visible_viewport_bottom_center(
        self,
    ) -> None:
        from meapet.config.store import normalize_live2d_placement_anchor

        mask = {
            "enabled": True,
            "cx": 0.62,
            "cy": 0.35,
            "rw": 0.24,
            "rh": 0.42,
        }

        self.assertEqual(
            normalize_live2d_placement_anchor(None, mask),
            {"x": 0.62, "y": 0.77},
        )
        self.assertEqual(
            normalize_live2d_placement_anchor(
                {"x": -4, "y": 7},
                mask,
            ),
            {"x": 0.0, "y": 1.0},
        )
        self.assertEqual(
            normalize_live2d_placement_anchor(
                {"x": "bad", "y": float("nan")},
                mask,
            ),
            {"x": 0.62, "y": 0.77},
        )

        full_canvas = dict(mask, enabled=False)
        self.assertEqual(
            normalize_live2d_placement_anchor(None, full_canvas),
            {"x": 0.5, "y": 1.0},
        )

    def test_window_shape_normalization_keeps_safe_add_and_subtract_contours(
        self,
    ) -> None:
        from meapet.config.store import normalize_live2d_window_shape

        normalized = normalize_live2d_window_shape(
            {
                "enabled": True,
                "contours": [
                    {
                        "operation": "add",
                        "points": [
                            [-1, 0.2],
                            [1.4, 0.2],
                            [0.5, 1.3],
                            [-1, 0.2],
                        ],
                    },
                    {
                        "operation": "subtract",
                        "points": [[0.4, 0.4], [0.6, 0.4], [0.5, 0.7]],
                    },
                    {
                        "operation": "invalid",
                        "points": [[0, 0], [1, 0], [0, 1]],
                    },
                    {"operation": "add", "points": [[0, 0], [1, 1]]},
                ],
            }
        )

        self.assertEqual(
            normalized,
            {
                "enabled": True,
                "contours": [
                    {
                        "operation": "add",
                        "points": [[0.0, 0.2], [1.0, 0.2], [0.5, 1.0]],
                    },
                    {
                        "operation": "subtract",
                        "points": [[0.4, 0.4], [0.6, 0.4], [0.5, 0.7]],
                    },
                ],
            },
        )
        self.assertEqual(
            normalize_live2d_window_shape(None),
            {"enabled": False, "contours": []},
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
        self.assertGreaterEqual(right - left, 0.20 - 1e-9)
        self.assertGreaterEqual(bottom - top, 0.20 - 1e-9)

        malformed = constrain_viewport_edges("bad", float("nan"), None, 4)
        self.assertEqual(malformed, (0.0, 0.0, 0.2, 1.0))

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

        settings.set_placement_anchor({"x": 0.53, "y": 0.86})
        self.assertAlmostEqual(settings.anchor_x_input.value(), 53.0)
        self.assertAlmostEqual(settings.anchor_y_input.value(), 86.0)
        self.assertEqual(
            settings.placement_anchor(),
            {"x": 0.53, "y": 0.86},
        )

    def test_editor_drag_moves_the_model_anchor_without_moving_the_viewport(
        self,
    ) -> None:
        settings = self._track(Live2DViewportSettings())
        settings.resize(700, 680)
        settings.show()
        QApplication.processEvents()
        settings.set_placement_anchor({"x": 0.50, "y": 0.80})
        editor = settings.editor
        before_viewport = editor.viewport()
        start = editor._anchor_point().toPoint()

        self._drag(editor, start, start + QPoint(20, -18))

        anchor = settings.placement_anchor()
        self.assertGreater(anchor["x"], 0.50)
        self.assertLess(anchor["y"], 0.80)
        self.assertEqual(editor.viewport(), before_viewport)
        self.assertIn("站立锚点", settings.status_label.text())

        # 不切换模式即可继续调整窗口边界；调整边界也不改写锚点。
        corner = editor._selection_rect().bottomRight().toPoint()
        self._drag(editor, corner, corner + QPoint(12, 12))
        self.assertGreater(editor.viewport()[2], before_viewport[2])
        self.assertEqual(settings.placement_anchor(), anchor)

    def test_anchor_remains_editable_when_viewport_crop_is_disabled(self) -> None:
        settings = self._track(Live2DViewportSettings())

        settings.crop_enabled.setChecked(False)
        settings.anchor_x_input.setValue(47.0)
        settings.anchor_y_input.setValue(92.0)

        self.assertTrue(settings.editor.isEnabled())
        self.assertTrue(settings.anchor_x_input.isEnabled())
        self.assertTrue(settings.anchor_y_input.isEnabled())
        self.assertEqual(
            settings.placement_anchor(),
            {"x": 0.47, "y": 0.92},
        )

    def test_editor_draws_disconnected_keep_regions_and_a_cutout(self) -> None:
        settings = self._track(Live2DViewportSettings())
        settings.resize(800, 1040)
        settings.show()
        QApplication.processEvents()
        settings.shape_enabled.setChecked(True)

        self._draw_shape_contour(
            settings,
            "add",
            ((0.25, 0.18), (0.48, 0.18), (0.42, 0.52), (0.28, 0.48)),
        )
        self._draw_shape_contour(
            settings,
            "add",
            ((0.58, 0.58), (0.72, 0.58), (0.66, 0.78)),
        )
        self._draw_shape_contour(
            settings,
            "subtract",
            ((0.32, 0.27), (0.40, 0.27), (0.36, 0.38)),
        )

        shape = settings.window_shape()
        self.assertTrue(shape["enabled"])
        self.assertEqual(
            [contour["operation"] for contour in shape["contours"]],
            ["add", "add", "subtract"],
        )
        self.assertIn("2 个保留区", settings.shape_status_label.text())
        self.assertIn("1 个挖空区", settings.shape_status_label.text())

        settings.shape_undo_button.click()
        self.assertEqual(
            [
                contour["operation"]
                for contour in settings.window_shape()["contours"]
            ],
            ["add", "add"],
        )

        settings.shape_clear_button.click()
        self.assertEqual(
            settings.window_shape(),
            {"enabled": True, "contours": []},
        )

    def test_shape_can_be_disabled_without_losing_drawn_contours(self) -> None:
        settings = self._track(Live2DViewportSettings())
        shape = {
            "enabled": True,
            "contours": [
                {
                    "operation": "add",
                    "points": [[0.2, 0.1], [0.8, 0.1], [0.5, 0.9]],
                }
            ],
        }

        settings.set_window_shape(shape)
        settings.shape_enabled.setChecked(False)

        saved = settings.window_shape()
        self.assertFalse(saved["enabled"])
        self.assertEqual(saved["contours"], shape["contours"])
        self.assertFalse(settings.shape_add_button.isEnabled())

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

    def test_editor_supports_keyboard_movement_without_changing_size(self) -> None:
        settings = self._track(Live2DViewportSettings())
        editor = settings.editor
        editor.setEnabled(True)
        editor.setFocus()
        before = editor.viewport()

        QTest.keyClick(editor, Qt.Key_Right)
        QTest.keyClick(editor, Qt.Key_Down, Qt.ShiftModifier)

        after = editor.viewport()
        self.assertAlmostEqual(after[0], before[0] + 0.01)
        self.assertAlmostEqual(after[1], before[1] + 0.05)
        self.assertAlmostEqual(after[2] - after[0], before[2] - before[0])
        self.assertAlmostEqual(after[3] - after[1], before[3] - before[1])

    def test_editor_drag_moves_the_window_rectangle_without_resizing_it(self) -> None:
        settings = self._track(Live2DViewportSettings())
        settings.resize(700, 620)
        settings.show()
        QApplication.processEvents()
        editor = settings.editor
        before = editor.viewport()
        start = editor._selection_rect().center().toPoint()
        self._drag(editor, start, start + QPoint(18, 14))

        after = editor.viewport()
        self.assertGreater(after[0], before[0])
        self.assertGreater(after[1], before[1])
        self.assertAlmostEqual(after[2] - after[0], before[2] - before[0])
        self.assertAlmostEqual(after[3] - after[1], before[3] - before[1])

    def test_editor_corner_handle_resizes_the_window_rectangle(self) -> None:
        settings = self._track(Live2DViewportSettings())
        settings.resize(700, 620)
        settings.show()
        QApplication.processEvents()
        editor = settings.editor
        before = editor.viewport()
        start = editor._selection_rect().bottomRight().toPoint()

        self._drag(editor, start, start + QPoint(14, 18))

        after = editor.viewport()
        self.assertEqual(after[:2], before[:2])
        self.assertGreater(after[2], before[2])
        self.assertGreater(after[3], before[3])

    def test_editor_can_draw_a_new_rectangle_in_the_dimmed_canvas(self) -> None:
        settings = self._track(Live2DViewportSettings())
        settings.resize(700, 620)
        settings.show()
        QApplication.processEvents()
        editor = settings.editor
        canvas = editor._canvas_rect()
        start = QPoint(
            round(canvas.left() + canvas.width() * 0.05),
            round(canvas.top() + canvas.height() * 0.95),
        )
        end = QPoint(
            round(canvas.left() + canvas.width() * 0.45),
            round(canvas.top() + canvas.height() * 0.55),
        )

        self._drag(editor, start, end)

        left, top, right, bottom = editor.viewport()
        self.assertAlmostEqual(left, 0.05, delta=0.02)
        self.assertAlmostEqual(top, 0.55, delta=0.02)
        self.assertAlmostEqual(right, 0.45, delta=0.02)
        self.assertAlmostEqual(bottom, 0.95, delta=0.02)

    def test_disabling_crop_retains_edges_but_uses_complete_canvas_at_runtime(self) -> None:
        settings = self._track(Live2DViewportSettings())
        before = settings.editor.viewport()

        settings.crop_enabled.setChecked(False)

        # 完整画布模式仍允许在预览中校准模型站立锚点。
        self.assertTrue(settings.editor.isEnabled())
        self.assertFalse(settings.left_input.isEnabled())
        self.assertFalse(settings.window_mask()["enabled"])
        self.assertEqual(settings.editor.viewport(), before)
        self.assertIn("完整画布", settings.status_label.text())

    def test_wizard_patches_only_live2d_viewport_fields(self) -> None:
        from wizard.app import SetupWizard

        initial = {
            "display": {"size_factor": 1.0},
            "live2d": {
                "enabled": True,
                "model_dir": "D:/models/mea",
                "custom_live2d_key": "keep-me",
                "placement_anchor": {"x": 0.52, "y": 0.88},
                "window_shape": {"enabled": False, "contours": []},
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
            wizard.live2d_viewport_settings.anchor_x_input.setValue(57.0)
            wizard.live2d_viewport_settings.set_window_shape(
                {
                    "enabled": True,
                    "contours": [
                        {
                            "operation": "add",
                            "points": [
                                [0.28, 0.10],
                                [0.82, 0.10],
                                [0.76, 0.82],
                                [0.32, 0.82],
                            ],
                        }
                    ],
                }
            )
            config = wizard.collect_config()

        self.assertEqual(config["live2d"]["model_dir"], "D:/models/mea")
        self.assertEqual(config["live2d"]["custom_live2d_key"], "keep-me")
        self.assertAlmostEqual(config["live2d"]["window_mask"]["cx"], 0.56)
        self.assertAlmostEqual(config["live2d"]["window_mask"]["rw"], 0.28)
        self.assertEqual(
            config["live2d"]["placement_anchor"],
            {"x": 0.57, "y": 0.88},
        )
        self.assertEqual(
            config["live2d"]["window_shape"],
            {
                "enabled": True,
                "contours": [
                    {
                        "operation": "add",
                        "points": [
                            [0.28, 0.10],
                            [0.82, 0.10],
                            [0.76, 0.82],
                            [0.32, 0.82],
                        ],
                    }
                ],
            },
        )

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

    def test_runtime_does_not_reapply_an_unchanged_viewport(self) -> None:
        host = _RuntimeConfigHost()

        self.assertTrue(host._apply_runtime_config(host.config))
        self.assertEqual(host.viewport_apply_count, 0)

    def test_runtime_reapplies_geometry_when_only_placement_anchor_changes(
        self,
    ) -> None:
        host = _RuntimeConfigHost()
        host.config["live2d"]["placement_anchor"] = {"x": 0.50, "y": 0.90}
        updated = {
            **host.config,
            "live2d": {
                **host.config["live2d"],
                "placement_anchor": {"x": 0.58, "y": 0.84},
            },
        }

        self.assertTrue(host._apply_runtime_config(updated))
        self.assertEqual(host.viewport_apply_count, 1)

    def test_runtime_reapplies_window_region_when_only_shape_changes(self) -> None:
        host = _RuntimeConfigHost()
        host.config["live2d"]["window_shape"] = {
            "enabled": False,
            "contours": [],
        }
        updated = {
            **host.config,
            "live2d": {
                **host.config["live2d"],
                "window_shape": {
                    "enabled": True,
                    "contours": [
                        {
                            "operation": "add",
                            "points": [[0.2, 0.1], [0.8, 0.1], [0.5, 0.9]],
                        }
                    ],
                },
            },
        }

        self.assertTrue(host._apply_runtime_config(updated))
        self.assertEqual(host.viewport_apply_count, 1)

    def test_anchor_reposition_keeps_a_canvas_point_fixed_across_geometry_changes(
        self,
    ) -> None:
        from meapet.desktop.render_host import (
            calculate_live2d_anchor_preserving_position,
        )

        target = calculate_live2d_anchor_preserving_position(
            QPoint(200, 100),
            QRect(-100, -20, 1000, 800),
            QRect(-250, -80, 500, 400),
            {"x": 0.60, "y": 0.90},
        )

        self.assertEqual(target, QPoint(650, 520))

    def test_custom_shape_builds_a_clipped_region_with_a_real_hole(self) -> None:
        from meapet.desktop.render_host import (
            calculate_live2d_viewport_layout,
            calculate_live2d_window_region,
        )

        layout = calculate_live2d_viewport_layout(
            1000,
            1000,
            0.3,
            {
                "enabled": True,
                "cx": 0.50,
                "cy": 0.50,
                "rw": 0.30,
                "rh": 0.40,
            },
        )
        shape = {
            "enabled": True,
            "contours": [
                {
                    "operation": "add",
                    "points": [[0.2, 0.1], [0.8, 0.1], [0.8, 0.9], [0.2, 0.9]],
                },
                {
                    "operation": "subtract",
                    "points": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]],
                },
            ],
        }

        region = calculate_live2d_window_region(layout, shape)

        self.assertIsNotNone(region)
        self.assertEqual(region.boundingRect(), QRect(0, 0, 180, 240))
        self.assertTrue(region.contains(QPoint(10, 10)))
        self.assertFalse(region.contains(QPoint(90, 120)))
        self.assertIsNone(
            calculate_live2d_window_region(
                layout,
                {**shape, "enabled": False},
            )
        )

    def test_render_host_applies_custom_region_and_clears_it_for_png(self) -> None:
        class CanvasModel:
            def get_suggested_size(self):
                return 1000, 1000

        class Host(PetRenderHostMixin, QWidget):
            pass

        host = self._track(Host())
        host.config = {
            "live2d": {
                "window_mask": {
                    "enabled": True,
                    "cx": 0.50,
                    "cy": 0.50,
                    "rw": 0.30,
                    "rh": 0.40,
                },
                "window_shape": {
                    "enabled": True,
                    "contours": [
                        {
                            "operation": "add",
                            "points": [
                                [0.2, 0.1],
                                [0.8, 0.1],
                                [0.8, 0.9],
                                [0.2, 0.9],
                            ],
                        },
                        {
                            "operation": "subtract",
                            "points": [
                                [0.4, 0.4],
                                [0.6, 0.4],
                                [0.6, 0.6],
                                [0.4, 0.6],
                            ],
                        },
                    ],
                },
            }
        }
        host._use_live2d = True
        host._l2d_model = CanvasModel()
        host.sprite_label = QWidget(host)

        host._apply_live2d_viewport_geometry(0.3)

        self.assertFalse(host.mask().isEmpty())
        self.assertFalse(host.mask().contains(QPoint(90, 120)))

        host._use_live2d = False
        host._apply_hit_region()
        self.assertTrue(host.mask().isEmpty())

    def test_hot_apply_resizes_parent_but_keeps_complete_child_canvas(self) -> None:
        class CanvasModel:
            def get_suggested_size(self):
                return 1000, 800

        class Host(PetRenderHostMixin, QWidget):
            def __init__(self):
                super().__init__()
                self.config = {
                    "live2d": {
                        "placement_anchor": {"x": 0.62, "y": 0.91},
                        "window_mask": {
                            "enabled": True,
                            "cx": 0.50,
                            "cy": 0.50,
                            "rw": 0.25,
                            "rh": 0.40,
                        }
                    }
                }
                self._use_live2d = True
                self._size_factor = 0.5
                self._l2d_model = CanvasModel()
                self.sprite_label = QWidget(self)
                self.bubble_positions = 0

            def _position_bubble(self, **_kwargs) -> None:
                self.bubble_positions += 1

        host = self._track(Host())
        host.resize(500, 400)
        host.move(200, 100)
        host.sprite_label.setGeometry(0, 0, 500, 400)
        anchor = host.config["live2d"]["placement_anchor"]

        def global_anchor() -> QPoint:
            canvas = host.sprite_label.geometry()
            return host.pos() + QPoint(
                canvas.x() + round(canvas.width() * anchor["x"]),
                canvas.y() + round(canvas.height() * anchor["y"]),
            )

        before_anchor = global_anchor()

        self.assertTrue(host._apply_live2d_viewport_preference())

        self.assertEqual(
            (
                host.sprite_label.x(),
                host.sprite_label.y(),
                host.sprite_label.width(),
                host.sprite_label.height(),
            ),
            (-125, -40, 500, 400),
        )
        self.assertEqual((host.width(), host.height()), (250, 320))
        self.assertEqual(global_anchor(), before_anchor)
        self.assertEqual(host.bubble_positions, 1)

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

    def test_render_host_skips_preview_when_full_canvas_is_too_large(self) -> None:
        class WidgetStub:
            grab_count = 0

            def width(self) -> int:
                return 4000

            def height(self) -> int:
                return 3000

            def grabFramebuffer(self):
                self.grab_count += 1
                return QImage()

        widget = WidgetStub()
        host = type(
            "PreviewHost",
            (),
            {"_use_live2d": True, "sprite_label": widget},
        )()

        self.assertIsNone(
            PetRenderHostMixin._capture_live2d_viewport_preview(host)
        )
        self.assertEqual(widget.grab_count, 0)


if __name__ == "__main__":
    unittest.main()
