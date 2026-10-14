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

    def isActive(self):
        """真 QTimer 有这一个符号：`_layer_penetrating()` 判的就是它。"""
        return self.active


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
        log = self._surface_log(host)
        self.assertIn("PNG 模式没有离屏渲染这一步", log)
        # 「离屏渲染不可用？」在那一侧指向一个不存在的开关（人工 2026-10-15 报的这句）
        self.assertNotIn("离屏渲染不可用", log)

    def test_live2d_names_its_own_reason_for_the_same_action(self):
        """同一动作、不同事实：Live2D 有离屏通道，拿不到帧是首帧未到，不能说成 PNG 那句。"""
        host, _RH = self._host()
        host._use_live2d = True
        host._fidus_current_frame = lambda: None
        region = host._layer_surface_region(461, 614)
        self.assertEqual(region, (0, 0, 461, 614))
        log = self._surface_log(host)
        self.assertIn("Live2D 离屏这一轮没给帧", log)
        self.assertIn("下一轮挂载再量轮廓", log)
        self.assertNotIn("PNG 模式没有离屏渲染这一步", log)

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
        host._remount_layer_after_render_switch = lambda reason: host.calls.append(
            ("remount", reason))
        host._toggle_render_mode()
        self.assertEqual(host.calls, ["cancel", "png", ("remount", "PNG")],
                         "先作废再换画布，否则那一发的读数与挂载矩形落到新画布上；"
                         "重挂排最后——挂载自己会 hide，落在中间会被后续 show 盖回来")


class TestEngineSwitchRemountsTheSurface(unittest.TestCase):
    """换渲染引擎时**重发一次挂载请求**（人工 2026-10-15「PNG切换到Live2D需要重新请求
    窗口大小，而不是直接切」）。

    L1 探针丙臂（`scripts/engine_switch_size_probe.py`）量的现状：穿透态里切引擎之后
    `enable_calls 总数=1`、`set_size 调用=[]`，configure 停在 PNG 那块的 130x260，而新画布
    是 366x512 ⇒ 新引擎每一帧都被裁进旧尺寸那块 surface。交互态没这一格（`resize()` 直接生效），
    所以缺的只有"重发 enable"这一步。
    """

    class Widget:
        def __init__(self, w, h, offset=(-88, 0)):
            from PyQt5.QtCore import QPoint

            self._size = (w, h)
            self._offset = QPoint(*offset)
            self._proxy_rect = None

        def width(self):
            return self._size[0]

        def height(self):
            return self._size[1]

        def mapTo(self, _parent, _point):
            return self._offset

    def _host(self, *, active=True, widget=None):
        import meapet.desktop.render_host as RH

        host = object.__new__(RH.PetRenderHostMixin)
        host.backend = FakeBackend()
        host._layer_backend = host.backend
        host.config = {}
        host._layer_window_pos = (10, 20)      # 进穿透前窗口还可见时记下的左上角
        host._layer_timer = FakeTimer()
        if active:
            host._layer_timer.start()
        host.sprite_label = widget if widget is not None else self.Widget(366, 512)
        host.calls = []
        host.hide = lambda: host.calls.append("hide")
        host._layer_fit_cancel = lambda: host.calls.append("fit-cancel")
        host._layer_fit_start = lambda *a, **k: host.calls.append(("fit", a, k))
        host._fidus_current_frame = lambda: None   # 量不出轮廓 ⇒ 整画布挂，尺寸最好认
        return host

    def test_interactive_switch_asks_for_nothing(self):
        """没挂着 surface 就没有要重发的东西：不该凭空建一只 layer。"""
        host = self._host(active=False)
        host._remount_layer_after_render_switch("Live2D")
        self.assertEqual(host.backend.enabled, [])
        self.assertEqual(host.calls, [])

    def test_remount_asks_the_compositor_for_the_new_canvas(self):
        """这一断言钉的是丙臂那个数：enable 的宽高必须换成**新**画布。"""
        host = self._host()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            host._remount_layer_after_render_switch("Live2D")
        # 画布矩形 = (10-88, 20+0, 366, 512)，负坐标按挂载口径夹到 0
        self.assertEqual(host.backend.enabled, [(0, 20, 366, 512)],
                         "surface 还是旧引擎那块 ⇒ 新引擎的帧被裁进去")
        self.assertEqual(host._layer_timer.active, True, "重挂后推帧得继续")
        self.assertIn("fit-cancel", host.calls, "上一轮的夹移还活着就会跟新一轮抢")

    def test_remount_does_not_pay_a_fidus_round(self):
        """换引擎不该触发「量完再挂」：那一发最坏 13.5 s，还可能弹模态要人按 Mod+V。"""
        host = self._host()
        with contextlib.redirect_stderr(io.StringIO()):
            host._remount_layer_after_render_switch("Live2D")
        fit = [c for c in host.calls if c[0] == "fit"][0]
        self.assertEqual(fit[2], {"measured": False})
        self.assertFalse(getattr(host, "_pending_mount", None))

    def test_first_frame_hook_is_the_live2d_side_of_the_switch(self):
        """钩子真在首帧那一步上：`_on_live2d_first_frame` 走完后 enable 收到了新尺寸。"""
        host = self._host()
        host._l2d_pending = True
        host._use_live2d = True
        host._cancel_live2d_startup_timeout = lambda: None
        host._fit_window_to_model = lambda: None
        host._restore_pet_position = lambda: None
        host._reveal_live2d_window = lambda: None
        host._mark_renderer_ready = lambda: None
        host.width = lambda: 219
        host.height = lambda: 410
        host.x = lambda: 10
        host.y = lambda: 20
        with contextlib.redirect_stderr(io.StringIO()):
            host._on_live2d_first_frame()
        self.assertEqual(host.backend.enabled, [(0, 20, 366, 512)])

    def test_fallback_remounts_after_show_not_before(self):
        """回退 PNG 那条链也会把顶层 show 回来：重挂必须在 `show()/raise_()` **之后**。

        这一格是 L1 探针丙臂实测出来的岔口：钩子原先挂在 `_init_png_renderer` 里面，
        回退收尾 `visible=True` 而 `enable_calls 总数=2` ⇒ 窗口与 surface 同时上屏，
        桌宠画两份。挂载动作自己会 `hide()`，所以它得站在链尾。
        """
        host = self._host()
        host.calls = []
        host._cancel_live2d_startup_timeout = lambda: None
        host._init_png_renderer = lambda: host.calls.append("png")
        host._place_initial_position = lambda: None
        host.setWindowOpacity = lambda *_a: None
        host.show = lambda: host.calls.append("show")
        host.raise_ = lambda: host.calls.append("raise")
        host._mark_renderer_ready = lambda: host.calls.append("ready")
        host._remount_layer_after_render_switch = lambda reason: host.calls.append(
            ("remount", reason))

        class OldGLWidget:
            def shutdown(self):
                pass

            def hide(self):
                pass

            def deleteLater(self):
                pass

        host.sprite_label = OldGLWidget()
        with contextlib.redirect_stderr(io.StringIO()):
            host._fallback_to_png("探针：首帧未到")
        self.assertEqual(
            host.calls,
            ["png", "show", "raise", "ready", ("remount", "Live2D 不可用，回退 PNG")],
            "重挂排在 show 之前 = 窗口留在屏上")
        self.assertLess(host.calls.index("show"),
                        host.calls.index(("remount", "Live2D 不可用，回退 PNG")))
        self.assertEqual(host._use_live2d, False)


if __name__ == "__main__":
    unittest.main()
