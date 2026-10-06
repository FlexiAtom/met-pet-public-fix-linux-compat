"""fidus 定位的产品侧回归：不碰真桌面、不碰 fidus 本体。

引擎一律是假的（`FakeEngine`）—— 本文件测的是**判据与接线**：
喂给引擎的信念是不是贴片中心、闭环拦不拦得住钉死的读数、失败是否一律退回。
真实读数与真机数字在 `scripts/fidus_positioning_probe/`（H8–H11），不在这里。
"""
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meapet.desktop import fidus_position as FP  # noqa: E402


def synthetic_frame(w: int = 400, h: int = 300) -> np.ndarray:
    """一块不透明矩形 + 里面有随机纹理：够注册，且质心不在 surface 中心。"""
    rng = np.random.default_rng(7)
    frame = np.zeros((h, w, 4), dtype=np.uint8)
    frame[80:260, 60:240, 3] = 255
    frame[100:220, 90:210, :3] = rng.integers(0, 255, (120, 120, 3), dtype=np.uint8)
    return frame


class FakeEngine:
    """按 `FidusEngine` 的面冒充：注册记窗口、读数由 `reading` 回调决定。"""

    def __init__(self, reading=None, refuse=False, ceiling=0.9):
        self.reading = reading or (lambda edge: (0.0, 0.0, 0.9))
        self.refuse = refuse
        self.ceiling = ceiling
        self.calibrations = 0
        self.registrations: list[tuple[int, tuple[float, float]]] = []

    def calibrate(self) -> None:
        self.calibrations += 1

    def register(self, box, initial_center):
        if self.refuse:
            raise FP.EngineError("register_target: FidusUntrackable")
        self.registrations.append((int(box.shape[0]),
                                   None if initial_center is None else tuple(initial_center)))
        return self.ceiling

    def estimate(self):
        return self.reading(self.registrations[-1][0])


class TestCandidates(unittest.TestCase):
    def test_boxes_stay_inside_the_opaque_contour(self):
        cands = FP.iter_candidates(synthetic_frame())
        self.assertTrue(cands)
        for cand in cands:
            self.assertTrue(np.all(cand.box[..., 3] > FP.ALPHA_MIN))

    def test_ladder_is_largest_first(self):
        edges = [c.edge for c in FP.iter_candidates(synthetic_frame())]
        self.assertEqual(edges, sorted(edges, reverse=True))

    def test_anchor_maps_between_box_centre_and_surface_centre(self):
        frame = synthetic_frame()
        h, w = frame.shape[:2]
        for cand in FP.iter_candidates(frame):
            # 盒在 surface 内 ⇒ 锚点幅度不会超过两侧余量的一半
            self.assertLessEqual(abs(cand.anchor[0]), (w - cand.edge) / 2.0 + 1e-9)
            self.assertLessEqual(abs(cand.anchor[1]), (h - cand.edge) / 2.0 + 1e-9)
            # 换算就是"加/减同一个偏移"
            self.assertEqual(cand.belief_for((0.0, 0.0)), cand.anchor)


class TestLocate(unittest.TestCase):
    def _move_loop(self, dx_seen):
        def move(dx, dy):
            dx_seen.append((dx, dy))
            return True          # `move` 的契约是"摆出去了没有"（四向判据的输入）
        return move

    def test_feeds_template_centre_not_surface_centre(self):
        seen = []
        eng = FakeEngine(reading=lambda edge: (150.0, 170.0, 0.9))
        FP.locate(eng, synthetic_frame(), (200.0, 240.0),
                  move=self._move_loop(seen))
        cand = FP.iter_candidates(synthetic_frame())[0]
        self.assertEqual(eng.registrations[0][1], cand.belief_for((200.0, 240.0)))
        self.assertNotEqual(eng.registrations[0][1], (200.0, 240.0))

    def test_reading_that_ignores_the_move_is_refused(self):
        """假峰钉住读数：请求摆过去了，读数一动不动 ⇒ 判不可信，不许回给调用方。"""
        eng = FakeEngine(reading=lambda edge: (150.0, 170.0, 0.9))
        fix = FP.locate(eng, synthetic_frame(), (200.0, 240.0), move=self._move_loop([]))
        self.assertIsNone(fix)
        self.assertEqual(eng.registrations[-1][1], (150.0 + FP.MOVE_PX, 170.0),
                         "最后一发是闭环重注册 ⇒ 判"
                         "不可信之前确实跑完了一整发闭环，不是没摆就退")

    def test_consistent_move_yields_corrected_centre(self):
        state = {"dx": 0.0}

        def move(dx, _dy):
            state["dx"] += dx
            return True

        eng = FakeEngine(reading=lambda edge: (150.0 + state["dx"], 170.0, 0.9))
        fix = FP.locate(eng, synthetic_frame(), (200.0, 240.0), move=move)
        cand = FP.iter_candidates(synthetic_frame())[0]
        self.assertIsNotNone(fix)
        # 答案取自位移**之前**那一发（贴片中心 150,170）⇒ surface 中心 = 减锚点
        self.assertEqual(fix.center, (150.0 - cand.anchor[0], 170.0 - cand.anchor[1]))
        self.assertEqual(fix.edge, cand.edge)
        # 闭环那一发的先验 = 引擎自己的稳态读数 + 请求位移，不是我方的陈旧信念
        self.assertEqual(eng.registrations[-1][1], (150.0 + FP.MOVE_PX, 170.0))
        self.assertNotEqual(eng.registrations[-1][1],
                            cand.belief_for((200.0 + FP.MOVE_PX, 240.0)))

    def test_readings_that_never_settle_are_refused(self):
        """逐发爬 10 px 的读数＝没定住：不许拿它当答案，`ACQUIRE_READS` 发内退回。"""
        state = {"n": 0}

        def crawling(edge):
            state["n"] += 1
            return (150.0 + 10.0 * state["n"], 170.0, 0.9)

        eng = FakeEngine(reading=crawling)
        self.assertIsNone(FP.locate(eng, synthetic_frame(), (200.0, 240.0),
                                    move=self._move_loop([])))
        self.assertGreaterEqual(state["n"], FP.ACQUIRE_READS)

    def test_all_candidates_refused_returns_none(self):
        eng = FakeEngine(refuse=True)
        self.assertIsNone(FP.locate(eng, synthetic_frame(), (0.0, 0.0),
                                    move=self._move_loop([])))

    def test_readings_that_always_throw_return_none(self):
        class Lost(FakeEngine):
            def estimate(self):
                raise FP.EngineError("estimate: FidusTargetLost")

        self.assertIsNone(FP.locate(Lost(), synthetic_frame(), (0.0, 0.0),
                                    move=self._move_loop([])))

    def test_lost_reads_are_tolerated_until_it_settles(self):
        """失稳那几发不算终点：每发把窗撑大 64 px，H11 实测偏 440 px 时第 6 发才锁。

        首版一遇异常就退 —— 等于把唯一那条"先验错得远"的救回路自己剪掉（H14 实测
        信念偏 ≥150 px 时**首发即抛**，那条路就是这么被掐死的）。
        """
        state = {"n": 0, "moved": False}

        class Recovering(FakeEngine):
            def estimate(self):
                state["n"] += 1
                if state["n"] <= 3:
                    raise FP.EngineError("estimate: FidusTargetLost")
                dx = FP.MOVE_PX if state["moved"] else 0.0
                return (150.0 + dx, 170.0, 0.9)

        def move(_dx, _dy):
            state["moved"] = True
            return True

        eng = Recovering()
        fix = FP.locate(eng, synthetic_frame(), (200.0, 240.0), move=move)
        cand = FP.iter_candidates(synthetic_frame())[0]
        self.assertIsNotNone(fix)
        self.assertGreaterEqual(state["n"], 5)          # 抛了 3 发仍在读
        self.assertEqual(fix.center, (150.0 - cand.anchor[0], 170.0 - cand.anchor[1]))

    def test_size_wins_over_ceiling(self):
        """**先大后小**：小贴片即使 ceiling 高出一倍也不许赢过大贴片。

        这条是被实机打出来的规则，不是审美。H14 在远偏先验下量到 `48²`/`32²`
        报出最坏 +173/+126 px 的数，而**位移闭环照样通过**（孪生峰对先验不变）；
        `96²`/`160²` 同几何下 ≤ 2.81 px。ceiling 与自相似度反号 ⇒ 越小越高，
        所以"跨档取最大 ceiling"必然一路选到最能撒谎的那一档。
        """
        frame = synthetic_frame()
        cands = FP.iter_candidates(frame)
        max_edge = max(c.edge for c in cands)
        edges_seen = []

        def ceiling_by_edge(box, initial_center):
            edge = int(box.shape[0])
            edges_seen.append(edge)
            return 0.9 if edge != max_edge else 0.3   # 偏把最高的给小档

        eng = FakeEngine(reading=lambda edge: (0.0, 0.0, 0.9))
        eng.register = ceiling_by_edge  # type: ignore[method-assign]
        picked = FP.select_template(eng, frame, (0.0, 0.0))
        self.assertIsNotNone(picked)
        self.assertEqual(picked[0].edge, max_edge)
        self.assertEqual(set(edges_seen), {max_edge})   # 更小档根本不该去问

    def test_highest_ceiling_wins_within_the_size(self):
        """同一档内几只盒都被注册成功时，取 ceiling 最高的那只。

        方向曾写反：`H7` 的 census 用 argmin 排的是 `s_engine`（自相似度本身），
        而 ceiling 与它反号。实机第一轮就照反的方向选到了地板 0.05 的那只。
        """
        frame = synthetic_frame()
        cands = FP.iter_candidates(frame)
        top = max(c.edge for c in cands)
        siblings = [c for c in cands if c.edge == top]
        self.assertGreater(len(siblings), 1, "合成帧该给出同档多只候选")
        seen = []

        def ceiling_by_order(box, initial_center):
            seen.append(box)
            return 0.9 if len(seen) == 2 else 0.3     # 最高的分给第二只，看它会不会被取走

        eng = FakeEngine(reading=lambda edge: (0.0, 0.0, 0.9))
        eng.register = ceiling_by_order  # type: ignore[method-assign]
        picked = FP.select_template(eng, frame, (0.0, 0.0))
        self.assertIsNotNone(picked)
        self.assertEqual(picked[1], 0.9)
        self.assertEqual(picked[0].edge, top)
        self.assertTrue(np.array_equal(picked[0].box, seen[1]))

    def test_falls_to_smaller_size_when_top_is_refused(self):
        """最大档全被引擎拒了，才降一档去问 —— 下界之内仍要试完。"""
        frame = synthetic_frame()
        cands = FP.iter_candidates(frame)
        top = max(c.edge for c in cands)
        ladder = sorted({c.edge for c in cands}, reverse=True)
        second = ladder[1]

        def register(box, initial_center):
            if int(box.shape[0]) == top:
                raise FP.EngineError("register_target: FidusUntrackable")
            return 0.4

        eng = FakeEngine(reading=lambda edge: (0.0, 0.0, 0.9))
        eng.register = register  # type: ignore[method-assign]
        picked = FP.select_template(eng, frame, (0.0, 0.0))
        self.assertIsNotNone(picked)
        self.assertEqual(picked[0].edge, second)   # 降一档就停，不再往小处走

    def test_ladder_has_no_lying_tiers(self):
        """阶梯下界是**安全边界**：H14 量到会安静撒谎的那两档不许在列。"""
        self.assertNotIn(32, FP.SIZES)
        self.assertNotIn(48, FP.SIZES)
        self.assertGreaterEqual(min(FP.SIZES), 64)


