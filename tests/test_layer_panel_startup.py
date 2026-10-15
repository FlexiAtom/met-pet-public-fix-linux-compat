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

from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from meapet.desktop.layer_debug_panel import LayerDebugPanel  # noqa: E402
from meapet.desktop.live2d_widget import PLACEHOLDER_CANVAS  # noqa: E402
from meapet.desktop.renderer import SpriteCanvas  # noqa: E402

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
    ③ PNG **按裁决**不去解 alpha 轮廓 ⇒ 挂载永远按整画布请求。
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

    def test_png_current_frame_comes_from_the_backing_frame(self):
        """PNG 现在也交得出 fidus 能用的帧：**模板与 surface 同源**这一句从此两种引擎都成立。

        人工 2026-10-15「要上测量，Niri上Qt不可靠」——Qt 坐标在那台合成器上不可信，唯一
        可信的是像素对照，而对照要求"量的是上屏那一份"。所以这里断的是逐位相等：
        shape 是 `(h, w, 4)`（把 w/h 数反就当场红），那颗单独涂色的像素落在 `[1, 2]`。
        """
        from PyQt5.QtGui import QColor, QPixmap
        from meapet.desktop.renderer import SpriteCanvas

        host, _RH = self._host()
        base = QPixmap(7, 5)
        base.fill(QColor(1, 2, 3, 255))
        marked = base.toImage()
        marked.setPixelColor(2, 1, QColor(9, 8, 7, 255))     # x=2, y=1：非方形 ⇒ 行列反了必露
        canvas = SpriteCanvas()
        canvas.set_frame(QPixmap.fromImage(marked))
        host.sprite_label = canvas

        frame = host._fidus_current_frame()
        self.assertIsNotNone(frame, "PNG 交不出帧 ⇒ 预挂载测量这一路又死回去")
        self.assertEqual(frame.shape, (5, 7, 4))
        self.assertEqual(frame[1, 2].tolist(), [9, 8, 7, 255])
        self.assertEqual(frame[0, 0].tolist(), [1, 2, 3, 255])
        self.assertEqual(int(frame[..., 3].min()), 255, "整帧不透明：alpha 别在转换里丢掉")
        canvas.deleteLater()

    def test_png_mounts_the_whole_canvas_and_says_why(self):
        """PNG 的挂载形状与 alpha 无关：那一格在**取帧之前**就被裁决挡回，并且**出声**。

        故意给一支**交得出帧**的后备帧：2026-10-15 之前那句「PNG 模式没有离屏渲染这一步」
        是能力缺失的自白，本项把那条路接上之后，拦着的只剩裁决本身——所以这里连"一次都不
        取帧"也要断言，守卫写在 `return` 之后而不是取帧之前就不算守住。
        """
        from PyQt5.QtGui import QColor, QImage

        host, _RH = self._host()
        calls = []

        def _frame(_self):
            calls.append(1)
            img = QImage(461, 614, QImage.Format_RGBA8888)
            img.fill(QColor(0, 0, 0, 0))
            img.fill(QColor(9, 9, 9, 255), img.rect().adjusted(60, 41, -341, -13))  # 一块轮廓
            return img

        host.sprite_label = type("PngWidget", (), {"frame_image": _frame})()
        region = host._layer_surface_region(461, 614)
        self.assertEqual(region, (0, 0, 461, 614), "按 alpha 解轮廓＝偷做热区")
        self.assertEqual(calls, [], "PNG 白付一次整帧拷贝")
        log = self._surface_log(host)
        self.assertIn("裁决 2026-10-06", log)
        self.assertIn("按整画布挂", log)
        # 「离屏渲染不可用？」在那一侧指向一个不存在的开关（人工 2026-10-15 报的这句）
        self.assertNotIn("离屏渲染不可用", log)
        self.assertNotIn("后备帧为空", log)

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
        self.assertNotIn("PNG 后备帧为空", log)
        self.assertNotIn("裁决 2026-10-06", log)

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

    def test_interactive_remount_builds_no_layer(self):
        """没挂着 surface 就没有要重发的东西：不该凭空建一只 layer。

        这条原名 `test_interactive_switch_asks_for_nothing`，那时它还顺手把"交互态换引擎
        什么都不该发生"钉成了契约——人工 2026-10-15 现报「PNG 切换到 Live2D 需要重新请求
        窗口大小」缺的正是顶层 `resize` 那一发，而 #258 交付时给这个缺口发的结婚证就是它。
        拆小回到本函数自己的那句：`_remount_layer_after_render_switch` 在没挂载的状态下一步
        不走（`hide` 走一步就把交互态窗口收掉了）。尺寸请求与读回归
        `TestEngineSwitchRequestsAndReadsBackSize`。
        """
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


