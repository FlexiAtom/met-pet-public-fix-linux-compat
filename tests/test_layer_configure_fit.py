"""穿透挂载后"回读 configure 尺寸 → 据此夹移"那一轮的回归（工作项 #78），
以及落定之后的**稳态重查**（工作项 #89，人工裁决「带撤之后夹移无声错 b 即可」）。

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

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QImage

from meapet.desktop.render_host import (
    PetRenderHostMixin,
    _LAYER_FIT_WAIT_TICKS,
    _LAYER_FIT_WATCH_TICKS,
)

MOUNT = (854, 143, 461, 614)      # 穿透挂载时的那一发请求（margin 坐标）
CLAMPED = (461, 445)              # 合成器实际配出的：高被 588 − 143 夹小
FITTED = (854, -26)               # 按模型回摆的位置（margin 可为负）
BAND = 180                        # 保留带厚度（本文件的常量替身，见 _host）
ON_SCREEN_TOP = 154               # 信念矩形的 y：-26 + 180 == 768 - 614
FIT = MOUNT[2:]                   # 461x614＝"配得等于请求"


class FakeBackend:
    """按 §7.1 #12 的门面形状冒充：读数由脚本给出，摆位请求记账。

    脚本耗尽后**重复最后一份**——这就是"合成器再也没给新 configure"的形态，
    夹移后读到夹移前那个尺寸不算证据，只有预算用完才判失败。
    """

    def __init__(self, readings, error=""):
        self.readings = list(readings) or [None]
        self.positions = []
        self.frames = []
        self.enabled = []
        self.reads = 0
        self.error = error

    def feed(self, *sizes):
        """换一份读数脚本（稳态重查的测试要能事后改合成器的行为）。"""
        self.readings = list(sizes)

    def logical_size(self):
        self.reads += 1
        if len(self.readings) > 1:
            return self.readings.pop(0)
        return self.readings[0]

    def enable(self, _screen, w, h, x, y):
        # 记账口径与 `test_fidus_position.FakeLayerBackend` 一致：(x, y, w, h)
        self.enabled.append((int(x), int(y), int(w), int(h)))

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


def _silhouette_frame(top: int, bottom: int, h: int = 614, w: int = 461):
    """只有 `top..bottom`（右开）那几行不透明的一帧——本机 `mea_live2d` 是 41..570。"""
    frame = np.zeros((h, w, 4), dtype=np.uint8)
    frame[top:bottom, :, 3] = 255
    return frame


def _contour_frame(top: int, bottom: int, left: int, right: int, h: int = 614, w: int = 461):
    """横竖都只有一块不透明：`(top,bottom) × (left,right)`，右开——画布四周带透明边的形状。"""
    frame = np.zeros((h, w, 4), dtype=np.uint8)
    frame[top:bottom, left:right, 3] = 255
    return frame


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


class TestHeadClipIsLoud(unittest.TestCase):
    """夹移把**帧**塞进可用区之后，**轮廓**仍可能出屏 ⇒ 必须出声（#81）。

    形状取自 niri `scale=1.5` 的实测（`working/fractional-scale-head-clip.md` §2）：
    请求 461x614、配出 461x512 ⇒ 夹移 (213,0)→(213,-102)，轮廓纵向 41..570 的顶端
    因此落在屏幕行 −61。这一段判的是出口，不改摆位、不拦 fidus。
    """

    MOUNT_15 = (213, 0, 461, 614)
    READINGS_15 = [(461, 512), (461, 614)]

    def test_clipped_head_speaks_and_still_positions(self):
        be = FakeBackend(self.READINGS_15)
        host = _host(be, band=0)          # 1.5 档没有外部带 ⇒ 带厚 0，屏高 512
        host._fidus_current_frame = lambda: _silhouette_frame(41, 570)
        host._layer_fit_start(*self.MOUNT_15)
        _pump(host, 2)
        self.assertEqual(len(host.bubbles), 1, f"该只有一句出声，实际 {host.bubbles}")
        self.assertIn("屏幕高度不够", host.bubbles[0])
        self.assertEqual(host.started, [(213, -102, 461, 614)],
                         "削头不许顺带把定位关掉——量具在 1.5 档四臂全绿")

    def test_silhouette_inside_the_screen_stays_quiet(self):
        """带厚 180 那一档（#74 实测形状）：夹移到 −26 后轮廓顶端在屏幕行 195，没削。"""
        be = FakeBackend([CLAMPED, MOUNT[2:]])
        host = _host(be)
        host._fidus_current_frame = lambda: _silhouette_frame(41, 570)
        host._layer_fit_start(*MOUNT)
        _pump(host, 2)
        self.assertEqual(host.bubbles, [])
        self.assertEqual(host.started, [(FITTED[0], FITTED[1], *MOUNT[2:])])

    def test_unclamped_settle_does_not_pay_a_render(self):
        """没夹过移 ⇒ 帧整体在可用区内、轮廓是其子集 ⇒ 结构上不可能出屏，不为此多渲染一次。"""
        be = FakeBackend([MOUNT[2:]])
        host = _host(be)
        seen = []
        host._fidus_current_frame = lambda: seen.append(1)
        host._layer_fit_start(*MOUNT)
        _pump(host, 1)
        self.assertEqual(seen, [], "判据不该在不可能出屏的那一档取帧")
        self.assertEqual(host.bubbles, [])
        self.assertEqual(host.started, [MOUNT])

    def test_unjudgable_frame_is_not_invented_into_a_clip(self):
        """取不到帧 = 无从判断 ⇒ 不出声也不摆位（跟"没有回读通道就放行"同一档）。"""
        be = FakeBackend(self.READINGS_15)
        host = _host(be, band=0)
        host._fidus_current_frame = lambda: None
        host._layer_fit_start(*self.MOUNT_15)
        _pump(host, 2)
        self.assertEqual(host.bubbles, [])
        self.assertEqual(host.started, [(213, -102, 461, 614)])


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
        # 真 QImage：门面那句 `isinstance(image, QImage)` 是产品与后端之间的契约，
        # 拿 SimpleNamespace 冒充会让"推的是同一块帧"这条断言测到假东西（甲落地时炸在这里）。
        sentinel = QImage(MOUNT[2], MOUNT[3], QImage.Format_RGBA8888)
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
        self.assertIs(be.frames[1], sentinel,
                      "configure 等于帧尺寸 ⇒ 一个像素都不该搬，别为常态付一次 memcpy")


