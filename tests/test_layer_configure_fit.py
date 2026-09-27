"""穿透挂载后"回读 configure 尺寸 → 据此夹移"那一轮的回归（工作项 #78）。

被测真身：`PetRenderHostMixin._layer_fit_*`。后端一律是**假的**——本文件判的是
**算法与出口**：读数怎么用、什么时候发那一发夹移、什么时候必须出声、fidus 什么时候
才被放行。真机数字与正证在
`~/.Athena/projects/meapet/working/layer-configure-size-ignored.md`（H16 探针 N1b/N1h）。

为什么是假后端（L2 而非 L3）
------------------------
这一段的立论前提是"合成器可能不照请求摆"。在真桌面上恰恰**测不到**这条：
niri 对本机那条 180 px 带的反应是可复现的，但 CI 上根本没有带，也就没有夹小；
没有合成器时桥接层连 ctx 都建不出来（`test_layer_bridge_abi.py` 的 no-session 路径）。
所以模型在真机上"是否还成立"不在这里判——**这里判的是它不成立时会怎样**：
预算用完必须走那条出声的出口，而不是静默消失。那正是本工作项要修的失效模式。

用实测的那组数（1366x768、顶带 180 ⇒ 可用高 588、画布 461x614）：
    挂载 (854,143) ⇒ configure 445 = 588 − 143        （被夹，帧全被自家尺寸门控丢掉）
    夹移 ny = 445 + 143 − 614 = −26 ⇒ configure 614   （装得下，帧开始流动）
    信念矩形 = (854, −26 + 180) = (854, 154) = 768 − 614（与带厚无关的恒等式）
"""
import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from meapet.desktop.render_host import PetRenderHostMixin, _LAYER_FIT_WAIT_TICKS

MOUNT = (854, 143, 461, 614)      # 穿透挂载时的那一发请求（margin 坐标）
CLAMPED = (461, 445)              # 合成器实际配出的：高被 588 − 143 夹小
FITTED = (854, -26)               # 按模型回摆的位置（margin 可为负）
BAND = 180                        # 保留带厚度（本文件的常量替身，见 _host）
ON_SCREEN_TOP = 154               # 信念矩形的 y：-26 + 180 == 768 - 614


class FakeBackend:
    """按 §7.1 #12 的门面形状冒充：读数由脚本给出，摆位请求记账。

    脚本耗尽后**重复最后一份**——这就是"合成器再也没给新 configure"的形态，
    夹移后读到夹移前那个尺寸不算证据，只有预算用完才判失败。
    """

    def __init__(self, readings, error=""):
        self.readings = list(readings) or [None]
        self.positions = []
        self.frames = []
        self.error = error

    def logical_size(self):
        if len(self.readings) > 1:
            return self.readings.pop(0)
        return self.readings[0]

    def set_position(self, x, y):
        self.positions.append((int(x), int(y)))

    def update_pixels(self, image):
        self.frames.append(image)

    def last_error(self):
        return self.error


class NoChannelBackend(FakeBackend):
    """旧产物：没有 `logical_size` 这个符号 ⇒ 回读通道整条不存在。"""

    logical_size = None        # 门面那句 callable(backend.logical_size) 因此落空

    def __init__(self):
        super().__init__([None])


def _host(backend, band=BAND):
    """`band=None` ⇒ 不钉带厚，用真身 `_layer_band_thickness`（配 mock 屏幕用）。"""
    host = object.__new__(PetRenderHostMixin)
    host._layer_backend = backend
    host.config = {}
    host.bubbles = []
    host.started = []
    host.sprite_label = SimpleNamespace(_proxy_rect=None)
    # 带厚是"读屏幕尺寸"的产物，归 `TestBandThickness` 判；夹移那一轮的用例钉成
    # 常数，好让 margin → 屏幕坐标的换算可断言。
    if band is not None:
        host._layer_band_thickness = lambda usable_h: band
    host._maybe_start_fidus_locate = lambda *r: host.started.append(tuple(r))
    host._show_bubble = lambda text, *_a, **_k: host.bubbles.append(text)
    return host