class FakeSignal:
    """假 Live2D 控件上那五条 `connect` 的落点：只看接没接上，不看它发不发得出。"""

    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class FakeLive2DWidget(QWidget):
    """`Live2DModel.create_widget` 的替身：**真** QWidget。

    真的才有 `geometry()`——`_fit_window_to_model` 要读旧画布那份 QRect。`setGeometry` 只记账
    不落真值，于是"这一发按哪份数请求子控件"逐条留在 `geometry_calls` 里。
    """

    def __init__(self):
        super().__init__()
        self.geometry_calls = []
        self.head_patted = FakeSignal()
        self.lower_left_patted = FakeSignal()
        self.lower_right_patted = FakeSignal()
        self.chat_requested = FakeSignal()
        self.first_frame_ready = FakeSignal()
        self.initialization_failed = FakeSignal()

    def setGeometry(self, *rect):                       # noqa: N802 · Qt 的驼峰名
        self.geometry_calls.append(tuple(int(v) for v in rect))


class FakeLive2DModelBase:
    """`_init_live2d` 只问三件事：模型在不在、报多大画布、能否造 widget。

    `model is None` 时报 `PLACEHOLDER_CANVAS`——逐字照 `live2d_widget.py:100-101`。那份占位数
    正是 C2 要拦的东西，替身这里不许随手换成别的数，否则这条测试判不到它。
    """

    LOADED_CANVAS = (731, 1024)      # 本机 mea_live2d 真画布（5:7）归一到参照长边 1024 的读数

    def __init__(self, _model_dir, *, loaded=False):
        self.model = object() if loaded else None
        self.widget = None

    def get_suggested_size(self):
        return self.LOADED_CANVAS if self.model is not None else PLACEHOLDER_CANVAS

    def create_widget(self, _parent):
        self.widget = FakeLive2DWidget()
        return self.widget


class RecordingSpriteCanvas(SpriteCanvas):
    """真 `SpriteCanvas`（C3 要判"PNG 控件没被换掉"就得是真那一类），但 `hide`/`deleteLater` 只记账。

    真 `deleteLater` 会把延迟删除投进事件循环——同一个进程里后面的测试一跑循环就会把这个
    C++ 对象收掉，那时断言读到的不是产品行为而是 Qt 的清理顺序。
    """

    def __init__(self):
        super().__init__()
        self.seen = []

    def hide(self):
        self.seen.append("hide")

    def deleteLater(self):                            # noqa: N802
        self.seen.append("deleteLater")