class TestPushConformsToConfigure(unittest.TestCase):
    """甲（人工 2026-10-03「都做」）：提交尺寸对齐 configure ⇒ **切着显示**，不再整片空白。

    失效模式（人工真机那条 `只配出 456x463（请求 512x512）`）：门控比的是"提交尺寸 == 逻辑
    尺寸"，而被夹小的挂载请求让这个等式**永远**不成立 ⇒ 每一帧被我们自己丢掉。裁这一刀才是
    裁决「测量赢 ⇒ 允许切头/切脚」的执行手——此前只落了前半句（不改位），所以那一声"允许切"
    实际表现为空白。位置在这一族里一字未动。
    """

    @staticmethod
    def _frame(w, h, marks=None):
        """一块 RGBA8888 的帧，`marks={(x, y): (r, g, b, a)}` 处点上做记号。"""
        frame = QImage(w, h, QImage.Format_RGBA8888)
        frame.fill(Qt.transparent)
        for (x, y), rgba in (marks or {}).items():
            frame.setPixelColor(x, y, QColor(*rgba))
        return frame

    def _push(self, host):
        """只跑推帧那一段：回读轮已经落定（`_fit_rect` 空、`_fit_anchor` 空）。"""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            host._push_layer_frame()
        return err.getvalue()

    def test_clamped_configure_crops_and_says_it_once(self):
        host = _host(FakeBackend([CLAMPED]))
        host._fit_size = CLAMPED                  # 回读轮落定后的那份事实
        host.sprite_label.render_offscreen = lambda: self._frame(461, 614)
        log = self._push(host)
        submitted = host._layer_backend.frames[0]
        self.assertEqual((submitted.width(), submitted.height()), CLAMPED,
                         "提交尺寸不等于 configure ⇒ 门控照丢，这一格就白修了")
        self.assertIn("按 configure 对齐", log, "在裁这件事不能只有气泡知道，日志得留一行")
        self.assertEqual(self._push(host), "", "每帧都成立的状态，连起来说会把日志淹掉")

    def test_crop_keeps_the_top_left_and_drops_the_overflow(self):
        """裁的是**右和下**：surface 的 (0,0) 就是 margin 那个点，往上/左裁会把桌宠整体搬走。"""
        host = _host(FakeBackend([CLAMPED]))
        host._fit_size = CLAMPED
        keep = (1, 2, 3, 255)          # 左上那颗：必须留在原位
        gone = (9, 9, 9, 255)          # 右下那颗：本来在请求之内、屏幕之外
        frame = self._frame(461, 614, {(0, 0): keep, (460, 613): gone})
        host.sprite_label.render_offscreen = lambda: frame
        self._push(host)
        submitted = host._layer_backend.frames[0]
        self.assertEqual((submitted.width(), submitted.height()), CLAMPED)
        self.assertEqual(submitted.pixelColor(0, 0).rgba(), QColor(*keep).rgba(),
                         "surface 左上那颗还得是帧左上那颗——挪了就等于把桌宠搬走")
        self.assertLess(submitted.height(), 614,
                        "右下那颗在 614 行里，而裁完只有 445 行——被切掉的就是这一块")

    def test_no_readback_submits_the_frame_byte_for_byte(self):
        """旧产物／还没读到 configure ⇒ 无从对齐 ⇒ 原样交出去（行为逐位不变，不猜尺寸）。"""
        host = _host(FakeBackend([None]))
        frame = self._frame(461, 614)
        host.sprite_label.render_offscreen = lambda: frame
        self._push(host)
        self.assertEqual(host._layer_backend.frames, [frame], "没读数就不该动这块帧")

    def test_crop_origin_follows_the_surface_region(self):
        """丙 的原点：surface 是帧里轮廓那一块 ⇒ 裁的窗口从 (ox,oy) 起，不是从 (0,0)。"""
        host = _host(FakeBackend([(30, 40)]))
        host._fit_size = (30, 40)
        host._layer_frame_origin = (10, 20)
        mark = (7, 8, 9, 255)
        frame = self._frame(461, 614, {(10, 20): mark})
        host.sprite_label.render_offscreen = lambda: frame
        self._push(host)
        submitted = host._layer_backend.frames[0]
        self.assertEqual((submitted.width(), submitted.height()), (30, 40))
        self.assertEqual(submitted.pixelColor(0, 0).rgba(), QColor(*mark).rgba(),
                         "surface 的左上那颗应当是帧里 (10,20) 那一颗")

    def test_oversized_configure_is_reported_once_and_not_padded(self):
        """模型只会往小夹；真配大了就是不适用 ⇒ 报数、不猜第二种解法（不补边、不起 QPainter）。"""
        host = _host(FakeBackend([(900, 900)]))
        host._fit_size = (900, 900)
        frame = self._frame(461, 614)
        host.sprite_label.render_offscreen = lambda: frame
        log = self._push(host)
        self.assertIn("模型不适用", log)
        self.assertIs(host._layer_backend.frames[0], frame)
        host.sprite_label.render_offscreen = lambda: self._frame(461, 614)
        self.assertEqual(self._push(host), "", "同一件事只说一次")
        host._layer_fit_start(*MOUNT)             # 新一轮挂载 ⇒ 那条"只说一次"要重新有效
        self.assertFalse(host._layer_conform_reported)


