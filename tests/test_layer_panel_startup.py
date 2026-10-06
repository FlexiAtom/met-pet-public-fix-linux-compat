"""启动默认态：桌宠留在**交互态**，穿透由面板那一个入口发起。

人工裁决 2026-10-03「先把启动默认进入穿透改为非穿透」。这一族钉两件事：

1. `_init_layer_overlay_mode()` 一次 `enable()` 都不发——量完再挂（fidus）那条路跟着它
   一起从启动路径上摘掉：启动即抓帧、即测量、量不出还可能弹模态，那些都不该发生在
   用户什么都没做的时候。
2. 面板的**出场文案跟着真实状态走**。它是切回交互态的唯一入口，一出场就说「点击穿透：开」
   等于让出口说谎——比没有出口更糟。

`_init_layer_overlay_mode()` 的守卫读 `QGuiApplication.platformName()`、`wayland_layer.is_available()`，
函数体又 `QTimer(self)` 与 `LayerDebugPanel(...)`，所以这里三样都遮：QTimer 换成假定时器
（假 host 不是 QObject，真 `QTimer(self)` 会 TypeError），面板**不**换——要看的就是它的真文案。
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)


class FakeTimer:
    def __init__(self, _parent=None):
        self.active = False

    def setInterval(self, ms):
        self.interval = ms

    def connect(self, *_args, **_kwargs):
        pass

    @property
    def timeout(self):
        return self

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


class FakeBackend:
    def __init__(self):
        self.enabled = []
        self.destroyed = 0

    def enable(self, _screen, w, h, x, y):
        self.enabled.append((x, y, w, h))
        return True

    def disable(self):
        self.destroyed += 1

    destroy_context = disable


class TestStartupStaysInteractive(unittest.TestCase):
    def _host(self):
        import meapet.desktop.render_host as RH

        host = object.__new__(RH.PetRenderHostMixin)
        host.backend = FakeBackend()
        host.config = {"fidus": {"enabled": True}}
        host.sprite_label = None
        host._layer_inited = False
        host._layer_window_pos = None
        host._layer_timer = None
        host._pending_mount = None
        host._fidus_busy = False
        host.calls = []
        host._position_layer_panel = lambda: None
        host._set_layer_mode = lambda penetrate: host.calls.append(penetrate)
        return host, RH

    def _init(self, host, RH):
        import meapet.desktop.wayland_layer as WL

        class Gui:                                # 函数级 import ⇒ 遮 `PyQt5.QtGui` 里那个名字
            @staticmethod
            def platformName():
                return "wayland"

        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch("PyQt5.QtGui.QGuiApplication", Gui), \
                mock.patch.object(WL, "is_available", lambda: True), \
                mock.patch.object(WL, "get_backend", lambda: host.backend), \
                mock.patch.object(RH, "QTimer", FakeTimer):
            host._init_layer_overlay_mode()

    def test_startup_emits_no_enable_and_no_layer_mount(self):
        host, RH = self._host()
        self._init(host, RH)
        self.assertEqual(host.calls, [], "启动不该调 `_set_layer_mode`——穿透是面板发起的")
        self.assertEqual(host.backend.enabled, [], "启动一次 `enable()` 都不许发")
        self.assertFalse(host._layer_timer.active, "推帧定时器建好但不启动")
        self.assertIs(host._layer_panel._on_toggle, host._set_layer_mode,
                      "面板手里那一个入口就是 `_set_layer_mode`")

    def test_panel_opens_saying_interactive(self):
        host, RH = self._host()
        self._init(host, RH)
        panel = host._layer_panel
        self.assertIs(panel._penetrate, False)
        self.assertEqual(panel._label.text(), "点击穿透：关")
        self.assertEqual(panel._btn.text(), "切到 穿透")

    def test_first_click_on_panel_is_the_mount(self):
        """面板第一次 `_toggle` 才是挂载：它翻成 `True` 后把同一个值交给 `_set_layer_mode`。"""
        host, RH = self._host()
        self._init(host, RH)
        host._layer_panel._toggle()
        self.assertEqual(host.calls, [True])
        self.assertEqual(host._layer_panel._label.text(), "点击穿透：开")
        host._layer_panel._toggle()
        self.assertEqual(host.calls, [True, False])
        self.assertEqual(host._layer_panel._label.text(), "点击穿透：关")


class TestPNGModeReachesTheMount(unittest.TestCase):
    """PNG 模式的常驻穿透（工作项 #154，人工 2026-10-06「检查 PNG 模式的点击穿透，
    如果没做需要做」；同一条把穿透钉成**整窗**不可点，热区是另外的功能）。

    原先的失效形态：挂载链只装在 `_on_live2d_first_frame` 上，PNG 分支没有那个回调
    ⇒ 面板、`_layer_backend`、推帧定时器在 PNG 模式下**根本不存在**，而待机链那两行
    `enable_click_through` 又让人以为"PNG 也支持穿透"。这里钉三件事：
    ① 渲染器就绪就建得出面板（与模式无关）；② PNG 的像素来源是它手上那份后备帧；
    ③ PNG 拿不到离屏帧 ⇒ 挂载按整画布请求，不去解 alpha 轮廓。
    """

    def _host(self):
        import meapet.desktop.render_host as RH

        host = object.__new__(RH.PetRenderHostMixin)
        host.backend = FakeBackend()
        host.config = {}
        host.sprite_label = None
        host._layer_inited = False
        host._layer_window_pos = None
        host._layer_timer = None
        host._pending_mount = None
        host._fidus_busy = False
        host.calls = []
        host._position_layer_panel = lambda: None
        host._set_layer_mode = lambda penetrate: host.calls.append(penetrate)
        host._renderer_ready = False
        host._renderer_ready_callbacks = []
        return host, RH

    def _mark_ready(self, host):
        import meapet.desktop.render_host as RH
        import meapet.desktop.wayland_layer as WL

        class Gui:                                # 函数级 import ⇒ 遮 `PyQt5.QtGui` 里那个名字
            @staticmethod
            def platformName():
                return "wayland"

        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch("PyQt5.QtGui.QGuiApplication", Gui), \
                mock.patch.object(WL, "is_available", lambda: True), \
                mock.patch.object(WL, "get_backend", lambda: host.backend), \
                mock.patch.object(RH, "QTimer", FakeTimer):
            host._mark_renderer_ready()

    def test_renderer_ready_builds_the_panel_without_live2d(self):
        """PNG 那一支只有 `_mark_renderer_ready()`：它必须建得出面板与后端。"""
        host, _RH = self._host()
        self._mark_ready(host)
        self.assertIsNotNone(getattr(host, "_layer_panel", None),
                             "就绪了却没有出口 ⇒ 用户永远切不进穿透")
        self.assertIs(host._layer_backend, host.backend)
        self.assertFalse(host._layer_timer.active, "建好但不推帧：穿透仍由面板发起")
        self.assertEqual(host.backend.enabled, [], "就绪不该顺手挂一发")

    def test_reaching_ready_twice_does_not_build_a_second_panel(self):
        host, _RH = self._host()
        self._mark_ready(host)
        panel = host._layer_panel
        host._renderer_ready = False
        self._mark_ready(host)
        self.assertIs(host._layer_panel, panel, "两只开关窗口 = 两个入口在抢同一个后端")

    def test_png_pushes_its_backing_frame(self):
        """PNG 没有 `render_offscreen`：推帧取的是 `SpriteCanvas` 手上那份后备帧。"""
        from PyQt5.QtGui import QColor, QPixmap
        from meapet.desktop.renderer import SpriteCanvas

        host, _RH = self._host()
        pushed = []
        host._layer_backend = host.backend
        host.backend.update_pixels = pushed.append
        host._layer_fit_service = lambda: False
        canvas = SpriteCanvas()
        frame = QPixmap(7, 5)
        frame.fill(QColor(1, 2, 3, 255))
        canvas.set_frame(frame)
        host.sprite_label = canvas
        self._push(host)

        self.assertEqual(len(pushed), 1, "PNG 模式一帧都没推 ⇒ 面板挂着而屏上是空的")
        img = pushed[0]
        self.assertFalse(img.isNull())
        self.assertEqual((img.width(), img.height()), (7, 5))
        self.assertEqual(img.pixelColor(0, 0).rgba(), QColor(1, 2, 3, 255).rgba())
        canvas.deleteLater()

    def test_png_mounts_the_whole_canvas_and_says_why(self):
        """整窗那一档：PNG 解不出轮廓就不该去解——按画布矩形请求，并且**出声**。"""
        host, _RH = self._host()
        host.sprite_label = type("PngWidget", (), {})()   # 真 PNG 控件没有 render_offscreen
        region = host._layer_surface_region(461, 614)
        self.assertEqual(region, (0, 0, 461, 614), "按 alpha 解轮廓＝偷做热区")
        self.assertIn("量不到轮廓", self._surface_log(host))

    @staticmethod
    def _push(host):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            host._push_layer_frame()
        return err.getvalue()

    @staticmethod
    def _surface_log(host):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            host._layer_surface_region(461, 614)
        return err.getvalue()


class TestModeSwitchCancelsThePendingMeasure(unittest.TestCase):
    """切渲染模式时把在飞的「量完再挂」作废：量的是即将被删的那只 widget。"""

    def test_cancel_runs_before_the_new_renderer_is_built(self):
        import meapet.desktop.render_host as RH

        host = object.__new__(RH.PetRenderHostMixin)
        host.calls = []
        host._use_live2d = True
        host._pending_mount = (1, 2, 3, 4)
        host.config = {}

        class Widget:
            def shutdown(self):
                pass

            def hide(self):
                pass

            def deleteLater(self):
                pass

        host.sprite_label = Widget()
        host._l2d_model = object()
        host._live2d_startup_widget = None
        host._cancel_live2d_startup_timeout = lambda: None
        host._cancel_premount_measure = lambda: host.calls.append("cancel")
        host._init_png_renderer = lambda: host.calls.append("png")
        host._show_bubble = lambda *_a, **_k: None
        host._save_config = lambda: None
        host._standby = False
        host._toggle_render_mode()
        self.assertEqual(host.calls, ["cancel", "png"],
                         "先作废再换画布，否则那一发的读数与挂载矩形落到新画布上")


if __name__ == "__main__":
    unittest.main()