class TestEngineSwitchRequestsAndReadsBackSize(unittest.TestCase):
    """交互态换引擎：不发占位假数（C2）、发了要读回（C1）、穿透态不假装（C3）。

    人工 2026-10-15 现报「"PNG切换到Live2D需要重新请求窗口大小，而不是直接切"此BUG尚未修复」，
    而 #258 在 `finished/png-to-live2d-window-size-requery.md` 里已被判完成——那一件修的是
    **穿透态**那一半（换引擎后重发一次 `enable`），交互态这三个缺口一个没动，还被
    旧那条 `test_interactive_switch_asks_for_nothing` 按成了期望。
    """

    FACTOR = 0.5
    TILED_SIZE = (659, 736)          # 人工现测：请求 461×614、平铺下实得 659×736

    def _host(self):
        import meapet.desktop.render_host as RH
        from PyQt5.QtCore import QPoint

        host = object.__new__(RH.PetRenderHostMixin)
        host.calls = []
        host.config = {"live2d": {"model_dir": "mea_live2d"}}
        host._use_live2d = False
        host._l2d_model = None
        host._l2d_pending = False
        host._live2d_startup_widget = None
        host._renderer_ready = True
        host._size_factor = self.FACTOR
        host.renderer = None
        host.sprite_label = RecordingSpriteCanvas()
        host._standby = False
        host._layer_timer = FakeTimer()
        host._layer_window_pos = (10, 20)
        host._layer_backend = FakeBackend()
        host._fidus_current_frame = lambda: None
        host._layer_panel = None
        # `_init_live2d` 把这几只手接到控件信号上。假 host 是纯 mixin，缺哪只就 AttributeError，
        # 而 `_toggle_render_mode` 那个 try 会把它吞成一次「回退 PNG」——链没跑歪，是桩没铺全。
        host._on_head_patted = lambda *_a: None
        host._on_lower_left_patted = lambda *_a: None
        host._on_lower_right_patted = lambda *_a: None
        host._start_chat = lambda *_a: None
        host._cancel_premount_measure = lambda: host.calls.append("cancel")
        host._cancel_live2d_startup_timeout = lambda: host.calls.append("cancel-timeout")
        host._save_config = lambda: host.calls.append("save")
        host._show_bubble = lambda text, *_a: host.calls.append(("bubble", text))
        host._place_initial_position = lambda: host.calls.append("place")
        host._init_png_renderer = lambda: host.calls.append("png")
        host._mark_renderer_ready = lambda: host.calls.append("ready")
        host._restore_pet_position = lambda: host.calls.append("restore")
        host._reanchor_after_resize = lambda *a, **k: None
        host._apply_live2d_window_region = lambda *a, **k: host.calls.append("region")
        host.when_renderer_ready = lambda fn: host.calls.append(("ready-cb", fn))
        host.setWindowOpacity = lambda *_a: None
        host.clearMask = lambda: None
        host.show = lambda: host.calls.append("show")
        host.raise_ = lambda: host.calls.append("raise")
        host.mapTo = lambda *_a: QPoint(0, 0)
        # 「实得」的真值源：`_pump_window_size` 判的就是这两只数跟请求合不合。
        # `size_follows=False` 那档 = 平铺（尺寸请求被吃），True = 尺寸请求当场生效。
        host.size = [262, 367]
        host.size_follows = True

        def _resize(w, h):
            host.calls.append(("resize", int(w), int(h)))
            if host.size_follows:
                host.size = [int(w), int(h)]

        host.resize = _resize
        host.width = lambda: host.size[0]
        host.height = lambda: host.size[1]
        host.x = lambda: 10
        host.y = lambda: 20
        return host, RH

    def _fake_qtimer(self, host):
        """假 `RH.QTimer`：`singleShot(0, …)` 只记账。真投递会在别的测试跑循环时打到这只假 host。"""

        class _T(FakeTimer):
            @staticmethod
            def singleShot(ms, _slot):
                host.calls.append(("singleShot", ms))

        return _T

    def _switch(self, host, RH, model_cls):
        import meapet.desktop.live2d_widget as LW

        err = io.StringIO()
        with mock.patch.object(LW, "init_live2d", lambda: None), \
                mock.patch.object(LW, "Live2DModel", model_cls), \
                mock.patch("meapet.config.store.resolve_resource_path", lambda _p: str(ROOT)), \
                mock.patch.object(RH, "QTimer", self._fake_qtimer(host)), \
                contextlib.redirect_stderr(err):
            host._toggle_render_mode()
        return err.getvalue()

    def _switched(self, *, loaded=False, penetrate=False):
        """走完一次交互态 `_toggle_render_mode`，返回 `(host, model, stderr)`。"""
        host, RH = self._host()
        made = []

        def _cls(model_dir):
            model = FakeLive2DModelBase(model_dir, loaded=loaded)
            made.append(model)
            return model

        if penetrate:
            host._layer_timer.start()
        err = self._switch(host, RH, _cls)
        expected_built = 0 if penetrate else 1
        self.assertEqual(len(made), expected_built,
                         "模型构造次数：穿透态该 0 次（到不了首帧，白加载一次），交互态 1 次")
        return host, (made[0] if made else None), err

    # ---------------- C2：不发按占位算的假数 ----------------
    def test_unloaded_canvas_emits_no_top_level_resize(self):
        """模型刚构造完（`initializeGL` 才加载）⇒ 读到的画布是占位 ⇒ 这一发**不发**顶层尺寸。

        占位 (525,735) 与真画布 (731,1024) 算出的窗口尺寸不同，所以"按占位请求"不是无害的
        一次多余请求，而是把已知是假的数发给合成器（人工「而不是直接切」那一格）。
        """
        host, model, err = self._switched()
        placeholder = RH_layout(host, PLACEHOLDER_CANVAS)
        loaded = RH_layout(host, FakeLive2DModelBase.LOADED_CANVAS)
        self.assertNotEqual(
            (placeholder.window_width, placeholder.window_height),
            (loaded.window_width, loaded.window_height),
            "前置事实：占位与真画布算出的顶层尺寸本来就不同")
        self.assertEqual([c for c in host.calls if isinstance(c, tuple) and c[0] == "resize"], [],
                         "画布没加载就发 resize = 发一个按占位算的假数")
        self.assertNotIn("region", host.calls,
                         "mask 按新 layout 算，窗口没跟着变就先切 mask 会切错块 ⇒ 与 resize 一起跳")
        self.assertEqual(
            model.widget.geometry_calls,
            [(placeholder.widget_x, placeholder.widget_y,
              placeholder.widget_width, placeholder.widget_height)],
            "子控件那份完整画布照发：不发它就没有东西可画，首帧永远引不出来")
        self.assertIn("画布未加载", err)
        self.assertEqual(model.widget.first_frame_ready.slots, [host._on_live2d_first_frame],
                         "读回挂在首帧之后，前提是这个钩子还接得上")

    def test_loaded_canvas_does_request_the_real_size(self):
        """对照臂：`.model` 已加载 ⇒ 默认 `resize_window=True` 那条路一字不动，按真画布发。"""
        host, model, _err = self._switched(loaded=True)
        loaded = RH_layout(host, FakeLive2DModelBase.LOADED_CANVAS)
        self.assertIn(("resize", loaded.window_width, loaded.window_height), host.calls)
        self.assertIn("region", host.calls)
        self.assertEqual(
            model.widget.geometry_calls,
            [(loaded.widget_x, loaded.widget_y, loaded.widget_width, loaded.widget_height)])

    # ---------------- C1：发了要读回 ----------------
    def test_interactive_switch_arms_the_readback(self):
        host, _model, _err = self._switched(loaded=True)
        self.assertIs(host._use_live2d, True)
        self.assertIs(host._l2d_pending, True)
        self.assertIs(host._l2d_switch_size_check, True,
                      "手动换引擎 ⇒ 置起读回标志；普通启动路径不置，于是一行日志都不多")
        self.assertIn(("singleShot", 0), host.calls, "5 s 首帧预算照旧起（本案不动它）")
        self.assertIs(host.config["live2d"]["enabled"], True)

    def test_first_frame_reads_back_the_requested_size(self):
        """首帧那一发之后泵一次实得，并把「期望／实得」一起写进日志。"""
        host, model, _err = self._switched()
        model.model = object()                 # 首帧能来，正是 `initializeGL` 里加载完了
        loaded = RH_layout(host, FakeLive2DModelBase.LOADED_CANVAS)
        err = self._first_frame(host)
        self.assertEqual(host.size, [loaded.window_width, loaded.window_height],
                         "`_pump_window_size` 判的是顶层此刻的宽高：这一档让它跟得上请求")
        self.assertIn(f"尺寸请求 期望 {loaded.window_width}x{loaded.window_height} "
                      f"实得 {loaded.window_width}x{loaded.window_height}", err)
        self.assertNotIn("被吃", err)
        self.assertIs(host._l2d_switch_size_check, False,
                      "读过一次就清：悬着的标志会把这一发的读回留给下一次首帧")

    def test_eaten_request_says_so(self):
        """平铺下尺寸请求被吃（人工现测 461×614→659×736）⇒ 说实话并**出声**。"""
        host, model, _err = self._switched()
        model.model = object()
        host.size_follows = False
        host.size = list(self.TILED_SIZE)
        loaded = RH_layout(host, FakeLive2DModelBase.LOADED_CANVAS)
        err = self._first_frame(host)
        self.assertIn(f"尺寸请求 期望 {loaded.window_width}x{loaded.window_height} "
                      f"实得 {self.TILED_SIZE[0]}x{self.TILED_SIZE[1]}", err)
        self.assertIn("被吃", err)
        self.assertIn(("bubble", "窗口尺寸请求被吃（多半还平铺着）⇒ 按 Mod+V 切成浮动再换引擎"),
                      host.calls, "终端那一行他盯着桌宠时读不到 ⇒ 同一句话得走气泡")

    def test_startup_first_frame_pumps_nothing(self):
        """普通启动路径逐字不变：标志不为真 ⇒ 不泵事件、不多一行读数。"""
        host, model, _err = self._switched()
        model.model = object()
        host._l2d_switch_size_check = False       # 启动那一发由 `_init_live2d` 走，不经 `_toggle_render_mode`
        err = self._first_frame(host)
        self.assertNotIn("尺寸请求", err)
        self.assertNotIn("被吃", err)

    def test_unfit_model_clears_the_flag_without_pumping(self):
        """`_fit_window_to_model` 报 None（模型没在）⇒ 没发过尺寸请求，也就没东西可读回，
        但标志照样清掉——否则它悬着，下一次首帧会泵一次不属于它的读数。"""
        host, _model, _err = self._switched()
        host._l2d_model = None
        err = self._first_frame(host)
        self.assertIs(host._l2d_switch_size_check, False)
        self.assertNotIn("尺寸请求", err)

    def test_fallback_clears_the_flag(self):
        host, model, _err = self._switched()
        model.model = object()
        self.assertIs(host._l2d_switch_size_check, True)
        # 换成 PNG 那一类控件：`_fallback_to_png` 对**非** SpriteCanvas 会真 `deleteLater`，
        # 那发延迟删除会投进事件循环，被后面某条测试的 `processEvents` 收掉。
        host.sprite_label = RecordingSpriteCanvas()
        with contextlib.redirect_stderr(io.StringIO()):
            host._fallback_to_png("等待 Live2D 首帧超时")
        self.assertIs(host._l2d_switch_size_check, False)

    # ---------------- C3：穿透态不假装 ----------------
    def test_penetrate_mode_refuses_the_engine_switch(self):
        """穿透态里换引擎**结构上到不了首帧**：顶层被 `hide()` ⇒ `initializeGL` 不派发。
        旧的下场是 5 s 超时 → 回退 PNG，而 `enabled=True` 已经写盘 ⇒ 下次启动再付一次
        「加载→超时→回退」。现在当场拒绝，config 与 PNG 控件一寸不动。"""
        host, model, err = self._switched(penetrate=True)
        canvas = host.sprite_label
        self.assertIsInstance(canvas, SpriteCanvas)
        self.assertEqual(canvas.seen, [], "拒绝 ⇒ PNG 控件不该被 hide/deleteLater")
        self.assertIsNone(model, "不构造模型：那一次加载是白付的")
        self.assertIsNone(host._l2d_model)
        self.assertIs(host._use_live2d, False)
        self.assertNotIn("enabled", host.config["live2d"],
                         "`live2d.enabled` 没被写 True ⇒ 下次启动不会再来一遍加载→超时→回退")
        self.assertNotIn("save", host.calls)
        self.assertNotIn(("singleShot", 0), host.calls, "不起 5 s 定时器：没有首帧可等")
        self.assertFalse(getattr(host, "_l2d_switch_size_check", False),
                         "没发过尺寸请求，也就没有要读回的那一发")
        self.assertIn("没换引擎", err)
        self.assertIn(("bubble", "穿透态里换引擎先切回交互态：Live2D 控件在窗口隐藏时不初始化，"
                                "现在切过去 5 秒后会自动退回 PNG"), host.calls)
        self.assertIn("cancel", host.calls, "作废在飞的「量完再挂」排在闸门之前，拒绝也要先作")

    def _first_frame(self, host):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            host._on_live2d_first_frame()
        return err.getvalue()