def _proxy_rect(host):
    rect = host.sprite_label._proxy_rect
    if rect is None:
        return None
    return (rect.x(), rect.y(), rect.width(), rect.height())


def _pump(host, ticks):
    out = []
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        for _ in range(ticks):
            out.append(host._layer_fit_service())
    return out, err.getvalue()


class TestSettlesImmediately(unittest.TestCase):
    def test_matching_configure_starts_fidus_without_a_move(self):
        be = FakeBackend([MOUNT[2:]])
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        skipped, log = _pump(host, 1)
        self.assertEqual(skipped, [False], "本拍已落定，不该拦推帧")
        self.assertEqual(be.positions, [], "尺寸对得上就不该挪")
        self.assertEqual(host.started, [MOUNT])
        self.assertEqual(host.bubbles, [])
        self.assertEqual(log, "")

    def test_late_configure_is_still_acted_on(self):
        """configure 慢一拍才落地是 §6.3 认过的瞬态：等它，别在空读数上下结论。"""
        be = FakeBackend([None, CLAMPED])
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        skipped, _ = _pump(host, 2)
        self.assertEqual(skipped, [True, True])
        self.assertEqual(be.positions, [FITTED])


class TestClampedConfigureGetsOneMove(unittest.TestCase):
    def test_uses_the_measured_numbers_and_settles(self):
        be = FakeBackend([CLAMPED, MOUNT[2:]])
        host = _host(be)
        host._layer_fit_start(*MOUNT)

        first, log1 = _pump(host, 1)
        self.assertEqual(first, [True], "刚发出夹移，这一拍别推帧")
        self.assertEqual(be.positions, [FITTED])
        self.assertLess(FITTED[1], 0, "夹移必须允许负 margin：max(0, y) 那条旧夹法正是病根")
        self.assertEqual(
            _proxy_rect(host), (FITTED[0], ON_SCREEN_TOP, MOUNT[2], MOUNT[3]),
            "信念矩形必须跟着夹移走：margin + 带厚 = 屏幕坐标",
        )
        self.assertEqual(host.started, [], "本轮没落定前不许启动 fidus")

        second, log = _pump(host, 1)
        self.assertEqual(second, [False])
        self.assertEqual(host.started, [(FITTED[0], FITTED[1], *MOUNT[2:])])
        self.assertEqual(host.bubbles, [])
        self.assertIn("回摆", log1, "那一发夹移必须留下按模型算出的过程记录")

    def test_only_one_move_is_ever_sent(self):
        """模型不成立时第二发就是猜：预算跑完也只发那一发。"""
        be = FakeBackend([CLAMPED])
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        results, log = _pump(host, _LAYER_FIT_WAIT_TICKS * 2)
        self.assertEqual(be.positions, [FITTED])
        self.assertFalse(results[-1], "落定之后不该一直拦帧")
        self.assertIn("仍未等于请求", log)

    def test_no_readback_channel_starts_fidus_at_once(self):
        """旧产物没有 #12 ⇒ 这段无从判断，直接把 fidus 放行，不拦帧也不出声。"""
        be = NoChannelBackend()
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        skipped, log = _pump(host, 3)
        self.assertEqual(skipped, [False, False, False])
        self.assertEqual(host.started, [MOUNT])
        self.assertEqual(be.positions, [])
        self.assertEqual(log, "")
        self.assertIsNone(host._fit_rect)


