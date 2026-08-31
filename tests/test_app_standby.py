"""app.py 新增待机点击穿透集成方法的验证测试。

用一个「协议替身」(duck-typed stand-in) 复刻 MeaPet.__init__ 里初始化的
相关属性，并把 4 个新方法以 bound-method 方式绑定到该对象上做验证。

meapet 包通过 stubs/meapet/__init__.py 提供（使 app.py 可导入）；
click_through / capture 使用 out/ 下重构后的真实模块。
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

# stubs 必须在 app 之前导入，保证 meapet 包已就绪
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "stubs"))
sys.path.insert(0, os.path.join(_HERE, "out"))

import click_through as click_through_mod  # noqa: E402
from PyQt5.QtCore import QTimer  # noqa: E402

from click_through import (  # noqa: E402
    ClickThroughState,
    RightClickEdgeDetector,
)
from app import MeaPet  # type: ignore  # noqa: E402


def _make_standin(width=200, height=300):
    s = types.SimpleNamespace()
    s._click_through_state = ClickThroughState()
    s._standby = False
    s._standby_menu_open = False
    s._standby_rc_timer = None
    s._standby_rc_detector = RightClickEdgeDetector()
    s._qt_transparent_for_input = False
    s._show_context_menu = mock.MagicMock()
    s.width = lambda: width
    s.height = lambda: height
    s.log = mock.MagicMock()
    return s


def _bind(obj, method_name):
    return getattr(MeaPet, method_name).__get__(obj)


class ApplyHitRegionTests(unittest.TestCase):
    def test_noop_when_inactive(self):
        s = _make_standin()
        with mock.patch("click_through.set_shape_region") as m:
            _bind(s, "_apply_hit_region")()
        m.assert_not_called()

    def test_sends_full_window_rect_when_active(self):
        s = _make_standin(120, 80)
        s._click_through_state = ClickThroughState(active=True, backend="x11", hwnd=1)
        with mock.patch("click_through.set_shape_region") as m:
            _bind(s, "_apply_hit_region")()
        self.assertTrue(m.called)
        rects_arg = m.call_args[0][1]
        self.assertEqual(rects_arg, [(0, 0, 120, 80)])


class EnsureStandbyTests(unittest.TestCase):
    def test_enable_on_standby(self):
        s = _make_standin()
        s._standby = True
        with mock.patch("click_through.enable_click_through") as enable, \
                mock.patch("click_through.disable_click_through") as disable, \
                mock.patch.object(MeaPet, "_apply_hit_region") as apply_hit, \
                mock.patch.object(MeaPet, "_start_standby_right_click_poll") as start:
            enable.return_value = ClickThroughState(active=True, backend="x11", hwnd=1)
            _bind(s, "_ensure_standby_click_through")()
        enable.assert_called_once()
        self.assertIs(enable.call_args[0][0], s)  # 传 self (QWidget)，非 int
        self.assertTrue(s._qt_transparent_for_input)
        apply_hit.assert_called_once_with(s)
        start.assert_called_once_with(s)
        disable.assert_not_called()

    def test_disable_when_not_standby(self):
        s = _make_standin()
        s._standby = False
        s._click_through_state = ClickThroughState(active=True, backend="x11", hwnd=1)
        with mock.patch("click_through.enable_click_through") as enable, \
                mock.patch("click_through.disable_click_through") as disable, \
                mock.patch.object(MeaPet, "_stop_standby_right_click_poll") as stop:
            _bind(s, "_ensure_standby_click_through")()
        disable.assert_called_once()
        enable.assert_not_called()
        self.assertFalse(s._qt_transparent_for_input)
        stop.assert_called_once_with(s)

    def test_menu_open_pauses_penetration(self):
        s = _make_standin()
        s._standby = True
        s._standby_menu_open = True
        s._click_through_state = ClickThroughState(active=True, backend="x11", hwnd=1)
        with mock.patch("click_through.disable_click_through") as disable:
            _bind(s, "_ensure_standby_click_through")()
        disable.assert_called_once()


class RightClickPollTests(unittest.TestCase):
    def test_wayland_backend_skips_poll(self):
        s = _make_standin()
        s._standby = True
        _bind(s, "_start_standby_right_click_poll")()
        self.assertIsNotNone(s._standby_rc_timer)
        try:
            with mock.patch("click_through.is_right_button_down") as rb, \
                    mock.patch("click_through.platform_backend_name",
                               return_value="wayland"):
                s._standby_rc_timer.timeout.emit()
            rb.assert_not_called()
        finally:
            _bind(s, "_stop_standby_right_click_poll")()

    def test_x11_backend_polls(self):
        s = _make_standin()
        s._standby = True
        _bind(s, "_start_standby_right_click_poll")()
        try:
            with mock.patch("click_through.is_right_button_down", return_value=False) as rb, \
                    mock.patch("click_through.platform_backend_name", return_value="x11"):
                s._standby_rc_timer.timeout.emit()
            rb.assert_called_once()
        finally:
            _bind(s, "_stop_standby_right_click_poll")()

    def test_stop_idempotent(self):
        s = _make_standin()
        _bind(s, "_stop_standby_right_click_poll")()
        self.assertIsNone(s._standby_rc_timer)


class OnStandbyRightClickTests(unittest.TestCase):
    def test_rising_edge_invokes_menu(self):
        s = _make_standin()
        s._standby_rc_detector = RightClickEdgeDetector()
        _bind(s, "_on_standby_right_click")()
        s._show_context_menu.assert_called_once()

    def test_no_fire_when_detector_blocks(self):
        s = _make_standin()
        s._standby_rc_detector = RightClickEdgeDetector()
        s._standby_rc_detector.update(cursor_in_pet=True, button_down=True)  # 先置为按下
        s._show_context_menu.reset_mock()
        _bind(s, "_on_standby_right_click")()
        s._show_context_menu.assert_not_called()


class BackendAutoDetectionTests(unittest.TestCase):
    def test_wayland_via_env(self):
        os.environ["XDG_SESSION_TYPE"] = "wayland"
        try:
            self.assertEqual(click_through_mod.platform_backend_name(), "wayland")
        finally:
            del os.environ["XDG_SESSION_TYPE"]

    def test_win32(self):
        self.assertEqual(click_through_mod.platform_backend_name("win32"), "win32")

    def test_x11(self):
        self.assertEqual(click_through_mod.platform_backend_name("xcb"), "x11")

    def test_wayland_right_button_always_false(self):
        self.assertFalse(click_through_mod.is_right_button_down(platform_name="wayland"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