class TestBelieflessPremount(unittest.TestCase):
    """预挂载那一发的形状：三处都不喂先验 + 2 px 容差 + 96² 档限。

    形状必须与 H25 量过的那一条逐位同形（探针里 `beliefless()` 把 `register` 包成
    永远传 `None`）——喂半截等于换一个没量过的判据，而那正是本件要躲开的失效方式。
    """

    @staticmethod
    def _honest_move(state):
        def move(dx, _dy):
            state["dx"] += dx
            return True
        return move

    def test_no_prior_reaches_the_engine_anywhere(self):
        state = {"dx": 0.0}
        eng = FakeEngine(reading=lambda edge: (150.0 + state["dx"], 170.0, 0.9))
        fix = FP.locate(eng, synthetic_frame(), None, move=self._honest_move(state),
                        tol_px=FP.PREMOUNT_TOL_PX, min_edge=FP.PREMOUNT_MIN_EDGE)
        self.assertIsNotNone(fix)
        self.assertEqual([prior for _edge, prior in eng.registrations],
                         [None] * len(eng.registrations),
                         "选型／定窗／闭环重注册一处都不该喂")

        # 轮数钉住"只有两轮冷获取"：选型那一轮自己会问几只同档候选，单独数一遍作基线。
        probe = FakeEngine(reading=lambda edge: (150.0, 170.0, 0.9))
        FP.select_template(probe, synthetic_frame(), None, min_edge=FP.PREMOUNT_MIN_EDGE)
        self.assertEqual(len(eng.registrations), len(probe.registrations) + 2,
                         "定窗与闭环各一发，没有第四发")

    def _half_honest(self):
        """摆过去了，但读数只跟上台差 3.06 px、纵向飘 10.69 px——H25 臂 B 的实测残差。"""
        state = {"moved": False}

        def move(_dx, _dy):
            state["moved"] = True
            return True

        def reading(_edge):
            if not state["moved"]:
                return (150.0, 170.0, 0.9)
            return (150.0 + FP.MOVE_PX - 3.06, 170.0 - 10.69, 0.9)

        return FakeEngine(reading=reading), move

    def test_the_24_px_tolerance_lets_a_3_px_wrong_lock_through(self):
        """旧容差那一档**放行**：这条不是夸新判据，是钉住"容差是判据的一半"这件事。"""
        eng, move = self._half_honest()
        fix = FP.locate(eng, synthetic_frame(), None, move=move)
        self.assertIsNotNone(fix, "24 px 容差本该放过 3.06 px 的错锁（H25 实测形态）")

    def test_the_2_px_tolerance_refuses_a_3_px_wrong_lock(self):
        eng, move = self._half_honest()
        self.assertIsNone(FP.locate(eng, synthetic_frame(), None, move=move,
                                    tol_px=FP.PREMOUNT_TOL_PX))

    def test_min_edge_stops_the_ladder_instead_of_descending_onto_a_lying_tier(self):
        """1080p 那道算术闸会把可用档压到 64²：响亮退回，**不许悄悄降档**（H14 撒谎档）。"""
        frame = synthetic_frame()
        top = max(c.edge for c in FP.iter_candidates(frame))

        def register(box, _prior):
            if int(box.shape[0]) > 64:
                raise FP.EngineError("register_target: FidusUntrackable")
            return 0.9

        eng = FakeEngine(reading=lambda edge: (0.0, 0.0, 0.9))
        eng.register = register  # type: ignore[method-assign]
        self.assertIsNone(FP.select_template(eng, frame, None, min_edge=FP.PREMOUNT_MIN_EDGE))
        self.assertNotIn(64, [edge for edge, _p in eng.registrations],
                         "档限以下连问都不该问")

        # 反向门：没有档限时 64² 仍会被取走 ⇒ 上面那个 None 是档限给的，不是阶梯空了。
        loose = FakeEngine(reading=lambda edge: (0.0, 0.0, 0.9))
        loose.register = register  # type: ignore[method-assign]
        picked = FP.select_template(loose, frame, None)
        self.assertIsNotNone(picked)
        self.assertEqual(picked[0].edge, 64)
        self.assertLess(picked[0].edge, top)