class TestMountRequestsTheContour(unittest.TestCase):
    """丙（人工 2026-10-03「都做」）：挂载请求吃**不透明轮廓**，信念继续吃**整画布**。

    那条决定性日志的两轴逐位等于「输出 − margin」（1366−910=456、768−305=463）⇒ 合成器就是
    "剩多少配多少"，而撑爆请求的是画布四周**用户从来没看见过**的那圈透明边（`_layer_geometry()`
    返回的就是 widget 尺寸）。所以源头在请求的形状，门控只是把"请求太大"变现成整片空白。

    这里判的是两套矩形的**分叉点**：`enable`／margin／回读轮／推帧的裁跟着 surface 走，
    `_proxy_rect` 跟着画布走——`live2d_widget._update_look_target` 吃 `proxy.topLeft()` 再配
    widget **自身**宽高算视线，写进 surface 桌宠就会朝自己外面看。`origin=(0,0)` 的每一条
    支路必须逐位等于丙之前（未启用 fidus 的那一半用户走的就是它）。
    """

    CANVAS = (854, 143, 461, 614)              # 画布矩形（`_layer_geometry()` 的口径）
    CONTOUR = (41, 570, 60, 400)               # 帧内不透明块：本机纵向 41..570
    ORIGIN = (44, 25)                          # (60−16, 41−16)，16 是 `_LAYER_BBOX_PAD_PX`
    SURFACE = (898, 168, 372, 561)             # 画布 + 原点平移，尺寸＝轮廓含两侧余量后夹到帧边

    def _mount(self, frame):
        """跑一次真身 `_do_layer_mount`，回 (host, 日志)。"""
        host = _host(FakeBackend([FIT]))
        host.actions = []
        host.hide = lambda: host.actions.append("hide")
        host._layer_timer = SimpleNamespace(start=lambda: host.actions.append("timer"))
        host._fidus_current_frame = lambda: frame
        host._layer_fit_start = lambda *a, **k: host.actions.append(("fit", a, k))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            host._do_layer_mount(*self.CANVAS, measured=False)
        return host, err.getvalue()

    def test_enable_gets_the_contour_while_belief_keeps_the_canvas(self):
        host, log = self._mount(_contour_frame(*self.CONTOUR))
        self.assertEqual(host._layer_backend.enabled, [self.SURFACE])
        self.assertEqual(_proxy_rect(host), self.CANVAS,
                         "信念写进 surface ⇒ 视线会算到画布外面去")
        self.assertEqual(host._layer_frame_origin, self.ORIGIN)
        self.assertEqual(host._layer_canvas_size, self.CANVAS[2:])
        fit = [a for a in host.actions if a[0] == "fit"][0]
        self.assertEqual(fit[1], self.SURFACE, "回读轮吃的也是 surface：夹移模型讲的是那块 surface")
        self.assertIn("挂载按轮廓请求 372x561", log)
        self.assertIn("← 画布 461x614@(854,143)", log)

    def test_a_fully_opaque_canvas_mounts_exactly_as_before(self):
        """整画布都看得见 ⇒ 一 px 也省不掉 ⇒ 请求逐位等于丙之前，且**一句不说**。"""
        host, log = self._mount(_contour_frame(0, 614, 0, 461))
        self.assertEqual(host._layer_backend.enabled, [self.CANVAS])
        self.assertEqual(_proxy_rect(host), self.CANVAS)
        self.assertEqual(host._layer_frame_origin, (0, 0))
        self.assertNotIn("挂载按轮廓请求", log)
        self.assertNotIn("← 画布", log)

    def test_no_opaque_pixel_speaks_and_mounts_the_canvas(self):
        """全透明帧（PNG 模式还没渲染出来／alpha 全 0）⇒ 判不出轮廓，不猜一个形状。"""
        host, log = self._mount(np.zeros((614, 461, 4), dtype=np.uint8))
        self.assertEqual(host._layer_backend.enabled, [self.CANVAS])
        self.assertIn("轮廓这一判没跑成", log)
        self.assertEqual(host._layer_frame_origin, (0, 0))

    def test_no_frame_speaks_and_mounts_the_canvas(self):
        host, log = self._mount(None)
        self.assertEqual(host._layer_backend.enabled, [self.CANVAS])
        self.assertIn("量不到轮廓", log)
        self.assertIn("按整画布挂", log)

    def test_a_contour_too_small_to_be_the_pet_is_refused_out_loud(self):
        """贴在右下角的一小坨 ⇒ 夹完只剩 27 宽，那不是轮廓而是噪声，不认（不拿它去请求挂载）。"""
        host, log = self._mount(_contour_frame(450, 460, 450, 460))
        self.assertEqual(host._layer_backend.enabled, [self.CANVAS])
        self.assertIn("不认它是轮廓", log)
        self.assertEqual(host._layer_frame_origin, (0, 0))

    def test_the_band_translates_both_rects_by_the_same_amount(self):
        """夹移是屏幕上的平移：surface 挪多少，画布信念就挪多少（两套矩形只差一个常数）。"""
        host = _host(FakeBackend([FIT]))
        host._layer_frame_origin = self.ORIGIN
        host._layer_canvas_size = self.CANVAS[2:]
        host._fit_band = BAND
        host._layer_fit_proxy_rect(898, -25, 372, 561)
        self.assertEqual(_proxy_rect(host), (854, -25 - 25 + BAND, 461, 614),
                         "减掉帧内原点才是画布，再加带厚才是屏幕坐标")

    def test_head_clip_subtracts_the_frame_origin_before_judging(self):
        """轮廓行号从**整帧**数起，而 surface 的 margin 已经含了 oy ⇒ 不减就把同一行数两次。

        画布顶在屏幕 −50（surface margin = −50+25 = −25）时头顶真的出屏 9 px；错的那一式会算出
        "轮廓顶端在屏幕行 16" 而**安静**放过——漏掉的恰好是该出声的那一类。
        """
        host = _host(FakeBackend([FIT]), band=0)
        host._fidus_current_frame = lambda: _contour_frame(*self.CONTOUR)
        host._layer_frame_origin = self.ORIGIN
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            host._layer_report_head_clip((898, -25, 372, 561), 0)
        self.assertIn("头顶被屏上沿切掉 9 逻辑 px", err.getvalue())
        self.assertEqual(len(host.bubbles), 1, f"该只有一句出声，实际 {host.bubbles}")