def RH_layout(host, canvas):
    """按宿主现成的那条几何通路算 layout——测试只借它取"期望的是哪两个数"，不另抄一份算术。"""
    import meapet.desktop.render_host as RH

    return RH.calculate_live2d_viewport_layout(
        canvas[0], canvas[1], host._size_factor,
        (host.config.get("live2d") or {}).get("window_mask"))


class TestPanelFollowsTheMountOutcome(unittest.TestCase):
    """面板那格跟着**结果**走，不跟着请求走（人工 2026-10-15「PNG仍然无法切穿透」那一轮）。

    `LayerDebugPanel._toggle` 先翻 UI 再调回调，而回调**可能被拒**（`_premount_refuse` ⇒ 一个
    surface 都没挂）。没有写口时，拒绝后面板显示「点击穿透：开／切到 交互」——那是个不存在的
    状态，而它是切回交互态的唯一入口。代价不只是难看：`_remount_layer_after_render_switch`
    的闸门读的正是 `_layer_penetrating()`（真值），面板每轮都在给一个跟真值相反的画面。
    """

    def _host_with_panel(self, *, active):
        """借 `TestEngineSwitchRemountsTheSurface` 那台能跑完整挂载链的 host（真 `_layer_surface_region`）。"""
        host = TestEngineSwitchRemountsTheSurface()._host(active=active)
        host._show_bubble = lambda *_a, **k: None
        host._layer_panel = None
        return host

    def test_set_penetrate_writes_state_without_firing_the_callback(self):
        """`set_penetrate` 是**结果**的写口：它不许变成第二只手去拨请求。"""
        fired = []
        panel = LayerDebugPanel(on_toggle=fired.append, penetrate=False)
        panel.set_penetrate(True)
        self.assertIs(panel._penetrate, True)
        self.assertEqual(panel._label.text(), "点击穿透：开")
        self.assertEqual(panel._btn.text(), "切到 交互")
        self.assertEqual(fired, [], "写口触发回调 = 面板自己又请求了一次切换")

    def test_refusal_puts_the_panel_back_to_off(self):
        """真按钮 → 真面板 → `_set_layer_mode(True)` → 被拒 ⇒ 面板回到「关」，且回调只被拨一次。"""
        host = self._host_with_panel(active=False)
        fired = []

        def _set_layer_mode(penetrate):
            fired.append(penetrate)
            if penetrate:
                host._premount_refuse("假引擎：没有可信读数", "量不出可信位置，没切穿透")

        panel = LayerDebugPanel(on_toggle=_set_layer_mode, penetrate=False)
        host._layer_panel = panel
        with contextlib.redirect_stderr(io.StringIO()):
            panel._btn.click()
        self.assertEqual(fired, [True], "回调被拨的那一次是**人**点的，写口不该再来一次")
        self.assertIs(panel._penetrate, False, "拒绝之后面板说「开」= 报一个不存在的状态")
        self.assertEqual(panel._label.text(), "点击穿透：关")
        self.assertEqual(panel._btn.text(), "切到 穿透", "按钮文案也得回去：它是切回交互态的入口")

    def test_successful_mount_writes_the_panel_to_on(self):
        """成功路径也回写：`enable` 那一段可能是被拒之后重试的，面板还停在上一轮的「关」。"""
        host = self._host_with_panel(active=False)
        panel = LayerDebugPanel(on_toggle=lambda _p: None, penetrate=False)
        host._layer_panel = panel
        with contextlib.redirect_stderr(io.StringIO()):
            host._do_layer_mount(0, 0, 366, 512, measured=False)
        self.assertEqual(host.backend.enabled, [(0, 0, 366, 512)], "前置：这一发真挂上了")
        self.assertIs(panel._penetrate, True)
        self.assertEqual(panel._label.text(), "点击穿透：开")

    def test_a_raising_enable_also_puts_the_panel_back_to_off(self):
        """`enable` 抛了 ⇒ 一个 surface 都没挂，而那一支不经 `_premount_refuse`，得自己回写。

        面板说谎的形态不止"被拒"一种：桥接层真抛时（memfd／ring 那一族）既没有气泡也没有
        `_premount_refuse`，只有终端里一行 `safe_print`，人看得见的是面板上那格「开」。
        """
        host = self._host_with_panel(active=False)
        panel = LayerDebugPanel(on_toggle=lambda _p: None, penetrate=True)
        host._layer_panel = panel

        def _boom(_screen, *_a, **_k):
            raise RuntimeError("memfd 没了")

        host.backend.enable = _boom
        with contextlib.redirect_stderr(io.StringIO()):
            host._do_layer_mount(0, 0, 366, 512, measured=False)
        self.assertFalse(host._layer_penetrating(), "前置：真值这边确实没挂上")
        self.assertIs(panel._penetrate, False, "挂失败后面板仍说「开」= 报一个不存在的状态")
        self.assertEqual(panel._label.text(), "点击穿透：关")

    def test_write_back_is_skipped_when_there_is_no_panel(self):
        """假 host 普遍没有 `_layer_panel`：没有就跳，不替它造一个（否则每条回归都得补一格桩）。"""
        host = self._host_with_panel(active=False)
        self.assertIs(getattr(host, "_layer_panel", None), None)
        with contextlib.redirect_stderr(io.StringIO()):
            host._premount_refuse("没面板", "也没气泡")
        self.assertIs(getattr(host, "_layer_panel", None), None)


if __name__ == "__main__":
    unittest.main()
