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


if __name__ == "__main__":
    unittest.main()