class TestSteadyRecheck(unittest.TestCase):
    """落定之后的稳态重查（#89）：configure 变了才重算那一发夹移。

    节奏由 `_LAYER_FIT_WATCH_TICKS` 控制，本文件把它和回读预算一起吃——所以每个用例
    先 `pump` 掉一整周期再断言，别指望"下一拍就有读数"。
    """

    def _land(self, host, be):
        """跑完挂载那一轮：夹移到 −26，然后配满 614 ⇒ 落定。"""
        host._layer_fit_start(*MOUNT)
        _pump(host, 2)
        self.assertEqual(be.positions, [FITTED], "前置：挂载轮该只发那一发夹移")
        return be.positions

    def test_unchanged_configure_sends_nothing_and_reads_once_per_period(self):
        be = FakeBackend([CLAMPED, FIT])
        host = _host(be)
        self._land(host, be)
        reads = be.reads
        results, log = _pump(host, _LAYER_FIT_WATCH_TICKS * 3)
        self.assertEqual(be.positions, [FITTED], "读数没变就不该有任何一发")
        self.assertEqual(log, "")
        self.assertEqual(set(results), {False}, "稳态不该拦推帧")
        self.assertEqual(be.reads - reads, 3,
                         "稳态成本＝每周期一次缓存读数，不是每帧一次")

    def test_band_removal_after_a_successful_clamp_is_INVISIBLE(self):
        """**本件测不到的那一格**（裁决 (b) 的字面对象）：钉成失败模式，别当已通过。

        夹移落定之后 configure 已经等于请求（461x614）；带整条撤走时 niri 重发的 configure
        **数值不变**（`min(614, 768+26)=614`），而桥接层只缓存那个数值（`state.rs` 的
        `logical_size`），没有"configure 来过一发"的序号 ⇒ 宿主侧无从发现这一相。
        要补它得动 Rust 桥（第 13 枚符号），不在本次授权面（render_host.py）内。
        已登记 `pending.md`，不赌它不发生。
        """
        be = FakeBackend([CLAMPED, FIT])
        host = _host(be)
        self._land(host, be)
        be.feed(FIT)                       # 带撤走：configure 重发，还是 461x614
        _pump(host, _LAYER_FIT_WATCH_TICKS * 3)
        self.assertEqual(be.positions, [FITTED], "这一相无从触发，所以一发都不该有")
        self.assertEqual(_proxy_rect(host), (FITTED[0], ON_SCREEN_TOP, *FIT),
                         "信念仍停在带在时那一格——这就是那条 180 px 无声错位")

    def test_narrower_usable_area_reclamps_from_the_current_margin(self):
        """带变厚 ⇒ 配出的尺寸掉下来 ⇒ 按新可用区再夹一发，并交回确认轮。"""
        be = FakeBackend([CLAMPED, FIT])
        host = _host(be)
        self._land(host, be)
        be.feed((461, 434))                # 可用区又少了 11 px（445→434 那一类）
        _pump(host, _LAYER_FIT_WATCH_TICKS)
        # ny = min(锚点 143, 434 + 当前 margin(−26) − 614) = −206
        self.assertEqual(be.positions, [FITTED, (854, -206)])
        self.assertEqual(_proxy_rect(host), (854, -206 + BAND, *FIT),
                         "信念要跟着重算的那一发走，吃的也是新带厚")
        started = list(host.started)
        be.feed(FIT)
        _pump(host, _LAYER_FIT_WAIT_TICKS)
        self.assertEqual(host.started, started,
                         "重查轮落定不许再启一轮 fidus：#78 把它排在落定之后一次")

    def test_wider_usable_area_probes_back_to_the_anchor_then_self_corrects(self):
        """宽裕相：收一发回锚点是**探针**，锚点装不下时确认轮会自己再夹回来。

        起手用"挂载轮没救回来"那一相（configure 一直停在 445）：那里 margin 已夹到 −26、
        锚点还在 143，而读数有事实可比（445→614 是真变化），所以这一支可达。
        """
        be = FakeBackend([CLAMPED])
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        # 预算那一拍是**减到 0 才判**，所以失败出口落在第 WAIT+1 拍：多泵一拍，
        # 少泵的话挂载轮还开着，下一份读数会被它自己吃掉（那是另一条正确路径）。
        _pump(host, _LAYER_FIT_WAIT_TICKS + 1)
        self.assertEqual(be.positions, [FITTED])

        be.feed(FIT)                                  # 带撤：445 → 614，看得见的一变
        _pump(host, _LAYER_FIT_WATCH_TICKS)
        self.assertEqual(be.positions, [FITTED, MOUNT[:2]],
                         "宽裕相该把位置朝锚点收那一发（回收夹移）")
        self.assertIsNone(host._fit_band, "回到锚点那一发没有夹移可记")
        self.assertEqual(_proxy_rect(host), (MOUNT[0], MOUNT[1], *FIT))

        be.feed(CLAMPED)                              # 锚点上其实装不下 ⇒ 合成器再夹
        _pump(host, _LAYER_FIT_WAIT_TICKS)
        self.assertEqual(be.positions, [FITTED, MOUNT[:2], FITTED],
                         "探针失败要自己夹回来，不该停在错位置也不该猜第二解")

    def test_fidus_running_makes_the_whole_watch_yield(self):
        """两个作者抢一个 set_position：fidus 在跑时重查整段让路（约束第 1 条的答复）。"""
        be = FakeBackend([CLAMPED, FIT])
        host = _host(be)
        self._land(host, be)
        host._fidus_busy = True
        be.feed((461, 434))
        _pump(host, _LAYER_FIT_WATCH_TICKS * 2)
        self.assertEqual(be.positions, [FITTED], "fidus 跑动期间插一发会把它的读数打乱")

    def test_old_artifact_pays_no_read_in_steady_state(self):
        """没有回读通道 ⇒ 不设锚 ⇒ 稳态一次都不读（#78 §8.1 那句成本口径不外溢）。"""
        be = NoChannelBackend()
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        results, log = _pump(host, _LAYER_FIT_WATCH_TICKS * 2)
        self.assertEqual(be.reads, 0)
        self.assertEqual(be.positions, [])
        self.assertEqual(log, "")
        self.assertEqual(set(results), {False})

    def test_anchor_survives_our_own_clamp_but_not_fidus(self):
        """锚点的归属：夹移是"被迫的"，不该把锚点搬过去，否则宽裕相无处可回收。"""
        be = FakeBackend([CLAMPED, FIT])
        host = _host(be)
        self._land(host, be)
        self.assertEqual(host._fit_anchor, MOUNT, "夹移之后锚点仍是挂载那一刻的请求")
        self.assertEqual(host._fit_margin, FITTED, "而 margin 记的是最后一次发出的那一发")
        host._fidus_owns_placement(700, 100)
        self.assertEqual(host._fit_anchor, (700, 100, *FIT),
                         "fidus 的主动摆放才是此后该被回收的位置")

    def test_watch_reports_a_broken_channel_once_per_period(self):
        """稳态读数坏了：报本周期，不撤锚也不静默消失。"""
        be = FakeBackend([CLAMPED, FIT])
        host = _host(be)
        self._land(host, be)

        def boom():
            raise RuntimeError("桥没了")

        be.logical_size = boom
        _pump(host, _LAYER_FIT_WATCH_TICKS)
        results, log = _pump(host, _LAYER_FIT_WATCH_TICKS)
        self.assertIn("稳态重查回读异常", log)
        self.assertEqual(set(results), {False}, "读坏了不该把推帧拦下来")
        self.assertIsNotNone(host._fit_anchor, "撤锚＝把「这一段不再管事」当成结论")


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