class TestFourDirectionProbe(unittest.TestCase):
    """四向探针位移的形状（人工裁决 2026-10-02「四向的探针位移，看哪个能用再采信哪个」）。

    单向那一发在平铺下会摆不出：`pet.move()` 屏上 0 px（H24），能挪的只有窗口内的子控件，
    而画布贴哪一边全看合成器怎么摆 ⇒ 右/下/左/上逐个试，**命中即采信**，全败才回 `None`。
    计数一律拿"注册了几回"当尺子：摆不出的那一向**不重注册**（重注册＝真把位移摆出去了），
    所以「第一向就成」与「第一向摆不出、第二向成」两场的注册数必须相等。
    """

    D1, D2, D3, D4 = FP.PREMOUNT_MOVES

    @staticmethod
    def _select_count():
        """选型那一轮自己问了几只候选——闭环的账要从这个基线往上算。"""
        eng = FakeEngine(reading=lambda edge: (150.0, 170.0, 0.9))
        FP.select_template(eng, synthetic_frame(), None, min_edge=FP.PREMOUNT_MIN_EDGE)
        return len(eng.registrations)

    @classmethod
    def _locate(cls, applied):
        """`applied`：方向 → 该向**实际**摆出去的增量；不在表里的方向回"摆不出"。

        赋值而非累加，钉的是宿主那条契约：每向都从 `_content_probe_base` 起（幂等），
        上一向没成不会把下一向的增量垫进去。
        """
        state = {"dx": 0.0, "dy": 0.0}
        calls: list = []

        def move(dx, dy):
            calls.append((dx, dy))
            got = applied.get((dx, dy))
            if got is None:
                return False
            state["dx"], state["dy"] = got
            return True

        eng = FakeEngine(
            reading=lambda edge: (150.0 + state["dx"], 170.0 + state["dy"], 0.9))
        fix = FP.locate(eng, synthetic_frame(), None, move=move,
                        moves=FP.PREMOUNT_MOVES,
                        tol_px=FP.PREMOUNT_TOL_PX,
                        min_edge=FP.PREMOUNT_MIN_EDGE)
        return fix, eng, calls

    def test_first_usable_direction_is_adopted_and_the_rest_are_not_tried(self):
        fix, eng, calls = self._locate({self.D1: (48.0, 0.0)})
        self.assertEqual(calls, [self.D1], "命中即采信：不该再去惊动另外三向")
        self.assertIsNotNone(fix)
        self.assertEqual(len(eng.registrations), self._select_count() + 2, "定窗与闭环各一发")
        cand = FP.iter_candidates(synthetic_frame())[0]
        self.assertEqual(fix.center, (150.0 - cand.anchor[0], 170.0 - cand.anchor[1]),
                         "答案仍取自位移**之前**那一发，与采信哪一向无关")

    def test_a_direction_that_cannot_be_placed_costs_no_re_registration(self):
        ok, _eng, _calls = self._locate({self.D1: (48.0, 0.0)})
        fix, eng, calls = self._locate({self.D2: (0.0, 48.0)})
        self.assertEqual(calls, [self.D1, self.D2], "摆不出的那一向要换下一向")
        self.assertIsNotNone(fix)
        self.assertEqual(fix.center, ok.center, "采信哪一向，答案都该是同一个中心")
        self.assertEqual(len(eng.registrations), self._select_count() + 2,
                         "白摆的那一向连注册都不该发生 ⇒ 与「第一向就成」同一本账")

    def test_all_four_directions_unusable_returns_none(self):
        fix, eng, calls = self._locate({})
        self.assertEqual(calls, list(FP.PREMOUNT_MOVES), "四向都得试到，不能第一向就弃")
        self.assertIsNone(fix)
        self.assertEqual(len(eng.registrations), self._select_count() + 1,
                         "只剩定窗那一发：没有为摆不出的方向重注册")

    def test_a_direction_that_fails_the_tolerance_moves_to_the_next(self):
        """合成器没照请求摆（这里右向只走成 38 px）⇒ 那一向判失败，换下一向，别硬采信。"""
        fix, eng, calls = self._locate({self.D1: (38.0, 0.0), self.D2: (0.0, 48.0)})
        self.assertEqual(calls, [self.D1, self.D2])
        self.assertIsNotNone(fix, "第二向诚实 ⇒ 采信第二向")
        self.assertEqual(len(eng.registrations), self._select_count() + 3,
                         "超容差那一向是真摆出去了才判的，注册照算")


class TestExecutorCarriesTheKnobs(unittest.TestCase):
    """`tol_px` / `min_edge` 得真走得进 fidus 线程——判据不能只在 GUI 侧成立。"""

    def test_premount_arguments_reach_locate(self):
        done = threading.Event()
        captured = {}

        def fake_locate(_engine, _frame, believed_center, **kw):
            captured["prior"] = believed_center
            captured["tol_px"] = kw.get("tol_px")
            captured["min_edge"] = kw.get("min_edge")
            captured["moves"] = kw.get("moves")
            done.set()
            return None

        with mock.patch.object(FP, "locate", fake_locate), \
                mock.patch.object(FP, "_ENGINE", object()):
            FP.request_locate(synthetic_frame(), None,
                              move=lambda _dx, _dy: True,
                              on_done=lambda _fix: None,
                              tol_px=FP.PREMOUNT_TOL_PX,
                              min_edge=FP.PREMOUNT_MIN_EDGE,
                              moves=FP.PREMOUNT_MOVES)
            self.assertTrue(done.wait(5.0), "作业没被 fidus 线程取走")
        self.assertIsNone(captured["prior"])
        self.assertEqual(captured["tol_px"], FP.PREMOUNT_TOL_PX)
        self.assertEqual(captured["min_edge"], FP.PREMOUNT_MIN_EDGE)
        self.assertEqual(captured["moves"], FP.PREMOUNT_MOVES)
        FP.shutdown()