class TestLoudExits(unittest.TestCase):
    def test_never_configured_reports_rather_than_hiding(self):
        be = FakeBackend([None])
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        results, log = _pump(host, _LAYER_FIT_WAIT_TICKS)
        self.assertEqual(results[:-1], [True] * (_LAYER_FIT_WAIT_TICKS - 1))
        self.assertFalse(results[-1])
        self.assertIn("从未被 configure", log)
        self.assertTrue(any("屏幕放不下" in b for b in host.bubbles))
        self.assertEqual(host.started, [], "尺寸都没落定，量一个不在屏上的 surface 只会误导")
        self.assertIsNone(host._fit_rect)

    def test_unmovable_mismatch_moves_nothing(self):
        """配得比请求还大（该合成器另有夹法）⇒ 按模型无处可挪：出声，不猜第二解。"""
        be = FakeBackend([(461, 700)])
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        results, log = _pump(host, 1)
        self.assertEqual(results, [False])
        self.assertEqual(be.positions, [])
        self.assertIn("无处可挪", log)
        self.assertEqual(host.started, [])
        self.assertEqual(_proxy_rect(host), None, "没挪就不该改写信念")

    def test_bridge_diagnosis_is_printed_but_never_decides(self):
        """粘性错误只作为附注端给人看（I6）：不得据它分支——这里让它为空也一样出声。"""
        be = FakeBackend([None], error="layer_logical_size: 句柄非法")
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        _, log = _pump(host, _LAYER_FIT_WAIT_TICKS)
        self.assertIn("桥接层诊断：layer_logical_size: 句柄非法", log)

        quiet = FakeBackend([None])
        host2 = _host(quiet)
        host2._layer_fit_start(*MOUNT)
        _, log2 = _pump(host2, _LAYER_FIT_WAIT_TICKS)
        self.assertNotIn("桥接层诊断", log2)
        self.assertIn("从未被 configure", log2)

    def test_readback_failure_is_reported_not_swallowed(self):
        be = FakeBackend([None])
        host = _host(be)
        host._layer_fit_start(*MOUNT)

        def boom():
            raise RuntimeError("桥没了")

        be.logical_size = boom
        results, log = _pump(host, 1)
        self.assertEqual(results, [False])
        self.assertIn("回读异常 RuntimeError: 桥没了", log)
        self.assertEqual(host.started, [])


class TestRoundLifetime(unittest.TestCase):
    def test_cancel_drops_a_pending_round(self):
        be = FakeBackend([CLAMPED])
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        _pump(host, 1)
        host._layer_fit_cancel()
        results, log = _pump(host, _LAYER_FIT_WAIT_TICKS * 2)
        self.assertEqual(results, [False] * len(results))
        self.assertEqual(log, "")
        self.assertEqual(host.started, [])

    def test_frames_are_skipped_only_while_the_round_is_open(self):
        sentinel = SimpleNamespace(isNull=lambda: False)   # 门面的"非空帧"判据
        be = FakeBackend([None, MOUNT[2:]])
        host = _host(be)
        host.sprite_label.render_offscreen = lambda: sentinel
        host._layer_fit_start(*MOUNT)

        host._push_layer_frame()
        self.assertEqual(be.frames, [], "等回读那一拍推了也必被尺寸门控丢掉")

        host._push_layer_frame()
        self.assertEqual(be.frames, [sentinel], "落定那一拍就放行")

        host._push_layer_frame()
        self.assertEqual(len(be.frames), 2, "落定之后每拍照常推，不再拦")


class TestBandThickness(unittest.TestCase):
    """带厚 = 整屏高 − 可用高：本模块唯一一处读屏幕尺寸的地方。"""

    def test_derives_from_the_screen_height(self):
        host = _host(FakeBackend([CLAMPED]), band=None)
        screen = SimpleNamespace(geometry=lambda: SimpleNamespace(height=lambda: 768))
        with mock.patch("meapet.desktop.render_host.QApplication") as app:
            app.primaryScreen.return_value = screen
            self.assertEqual(host._layer_band_thickness(588), BAND)

    def test_no_screen_is_zero_not_a_guess(self):
        host = _host(FakeBackend([CLAMPED]), band=None)
        with mock.patch("meapet.desktop.render_host.QApplication") as app:
            app.primaryScreen.return_value = None
            self.assertEqual(host._layer_band_thickness(588), 0)

    def test_negative_band_clamps_to_zero(self):
        """可用高比整屏还大（scale≠1 之类）⇒ 不给一个负的带厚去挪信念矩形。"""
        host = _host(FakeBackend([CLAMPED]), band=None)
        screen = SimpleNamespace(geometry=lambda: SimpleNamespace(height=lambda: 768))
        with mock.patch("meapet.desktop.render_host.QApplication") as app:
            app.primaryScreen.return_value = screen
            self.assertEqual(host._layer_band_thickness(900), 0)


if __name__ == "__main__":
    unittest.main()