class TestMeasuredPositionWins(unittest.TestCase):
    """「测量赢」（人工裁决 2026-10-02）：位置来自屏幕读数时，回读轮**只报数不改位**。

    这不是"夹移算错了"：`ny = lh + y − h` 展开后是"可用高 − 请求高"，与 `y` 来自信念
    还是来自测量**无关**，所以它一定会把量出来的那个位置搬走。让位是这条裁决的全部
    内容，代价也一并写在脸上：**带后来出现／消失时不再自动回位**，#89 那一族的无声
    180 px 在这里变成"有声不改"。
    """

    def test_clamped_configure_moves_nothing_and_speaks(self):
        be = FakeBackend([CLAMPED])
        host = _host(be)
        host._layer_fit_start(*MOUNT, measured=True)
        results, log = _pump(host, 1)
        self.assertEqual(results, [False], "没发夹移就没有「在等新的 configure」，别拦帧")
        self.assertEqual(be.positions, [], "测量位不许被回读轮搬走")
        self.assertIsNone(host._fit_rect)
        self.assertEqual(host.started, [], "20 s 装不下两发：measured 挂载不排第二轮 fidus")
        self.assertIn("按测量位", log)
        self.assertTrue(any("屏幕放不下" in b for b in host.bubbles),
                        "后果是空白（尺寸门控丢帧），气泡得说这件事而不是说位置")

    def test_matching_configure_is_silent_and_starts_no_fidus(self):
        be = FakeBackend([MOUNT[2:]])
        host = _host(be)
        host._layer_fit_start(*MOUNT, measured=True)
        results, log = _pump(host, 1)
        self.assertEqual(results, [False])
        self.assertEqual(be.positions, [])
        self.assertEqual(host.bubbles, [])
        self.assertEqual(log, "")
        self.assertEqual(host.started, [], "`_fit_fidus` 起头就是 None，不是「落定后清掉」")

    def test_negative_measured_margin_still_judges_the_head(self):
        """测量位允许是负的（不夹不移），于是削头那一判第一次在这条路上真跑到。"""
        be = FakeBackend([(461, 512)])
        host = _host(be, band=0)
        host._fidus_current_frame = lambda: _silhouette_frame(41, 570)
        host._layer_fit_start(213, -102, 461, 614, measured=True)
        _pump(host, 1)
        self.assertEqual(be.positions, [])
        self.assertEqual(len(host.bubbles), 2, f"该两句都出声：{host.bubbles}")
        self.assertTrue(any("屏幕放不下" in b for b in host.bubbles))
        self.assertTrue(any("屏幕高度不够" in b for b in host.bubbles))

    def test_steady_recheck_sees_the_band_but_does_not_move(self):
        """带变厚：稳态重查照旧看见这一相，只出声、不改位（那句代价的正面写法）。"""
        be = FakeBackend([FIT])
        host = _host(be)
        host._layer_fit_start(*MOUNT, measured=True)
        _pump(host, 1)
        self.assertEqual(be.positions, [], "前置：落定那一轮什么都没挪")

        be.feed((461, 434))                # 带变厚 ⇒ configure 掉下来，是真变化
        results, log = _pump(host, _LAYER_FIT_WATCH_TICKS)
        self.assertEqual(be.positions, [], "看见了也不回摆")
        self.assertIn("不回摆", log)
        self.assertEqual(set(results), {False}, "不改位就没有要等的新 configure")

    def test_unmeasured_mount_keeps_the_old_clamp(self):
        """反向门：`measured` 默认 False ⇒ 未启用 fidus 的那一半用户行为逐位不变。"""
        be = FakeBackend([CLAMPED, MOUNT[2:]])
        host = _host(be)
        host._layer_fit_start(*MOUNT)
        _pump(host, 2)
        self.assertEqual(be.positions, [FITTED])
        self.assertEqual(host.started, [(FITTED[0], FITTED[1], *MOUNT[2:])])


if __name__ == "__main__":
    unittest.main()