class TestPremountOrdering(unittest.TestCase):
    """「量完再挂」的接线：测量在前、挂载在后，量不出来就**不切**（人工裁决 2026-10-02）。

    这四条退路各自的气泡说的是不同的缺口（没带引擎／画面拿不到／探针摆不下／没量准），
    合成一句"没能量准位置"会把人引去等一次根本不会到来的校准。
    """

    BELIEF = (300, 200, 160, 160)      # `_layer_geometry()` 那份已知在撒谎的信念矩形
    CENTER = (400.0, 300.0)            # 假引擎量出来的 surface 中心（屏幕坐标）

    class FakeTimer:
        def __init__(self):
            self.starts = 0
            self.stops = 0

        def start(self):
            self.starts += 1

        def stop(self):
            self.stops += 1

    class FakeWidget:
        def __init__(self, x=0, y=0, size=200):
            self._x, self._y, self._size = x, y, size
            self.moved = []
            self._proxy_rect = None

        def x(self):
            return self._x

        def y(self):
            return self._y

        def width(self):
            return self._size

        def height(self):
            return self._size

        def move(self, x, y):
            self.moved.append((int(x), int(y)))
            self._x, self._y = int(x), int(y)

    class FakeLayerBackend:
        def __init__(self):
            self.enabled = []
            self.positions = []
            self.destroyed = 0

        def enable(self, _screen, w, h, x, y):
            self.enabled.append((int(x), int(y), int(w), int(h)))

        def set_position(self, x, y):
            self.positions.append((int(x), int(y)))

        def destroy_context(self):
            self.destroyed += 1

    def _host(self, frame="ok", tiled=False):
        """几何**可变**的假 host：常态把窗口做成"就是画布那么大"。

        刻意的起点是 `(300,200)` 处一只 160×160 的窗口装着 160×160 的画布——`window_mask`
        关时产品码就是这个形状（`widget_x=0`、`window_width == widget_width`），于是
        `_content_probe_room()` 四数全 0，一发探针都摆不出（人工 2026-10-03 的 ②「恒为否」）。
        测量那一路必须先重请求一次窗口尺寸才谈得上判据，所以这里必须让 `resize` 真的改数。
        `tiled=True` 复现平铺吃尺寸请求：`resize` 叫了，什么也没变。
        """
        from meapet.desktop.render_host import PetRenderHostMixin

        host = object.__new__(PetRenderHostMixin)
        # ask_float=False：闸门自有一组用例（TestFloatGate）。这里只测挂载顺序，而且
        # 这个假 host 不是 QWidget，真对话框会当场炸在 `QDialog(parent)` 上。
        host.config = {"fidus": {"enabled": True, "ask_float": False}}
        host.backend = TestPremountOrdering.FakeLayerBackend()
        host._layer_backend = host.backend
        widget = host.sprite_label = TestPremountOrdering.FakeWidget(size=160)
        host.bubbles = []
        host.actions = []
        host.shifts = []
        geom = {"x": 300, "y": 200, "w": 160, "h": 160}
        host._geom = geom

        def _resize(w, h):
            if tiled:
                return                      # 平铺：尺寸请求被吃，实得还是那个预设
            geom["w"], geom["h"] = int(w), int(h)
            host.actions.append(("resize", int(w), int(h)))

        def _move(x, y):
            geom["x"], geom["y"] = int(x), int(y)
            host.actions.append(("move", int(x), int(y)))

        host.x = lambda: geom["x"]
        host.y = lambda: geom["y"]
        host.width = lambda: geom["w"]
        host.height = lambda: geom["h"]
        host.resize = _resize
        host.move = _move
        host.clearMask = lambda: host.actions.append("clearMask")
        host.hide = lambda: host.actions.append("hide")
        host.show = lambda: host.actions.append("show")
        host.raise_ = lambda: host.actions.append("raise")
        host.enabled_at = []
        real_enable = host.backend.enable

        def _enable(_screen, w, h, x, y):
            real_enable(_screen, w, h, x, y)
            # 挂载那一刻的窗口尺寸：缩回没缩回，只有当场取才作数
            host.enabled_at.append((geom["w"], geom["h"]))

        host.backend.enable = _enable
        # 与真码同一个式子（顶层窗口原点 + 子控件偏移）：放大窗口时画布的屏幕位置
        # 该逐位不动，`_pending_mount` 才继续有效——这条只能靠"真的算一遍"来钉。
        host._layer_geometry = lambda: (geom["x"] + widget.x(), geom["y"] + widget.y(),
                                       widget.width(), widget.height())
        host._fidus_current_frame = (lambda: None) if frame is None else \
            (lambda: np.zeros((200, 200, 4), dtype=np.uint8))
        host._fidus_timer = TestPremountOrdering.FakeTimer()
        host._mount_timer = TestPremountOrdering.FakeTimer()
        host._layer_timer = TestPremountOrdering.FakeTimer()
        host._layer_fit_start = lambda *a, **k: host.actions.append(("fit", a, k))
        host._show_bubble = lambda text, *_a, **_k: host.bubbles.append(text)
        # 这批钉的是「谁先被调用」，而 `have_engine()` 是 `_set_layer_mode` 的第一道门：
        # CI 不带 fidus 时它为假，流程在门前就出口，顺序压根走不到被钉的那一段。
        # 引擎在场与否则由 `TestEnginePresence` 与下面那几条 no_engine 子测试各自钉。
        gate = mock.patch.object(FP, "have_engine", return_value=True)
        gate.start()
        self.addCleanup(gate.stop)
        return host

    def _start(self, host):
        """走 `_set_layer_mode(True)` 起工，回捕 `request_locate` 收到的那一份参数。

        引擎与线程都不在这里出现：那一发被假函数截住，测试自己决定什么时候把结果交回去
        ——「测量在前、挂载在后」这条顺序只能在**由谁先被调用**上钉。
        """
        box = {}

        def fake_request(frame, prior, *, move, on_done, log, **kw):
            box["prior"] = prior
            box["move"] = move
            box["on_done"] = on_done
            box.update(kw)

        patcher = mock.patch.object(FP, "request_locate", fake_request)
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch("meapet.desktop.render_host.safe_print"):
            host._set_layer_mode(True)
        return box

    def test_mount_waits_for_the_measurement(self):
        from meapet.desktop.render_host import _FIDUS_SETTLE_TICKS

        host = self._host()
        slack = host._probe_slack_px()
        box = self._start(host)
        self.assertEqual(host.backend.enabled, [], "量之前一次 enable 都不许有")
        self.assertEqual(host.backend.positions, [], "预挂载不经 `set_position`（无 ctx 时静默 no-op）")
        self.assertEqual(host._pending_mount, TestPremountOrdering.BELIEF)
        self.assertEqual(host._geom["w"],
                         160 + slack + host._PROBE_ROOM_MARGIN_PX,
                         "测量期间窗口该往右下长出缺的那一段（尺寸重请求落地了）")
        self.assertEqual(host._layer_geometry(), TestPremountOrdering.BELIEF,
                         "画布没动 ⇒ `_pending_mount` 那份矩形继续有效")
        self.assertIsNone(box["prior"], "信念已知在撒谎 ⇒ 这一发一个先验都不喂")
        self.assertEqual(box["tol_px"], FP.PREMOUNT_TOL_PX)
        self.assertEqual(box["min_edge"], FP.PREMOUNT_MIN_EDGE)
        self.assertEqual(box["moves"], FP.PREMOUNT_MOVES,
                         "四向逐个试是这一发的判据，参数得真走得进 fidus 线程")

        # 闭环那一发的手：预挂载只能伸子控件（H 手），摆完还得把 fidus 线程放行。
        req = threading.Event()
        outcome: list = [None]
        host._fidus_move_req = (FP.MOVE_PX, 0.0, req, host._fidus_round, outcome)
        for _ in range(_FIDUS_SETTLE_TICKS + 1):
            host._fidus_service()
        self.assertEqual(host.sprite_label.moved, [(0, 0), (int(FP.MOVE_PX), 0)],
                         "位移该摆在子控件上，且每次从原位出发（幂等才有已知增量）")
        self.assertEqual(host.backend.positions, [], "那只手不是 `set_position`")
        self.assertTrue(req.is_set(), "摆完要放行，不许让 fidus 线程干等 3 秒")
        self.assertTrue(outcome[0], "摆出去了要如实回 True——四向判据就吃这个数")

        fix = FP.Fix(center=TestPremountOrdering.CENTER, conf=0.9, edge=96, ceiling=0.9)
        box["on_done"](fix)
        host._fidus_service()
        w, h = TestPremountOrdering.BELIEF[2:]
        self.assertEqual(host.backend.enabled,
                         [(400 - w // 2, 300 - h // 2, w, h)],
                         "enable 收到的该是测量中心反解出的矩形")
        self.assertEqual(host.enabled_at[-1], (160, 160),
                         "挂载那一刻窗口已经缩回常态：带着放大的透明边挂上去就是双倍热区")
        self.assertEqual(host.sprite_label.moved[-1], (0, 0),
                         "`Fix.center` 是位移**之前**的读数 ⇒ 挂载前该把探针位移吃掉")
        self.assertEqual((host.x(), host.y(), host.width(), host.height()),
                         (300, 200, 160, 160), "挂之前窗口要缩回常态")
        self.assertIsNone(host._pending_mount)
        self.assertFalse(host._fidus_busy)
        self.assertEqual(host._mount_timer.stops, 1)

    def test_probe_window_grows_right_and_down_without_touching_the_canvas(self):
        """①「没有再次请求一个正确大小的 Qt 窗口」+ ②「四向空档恒为否」的正面钉法。

        常态那条算术（`window_mask` 关 ⇒ 窗口就是画布）必须**当场复现**成四数全 0，
        否则这一路测的是"本来就摆得出去"的假场景。长完右/下各得 `slack`；
        **左/上仍是 0，这是设计而非缺口**：把画布摆到窗口中间要靠 `self.move()` 抵消，
        而 C 手在 Wayland 屏上 0 px（H24）⇒ 抵消不掉，只会让桌宠在测量一开始就真跳一份 slack。
        """
        host = self._host()
        slack = host._probe_slack_px()
        grow = slack + host._PROBE_ROOM_MARGIN_PX
        self.assertEqual(host._content_probe_room(), (0, 0, 0, 0),
                         "常态窗口==画布 ⇒ 右/下空档为 0（人工 2026-10-03 的 ②，就是这条 bug）")
        widget_before = (host.sprite_label.x(), host.sprite_label.y(), host.sprite_label.width())
        want, got = host._request_probe_window()
        self.assertEqual(want, (160 + grow, 160 + grow))
        self.assertEqual(got, want, "假 host 的 resize 落地——被吃那一臂另有用例")
        self.assertGreater(host._content_probe_room()[0], slack,
                           "空档要**严格大于**位移量：合成器少给 1 px 就不该把可用方向判死")
        self.assertEqual(host._content_probe_room(), (grow, grow, 0, 0))
        self.assertEqual((host.sprite_label.x(), host.sprite_label.y(), host.sprite_label.width()),
                         widget_before, "画布一码不动：动了就是拿假位移去凑四向")
        self.assertEqual(host.sprite_label.moved, [])
        self.assertEqual([a for a in host.actions if isinstance(a, tuple) and a[0] == "move"], [],
                         "测量路上一次 `self.move()` 都不许有（niri 下 Qt 拿不到自己的窗口坐标）")
        self.assertEqual(host.actions.count("clearMask"), 0,
                         "长出去那一段在裁剪区外，不该动 mask：清了就得有东西把它还回来，而这条路上没有")
        host._restore_probe_window()
        self.assertEqual((host.width(), host.height()), (160, 160))
        self.assertIsNone(host._probe_saved)
        self.assertEqual(host.sprite_label.moved, [])
        host.actions.clear()
        host._restore_probe_window()
        self.assertEqual(host.actions, [], "没放大过就是空转，不许二次复原乱改几何")

    def test_a_canvas_that_changed_during_enlargement_is_mounted_as_it_now_is(self):
        """重发几何把画布改小时，挂载矩形跟着实际几何走，不留在按下去的那一数。

        fidus 量的是**此刻屏上**那份内容；`_pending_mount` 若还是按下那一刻的 (w,h)，
        `enable` 出来的 surface 就比量出来的中心偏一份尺寸差——而这份偏差没人会说出来。
        """
        host = self._host()
        real_resize = host.resize

        def resize_that_reshrinks_the_canvas(w, h):
            real_resize(w, h)
            host.sprite_label._size = 120       # 模拟 viewport 重算把画布改小

        host.resize = resize_that_reshrinks_the_canvas
        with mock.patch("meapet.desktop.render_host.safe_print"):
            self._start(host)
        self.assertEqual(host._pending_mount, host._layer_geometry(),
                         "挂载矩形必须等于画布此刻的矩形——否则这一发挂的是旧尺寸")
        self.assertEqual(host._pending_mount[2:], (120, 120))

    def _refusal(self, label):
        """把一条退路跑到头，回 (host, 那一发的参数)。

        `no_room` 用的是**尺寸请求被吃**那一臂（平铺）：请求放大叫了、实得没变，
        于是四向空档不满——这既是拒绝的理由，也是它该被说出来的原因。
        """
        host = self._host(frame=None if label == "no_frame" else "ok",
                          tiled=(label == "no_room"))
        with mock.patch("meapet.desktop.render_host.safe_print"), \
                mock.patch.object(FP, "have_engine",
                                  return_value=(label != "no_engine")):
            box = self._start(host)
            if label == "no_fix":
                box["on_done"](None)
                host._fidus_service()
        return host, box

    def test_refusals_speak_and_mount_nothing(self):
        """四臂（没带引擎／没帧／窗口放不下探针／量不准）：各出一句、`enable` 一次都没有。

        四句说的必须是**四个不同的缺口**——合成一句"没能量准位置"会把人引去等一次
        根本不会到来的校准，而缺口分别在打包面、渲染面、窗口尺寸和屏幕上。
        """
        expect = {
            "no_engine": "没带",
            "no_frame": "拿不到当前画面",
            "no_room": "放得下定位探针",
            "no_fix": "没量准",
        }
        for label, needle in expect.items():
            with self.subTest(label):
                host, box = self._refusal(label)
                self.assertEqual(host.backend.enabled, [], f"{label} 不许挂载")
                spoken = [b for b in host.bubbles if not b.startswith("正在定位")]
                self.assertEqual(len(spoken), 1, f"{label} 该只出一句：{host.bubbles}")
                self.assertIn(needle, spoken[0])
                self.assertIsNone(getattr(host, "_pending_mount", None))
                self.assertFalse(getattr(host, "_fidus_busy", False))
                self.assertEqual(host.backend.positions, [])
                self.assertIsNone(getattr(host, "_probe_saved", None),
                                  f"{label} 不许留下一个放大过、mask 被清的窗口")
                if label == "no_room":
                    self.assertIn("Mod+V", spoken[0], "缺的是那一次按键，得把按键说出来")
                    self.assertEqual((host.width(), host.height()), (160, 160))
                    self.assertEqual(host.sprite_label.moved, [], "拒了也不该把画布挪过")
                if label != "no_fix":
                    self.assertEqual(box, {}, f"{label} 该在起工前就退回，不付那一份校准与探针")

    def test_timeout_is_one_shot_and_refuses(self):
        host = self._host()
        self._start(host)
        self.assertEqual(host.width(),
                         160 + host._probe_slack_px() + host._PROBE_ROOM_MARGIN_PX)
        with mock.patch("meapet.desktop.render_host.safe_print"):
            host._fidus_mount_timeout()
        self.assertEqual(host.backend.enabled, [])
        self.assertTrue(any("没量准" in b for b in host.bubbles))
        self.assertIsNone(host._pending_mount)
        self.assertEqual(host._mount_timer.stops, 1)
        self.assertEqual(host.width(), 160, "20 秒到也要把探针窗口缩回去——量废了那圈透明边还在")

    def test_a_second_click_starts_no_second_measurement(self):
        host = self._host()
        calls = []
        patcher = mock.patch.object(FP, "request_locate",
                                    side_effect=lambda *a, **k: calls.append(a))
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch("meapet.desktop.render_host.safe_print"):
            host._set_layer_mode(True)
            host._set_layer_mode(True)
        self.assertEqual(len(calls), 1, "面板连点不该叠两份测量")
        self.assertEqual(host.backend.enabled, [])

    def test_switching_back_drops_the_round_and_its_late_arrivals(self):
        """切回交互态之后，那一发迟到的读数与迟到的位移请求都该被轮次号作废。"""
        host = self._host()
        box = self._start(host)
        stale = host._fidus_round
        with mock.patch("meapet.desktop.render_host.safe_print"):
            host._set_layer_mode(False)
        self.assertIsNone(host._pending_mount)
        self.assertEqual(host.backend.destroyed, 1,
                         "没挂过也照发这一发——门面那句 `if self._ctx is None: return` 是它的保险")
        self.assertEqual(host.backend.enabled, [], "取消不许顺手提一次挂载")
        self.assertFalse(host._fidus_busy)
        self.assertEqual((host.width(), host.height()), (160, 160),
                         "取消同样要缩回探针窗口：放大过的那圈透明边不该留在交互态")
        self.assertIsNone(host._probe_saved)

        host._content_probe_shift = lambda dx, dy: host.shifts.append((dx, dy))
        req = threading.Event()
        stale_box: list = [None]
        host._fidus_move_req = (FP.MOVE_PX, 0.0, req, stale, stale_box)
        with mock.patch("meapet.desktop.render_host.safe_print"):
            host._fidus_service()
        self.assertEqual(host.shifts, [], "迟到的探针位移不许摆")
        self.assertTrue(req.is_set(), "但也不许让 fidus 线程干等 3 秒")
        self.assertIsNone(stale_box[0], "作废那一发不写成败——`_fidus_request_move` 按没摆出去处理")

        box["on_done"](FP.Fix(center=TestPremountOrdering.CENTER, conf=0.9,
                              edge=96, ceiling=0.9))
        self.assertFalse(host._fidus_finished, "迟到的读数该在进门那一刻就被丢掉")
        self.assertEqual(host.backend.enabled, [])

    def test_room_is_read_per_direction_and_left_up_are_not_blind(self):
        """四向余量按 `PREMOUNT_MOVES` 逐位对应；**左/上**那一向也得真摆得出去。

        旧那一发只看右/下 ⇒ 画布贴在窗口右下角时（平铺常见），左移明明有 200 px 余量
        却被判成"摆不出"。这一条钉的是"四角都要试"里的那两只角。
        """
        host = self._host()
        host._geom.update(w=600, h=600)
        host.sprite_label = self.FakeWidget(x=200, y=200, size=200)   # 600×600 里居中
        host._content_probe_base = None
        self.assertEqual(host._content_probe_room(), (200, 200, 200, 200),
                         "顺序是 (右,下,左,上)，与 `FP.PREMOUNT_MOVES` 逐位对齐")
        for direction in FP.PREMOUNT_MOVES:
            self.assertTrue(host._content_probe_shift(*direction), f"{direction} 该摆得出去")
            self.assertEqual(host.sprite_label.moved[-1],
                             (200 + int(direction[0]), 200 + int(direction[1])))
            self.assertEqual(host.sprite_label.moved[-2], (200, 200),
                             "每一向都从原位出发（幂等），不拿上一向的落点垫账")

        host.sprite_label = self.FakeWidget(x=10, y=10, size=200)     # 贴左上角
        host._content_probe_base = None
        self.assertFalse(host._content_probe_shift(-FP.MOVE_PX, 0.0))
        self.assertEqual(host.sprite_label.moved[-1], (10, 10), "摆不成要留在原位")
        self.assertTrue(host._content_probe_shift(FP.MOVE_PX, 0.0), "另一向仍有余量就该摆")

    def test_a_negative_room_in_an_unneeded_direction_does_not_veto_the_move(self):
        """真机 2026-10-03「恒不通过」的根因钉：负空档不得否掉 `need=0` 的那一向。

        `window_mask` 开着 ⇒ 画布比视口宽、往左溢出 ⇒ 「左」那一只空档**恒为负**
        （日志逐字：`四向空档 (右48,下48,左-123,上0) 摆不出 (48,0)`）。向右那一发根本不需要
        左边的位置，旧那式却拿 `need=0` 去比 `room=-123`，`0 > -123` 为真 ⇒ 四向全死、
        每一次点击都判回"摆不出"。这里用的是日志那四个数本身，不是编出来的边界值。
        """
        host = self._host()
        host._geom.update(w=437, h=560)                          # 日志：请求 437×560 实得 437×560
        host.sprite_label = self.FakeWidget(x=-123, y=0, size=512)  # 日志：画布 512² @(-123,0)
        host._content_probe_base = None
        self.assertEqual(host._content_probe_room(), (48, 48, -123, 0),
                         "空档按 (右,下,左,上) 逐位对应 `PREMOUNT_MOVES`")
        self.assertTrue(host._content_probe_shift(FP.MOVE_PX, 0.0),
                        "向右只要 48 px，左边那个 -123 是内容被裁的位置，不是这一向的缺口")
        self.assertEqual(host.sprite_label.moved[-1], (-123 + int(FP.MOVE_PX), 0))
        host._restore_content_probe()
        self.assertEqual(host.sprite_label.moved[-1], (-123, 0), "探针吃掉后要回原位")

        self.assertTrue(host._content_probe_shift(0.0, FP.MOVE_PX), "向下那一只同理")
        host._restore_content_probe()

        # 反向门：真缺口仍然必须响亮判死，别把这次修复读成"空档不看负数了"。
        self.assertFalse(host._content_probe_shift(-FP.MOVE_PX, 0.0),
                         "左向真要 48 px，而左是 -123 ⇒ 这一向该拒")
        self.assertEqual(host.sprite_label.moved[-1], (-123, 0), "摆不成要留在原位")
        self.assertFalse(host._content_probe_shift(0.0, -FP.MOVE_PX), "上向真要 48 px，而上是 0 ⇒ 该拒")

    def test_post_mount_round_still_moves_the_surface(self):
        """反向门：`premount=False` 那一支仍走 `_fidus_place`，两路不共用一只手。"""
        host = self._host()
        host._pending_mount = None
        host._fidus_premount = False
        token = host._begin_fidus_round(*TestPremountOrdering.BELIEF, premount=False)
        placed = []
        host._fidus_place = lambda x, y: placed.append((x, y)) or True
        req = threading.Event()
        outcome: list = [None]
        host._fidus_move_req = (FP.MOVE_PX, 0.0, req, token, outcome)
        host._fidus_service()
        self.assertEqual(placed, [(348, 200)])
        self.assertTrue(outcome[0], "post-mount 也照实回成败——`move` 的契约两边同一个")
        self.assertEqual(host.sprite_label.moved, [], "post-mount 不该碰子控件那只手")


class TestFloatGate(unittest.TestCase):
    """测量前的浮动闸门（人工裁决 2026-10-02「先让用户 Mod + V…需要不再提示选项」）。

    平铺下客户端请求的尺寸会被吃掉（人工现测）⇒ 交互态读到的四向空档与屏上真实边界脱钩。
    这一闸门管三件事：**问了才量**、不点「继续」就**不切**（裁决 1 的又一条出口）、
    勾了「不再提示」就落盘且此后不再问。对话框一律是假的——`exec_()` 会吃掉测试的时间，
    而这里要钉的是"谁在什么时候被调用"，不是它的圆角。
    """

    def _host(self):
        return TestPremountOrdering._host(self)      # 借同一份假 host / 假 backend

    def _start(self, host):
        return TestPremountOrdering._start(self, host)

    def _asked(self, host):
        host.config["fidus"]["ask_float"] = True       # 覆盖 `_host()` 里那句"不再问"
        host.saved = []
        host._save_config = lambda: host.saved.append(True)

    def _dialog(self, reply, checked=False):
        from PyQt5.QtWidgets import QMessageBox

        built = []
        value = int(reply)

        class FakeDialog:
            def __init__(self, _parent, **kwargs):
                self.kwargs = kwargs
                built.append(self)

            def exec_(self):
                return value

            def is_checked(self):
                return checked

        patcher = mock.patch("meapet.message_dialog.MeaMessageDialog", FakeDialog)
        patcher.start()
        self.addCleanup(patcher.stop)
        return built

    def test_it_asks_before_posting_the_measurement(self):
        from PyQt5.QtWidgets import QMessageBox

        host = self._host()
        self._asked(host)
        built = self._dialog(QMessageBox.Yes)
        box = self._start(host)
        self.assertEqual(len(built), 1, "没问过就投测量＝拿平铺那份假空档去量")
        self.assertEqual(built[0].kwargs["check_text"], "不再提示")
        self.assertIn("Mod+V", built[0].kwargs["text"])
        self.assertIsNotNone(box.get("on_done"), "点了继续 ⇒ 测量照起工")
        self.assertEqual(host.backend.enabled, [], "量完之前仍不许挂")

    def test_declining_mounts_nothing_and_posts_no_measurement(self):
        from PyQt5.QtWidgets import QMessageBox

        host = self._host()
        self._asked(host)
        self._dialog(QMessageBox.No)
        with mock.patch("meapet.desktop.render_host.safe_print"):
            host._set_layer_mode(True)
        self.assertEqual(host.backend.enabled, [], "不点继续就是没切穿透")
        self.assertEqual(getattr(host, "_pending_mount", None), None)
        self.assertFalse(getattr(host, "_fidus_busy", False))
        spoken = [b for b in host.bubbles if b.startswith("要先按")]
        self.assertEqual(len(spoken), 1, f"该出声说清缺的是那一次按键：{host.bubbles}")
        self.assertIn("Mod+V", spoken[0])

    def test_dont_ask_again_persists_and_silences_the_gate(self):
        from PyQt5.QtWidgets import QMessageBox

        host = self._host()
        self._asked(host)
        built = self._dialog(QMessageBox.Yes, checked=True)
        self._start(host)
        self.assertIs(host.config["fidus"]["ask_float"], False)
        self.assertEqual(host.saved, [True], "写了判据却没落盘 ⇒ 下次启动又问一遍")
        self.assertTrue(host._confirm_float_first(), "此后不该再弹")
        self.assertEqual(len(built), 1, "第二次连对话框都不该构造")
        self.assertEqual(host.saved, [True], "不再提示这条只落一次盘")

    def test_answered_before_never_builds_a_dialog(self):
        from PyQt5.QtWidgets import QMessageBox

        host = self._host()                       # `_host()` 里 ask_float 已是 False
        built = self._dialog(QMessageBox.Yes)
        box = self._start(host)
        self.assertEqual(built, [], "落过盘之后弹框＝每切一次都要点一次")
        self.assertIsNotNone(box.get("on_done"))


class TestConfigAndWiring(unittest.TestCase):
    def test_default_is_off_after_normalize(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config({})
        self.assertIs(cfg["fidus"]["enabled"], False)

    def test_on_survives_normalize(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config({"fidus": {"enabled": True}})
        self.assertIs(cfg["fidus"]["enabled"], True)

    def test_float_gate_asks_by_default_and_answered_stays_answered(self):
        """默认问（平铺那份空档不可信），落过「不再提示」之后规范化不许把它洗回 True。"""
        from meapet.config.store import normalize_config

        self.assertIs(normalize_config({})["fidus"]["ask_float"], True)
        self.assertIs(normalize_config({"fidus": {"ask_float": False}})["fidus"]["ask_float"],
                      False)

    def test_example_config_declares_the_key(self):
        import json

        data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertIs(data["fidus"]["enabled"], False)
        self.assertIs(data["fidus"]["ask_float"], True,
                      "示例少一行 ⇒ 读示例的人不知道有这道闸门")

    def test_menu_leaf_defaults_unchecked_and_follows_config(self):
        from PyQt5.QtWidgets import QApplication, QWidget

        from meapet.desktop.dev_options import PetDevOptionsMixin
        from meapet.desktop.window_chrome import PetWindowChromeMixin

        app = QApplication.instance() or QApplication(sys.argv)
        del app

        class Host(QWidget, PetWindowChromeMixin, PetDevOptionsMixin):
            # 菜单上还连着十几个别的手势，本用例只管「定位与穿透」那一片：
            # 其余按名给空实现，没点名的照常 AttributeError。
            _NOOP = (
                "_copy_agent_control_token", "_do_screen_watch", "_is_auto_start",
                "_open_size_dialog", "_regenerate_agent_control_token",
                "_reopen_setup_wizard", "_reset_memory", "_safe_set_mood",
                "_set_size_factor", "_set_vision_backend", "_set_vision_model",
                "_show_status_panel", "_show_timeline", "_show_volume_dialog",
                "_toggle_auto_start", "_toggle_fidus_enabled", "_toggle_render_mode",
                "_toggle_standby", "_toggle_voice_input", "_toggle_watcher_enabled",
            )
            _FLAGS = ("_standby", "_use_live2d")

            def __init__(self, enabled):
                super().__init__()
                self.config = {"fidus": {"enabled": enabled}}

            def __getattr__(self, name):
                if name in Host._NOOP:
                    return lambda *_a, **_k: None
                if name in Host._FLAGS:
                    return False
                raise AttributeError(name)

            def _show_bubble(self, *_a, **_k):
                pass

        def find_menu(menu, title):
            """归并后「定位与穿透」嵌在「设置与数据」里，根层直取找不到。"""
            for act in menu.actions():
                sub = act.menu()
                if sub is None:
                    continue
                if sub.title() == title:
                    return sub
                hit = find_menu(sub, title)
                if hit is not None:
                    return hit
            return None

        for enabled in (False, True):
            host = Host(enabled)
            locate = find_menu(host._build_context_menu(), "定位与穿透")
            self.assertIsNotNone(locate, "归并后这一支还在菜单树里")
            leaf = locate.actions()[0]
            self.assertEqual(leaf.text(), "启用fidus")
            self.assertTrue(leaf.isCheckable())
            self.assertEqual(leaf.isChecked(), enabled)

    def test_disabled_switch_never_touches_the_engine(self):
        """开关关着时 `_maybe_start_fidus_locate` 必须立刻返回：不许有副作用。"""
        from meapet.desktop.render_host import PetRenderHostMixin

        host = object.__new__(PetRenderHostMixin)
        host.config = {"fidus": {"enabled": False}}
        calls = []
        host._fidus_current_frame = lambda: calls.append("frame") or None
        host._maybe_start_fidus_locate(0, 0, 64, 64)
        self.assertEqual(calls, [])

    def test_absent_engine_is_caught_before_the_round_opens(self):
        """没带 fidus ⇒ 不起工、不取帧，气泡说的是"没带"，不是"没量准"。

        这条判据的形状与 #78 那条同源：量不了的活儿不放行，且报出的原因要指向真正的
        缺口。把"随包分发没落地"报成"没能量准位置，沿用原来的位置"，人会去查屏幕，
        而屏幕上什么都没有——校准那 2 秒和一次闪屏还会照付。
        """
        from meapet.desktop.render_host import PetRenderHostMixin

        host = object.__new__(PetRenderHostMixin)
        host.config = {"fidus": {"enabled": True}}
        seen = []
        host._fidus_current_frame = lambda: seen.append("frame") or None
        host._show_bubble = lambda text, *_a, **_k: seen.append(text)
        with mock.patch.object(FP, "have_engine", return_value=False):
            host._maybe_start_fidus_locate(0, 0, 64, 64)
        self.assertNotIn("frame", seen)
        self.assertEqual(len(seen), 1)
        self.assertIn("没带", seen[0])
        self.assertFalse(getattr(host, "_fidus_busy", False))

    def test_present_engine_still_reaches_the_frame(self):
        """反向门：`have_engine()` 为真时这条检查不该挡住正常路径。

        帧 mock 成 None ⇒ 出声闸（test_frame_none_is_loud_not_silent 的判据）在
        这条路径上落地；本条只判 have_engine 没挡住取帧、也没把 round 开起来。
        """
        from meapet.desktop.render_host import PetRenderHostMixin

        host = object.__new__(PetRenderHostMixin)
        host.config = {"fidus": {"enabled": True}}
        seen = []
        host._fidus_current_frame = lambda: seen.append("frame") or None
        host._show_bubble = lambda text, *_a, **_k: seen.append(text)
        with mock.patch.object(FP, "have_engine", return_value=True), \
                mock.patch("meapet.desktop.render_host.safe_print"):
            host._maybe_start_fidus_locate(0, 0, 64, 64)
        self.assertEqual(seen[0], "frame")
        self.assertFalse(getattr(host, "_fidus_busy", False))

    def test_frame_none_is_loud_not_silent(self):
        """出声闸：离屏帧拿不到 ⇒ 日志与气泡都要出声，不许静默 return。

        那是「启用了 fidus 但什么也不发生」时人无从下手的症状；气泡说的是
        「画面拿不到」（缺口在渲染面），不是「没量准」——后者会把人引去等校准。
        """
        from meapet.desktop.render_host import PetRenderHostMixin

        host = object.__new__(PetRenderHostMixin)
        host.config = {"fidus": {"enabled": True}}
        seen = []
        host._fidus_current_frame = lambda: seen.append("frame") or None
        host._show_bubble = lambda text, *_a, **_k: seen.append(text)
        with mock.patch.object(FP, "have_engine", return_value=True), \
                mock.patch("meapet.desktop.render_host.safe_print") as log:
            host._maybe_start_fidus_locate(0, 0, 64, 64)
        self.assertIn("frame", seen)
        self.assertEqual(
            seen[seen.index("frame") + 1], "拿不到当前画面，定位不了位置")
        self.assertIn("拿不到当前离屏帧", log.call_args[0][0])
        self.assertFalse(getattr(host, "_fidus_busy", False))


class TestEnginePresence(unittest.TestCase):
    """「引擎在不在场」是打包面的事实，必须与「这次量没量准」分得开。"""

    def test_have_engine_follows_the_module_spec(self):
        for present in (True, False):
            spec = object() if present else None
            with mock.patch.object(FP.importlib.util, "find_spec",
                                   return_value=spec), \
                 mock.patch.object(FP, "_HAVE_FIDUS", None):
                self.assertIs(FP.have_engine(), present)

    def test_broken_import_is_reported_as_import_not_as_measurement(self):
        """规格在、导入炸（ABI 不符／产物被裁）时，话要说成"导入不了"。"""
        with mock.patch.dict(sys.modules, {"fidus": None}):
            with self.assertRaises(FP.EngineError) as ctx:
                FP.FidusEngine()
        self.assertIn("导入", str(ctx.exception))


class TestFallbackEatsTheProbeMove(unittest.TestCase):
    """退回出口必须把探针位移吃掉。

    `locate()` 的读数取自 +`MOVE_PX` 位移**之前**，它自己不回摆：位移之后的三条出口
    （重注册失败／定不住／闭环超容差）都直接把 `None` 交回。而产品在那一刻弹的气泡是
    "没能量准位置，沿用原来的位置" —— 若不重新请求挂载位，这句话在屏幕上就是错的。
    H15 A3 在本机没能触发这条（niri 不夹 layer surface，+48 照单执行 ⇒ 闭环诚实通过），
    所以这条只能在假引擎上证：真桌面量不到"合成器没照请求摆"，正是本功能的立论前提。
    """

    MOUNT = (400, 300, 160, 160)

    def _host(self, surface):
        from types import SimpleNamespace

        from meapet.desktop.render_host import PetRenderHostMixin

        host = object.__new__(PetRenderHostMixin)
        host.config = {"fidus": {"enabled": True}}
        host.bubbles = []
        host.placed = []
        host.sprite_label = SimpleNamespace(_proxy_rect=None)
        host._fidus_mount = self.MOUNT
        host._fidus_surface = surface
        host._fidus_busy = True
        host._fidus_finished = True
        host._fidus_result = None
        host._fidus_move_req = None
        host._fidus_move_ack = None
        host._fidus_settle = 0

        class _Timer:
            def stop(self):
                pass

        host._fidus_timer = _Timer()

        def place(x, y):
            host.placed.append((int(x), int(y)))
            host._fidus_surface = (int(x), int(y), self.MOUNT[2], self.MOUNT[3])
            return True

        host._fidus_place = place
        host._show_bubble = lambda text, *_a, **_k: host.bubbles.append(text)
        return host

    def test_moved_then_refused_places_the_mount_rect_back(self):
        moved = (448, 300, 160, 160)          # 探针位移后的位置：合成器只接受了 48 px
        host = self._host(moved)
        host._fidus_service()
        self.assertEqual(host.placed, [(400, 300)])
        rect = host.sprite_label._proxy_rect
        self.assertEqual((rect.x(), rect.y(), rect.width(), rect.height()), self.MOUNT)
        self.assertTrue(any("没能量准" in b for b in host.bubbles))
        self.assertFalse(host._fidus_busy)

    def test_refused_before_any_move_sends_no_request(self):
        """候选全拒时一次摆位都不该有：H15 A2 在真机上量到的就是这个 0。"""
        host = self._host(self.MOUNT)
        host._fidus_service()
        self.assertEqual(host.placed, [])
        self.assertIsNone(host.sprite_label._proxy_rect)
        self.assertTrue(any("没能量准" in b for b in host.bubbles))


class TestPostMountRectanglesSplit(unittest.TestCase):
    """丙 接到 post-mount：读数是**画布中心**，`set_position` 吃 surface margin，信念吃画布矩形。

    桥接层那个锚点定死在帧中心（`fidus_position.iter_candidates` 里 `anchor = 盒中心 − 帧中心`），
    而帧就是整画布 ⇒ 它与挂载时切走的那圈透明边无关。谁拿读数反解矩形，谁就得按**画布**尺寸反解，
    再把帧内原点加回去交给 `set_position`。`origin=(0,0)` 那一格逐位等于丙之前（上面
    `TestFallbackEatsTheProbeMove` 守着），这里只钉分叉之后那一格。

    两套尺寸是刻意配成**不对称**的（surface 100²、画布 461x614）：真实轮廓两侧各带 16 px 余量时
    两个中心几乎重合，反解错用尺寸只差 1–2 px，钉不出判决——这里要的是"用错矩形会当场炸"。
    """

    CANVAS = (461, 614)
    ORIGIN = (44, 25)
    SURFACE = (898, 168, 100, 100)          # 画布 (854,143) 平移帧内原点之后的那一块
    CANVAS_CENTER = (854 + 461 / 2.0, 143 + 614 / 2.0)

    class _Timer:
        def __init__(self):
            self.starts = 0
            self.stops = 0

        def start(self):
            self.starts += 1

        def stop(self):
            self.stops += 1

    def _host(self):
        from types import SimpleNamespace

        from meapet.desktop.render_host import PetRenderHostMixin

        host = object.__new__(PetRenderHostMixin)
        host.config = {"fidus": {"enabled": True}}
        host.bubbles = []
        host.placed = []
        host.sprite_label = SimpleNamespace(_proxy_rect=None)
        host._layer_frame_origin = self.ORIGIN
        host._layer_canvas_size = self.CANVAS
        host._fidus_timer = self._Timer()
        host._fidus_premount = False
        host._fidus_busy = True
        host._fidus_finished = True
        host._fidus_result = None
        host._fidus_move_req = None
        host._fidus_move_ack = None
        host._fidus_settle = 0
        host._fidus_surface = self.SURFACE
        host._fidus_mount = self.SURFACE
        host._show_bubble = lambda text, *_a, **_k: host.bubbles.append(text)
        host._fidus_current_frame = lambda: np.zeros((614, 461, 4), dtype=np.uint8)

        def place(x, y):
            host.placed.append((int(x), int(y)))
            host._fidus_surface = (int(x), int(y), *self.SURFACE[2:])
            return True

        host._fidus_place = place
        return host

    def test_a_fix_is_inversed_against_the_canvas_and_placed_as_surface(self):
        host = self._host()
        host._fidus_result = FP.Fix(center=self.CANVAS_CENTER, conf=0.9, edge=96, ceiling=0.9)
        with mock.patch("meapet.desktop.render_host.safe_print"):
            host._fidus_service()
        self.assertEqual(host.placed, [self.SURFACE[:2]],
                         "拿 surface 尺寸反解画布中心 ⇒ 摆到画布外面去（旧式会算出 (1034,400)）")
        rect = host.sprite_label._proxy_rect
        self.assertEqual((rect.x(), rect.y(), rect.width(), rect.height()),
                         (854, 143, *self.CANVAS), "信念写 surface 矩形 ⇒ 视线朝桌宠外面看")

    def test_the_refusal_restores_the_mount_rect_as_a_canvas(self):
        moved = (946, 168, *self.SURFACE[2:])    # 探针位移后：合成器真接受了 +48
        host = self._host()
        host._fidus_surface = moved
        with mock.patch("meapet.desktop.render_host.safe_print"):
            host._fidus_service()
        self.assertEqual(host.placed, [self.SURFACE[:2]])
        rect = host.sprite_label._proxy_rect
        self.assertEqual((rect.x(), rect.y(), rect.width(), rect.height()),
                         (854, 143, *self.CANVAS))

    def test_the_prior_handed_to_the_engine_is_the_canvas_center(self):
        """起工那一发喂的先验同样是画布口径——喂 surface 中心就是拿一个错先验去骗选型。"""
        host = self._host()
        host._fidus_busy = False
        captured = []
        with mock.patch.object(FP, "have_engine", lambda: True), \
             mock.patch.object(FP, "request_locate",
                               side_effect=lambda _f, prior, **_k: captured.append(prior)), \
             mock.patch("meapet.desktop.render_host.safe_print"):
            host._maybe_start_fidus_locate(*self.SURFACE)
        self.assertEqual(captured, [self.CANVAS_CENTER])


if __name__ == "__main__":
    unittest.main()
