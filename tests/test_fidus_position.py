"""fidus 定位的产品侧回归：不碰真桌面、不碰 fidus 本体。

引擎一律是假的（`FakeEngine`）—— 本文件测的是**判据与接线**：
喂给引擎的信念是不是贴片中心、闭环拦不拦得住钉死的读数、失败是否一律退回。
真实读数与真机数字在 `scripts/fidus_positioning_probe/`（H8–H11），不在这里。
"""
import sys
import unittest
from pathlib import Path

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
        self.registrations.append((int(box.shape[0]), tuple(initial_center)))
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

    def test_consistent_move_yields_corrected_centre(self):
        state = {"dx": 0.0}

        def move(dx, _dy):
            state["dx"] += dx

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


class TestConfigAndWiring(unittest.TestCase):
    def test_default_is_off_after_normalize(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config({})
        self.assertIs(cfg["fidus"]["enabled"], False)

    def test_on_survives_normalize(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config({"fidus": {"enabled": True}})
        self.assertIs(cfg["fidus"]["enabled"], True)

    def test_example_config_declares_the_key(self):
        import json

        data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertIs(data["fidus"]["enabled"], False)

    def test_menu_leaf_defaults_unchecked_and_follows_config(self):
        from PyQt5.QtWidgets import QApplication, QWidget

        from meapet.desktop.window_chrome import PetWindowChromeMixin

        app = QApplication.instance() or QApplication(sys.argv)
        del app

        class Host(QWidget, PetWindowChromeMixin):
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

        for enabled in (False, True):
            host = Host(enabled)
            action = next(
                a for a in host._build_context_menu().actions()
                if a.menu() is not None and a.menu().title() == "定位与穿透"
            )
            leaf = action.menu().actions()[0]
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


if __name__ == "__main__":
    unittest.main()
