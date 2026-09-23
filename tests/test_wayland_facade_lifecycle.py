"""`WaylandLayerBackend` 门面的 ctx 生命周期回归（wayland-facade-lifecycle）。

被测真身（不是复刻）：`meapet/desktop/wayland_layer.py` 里 `WaylandLayerBackend`
的 `enable / disable / destroy_context`。后端注入一个 **fake shim**，用来在无合成器、
不 `dlopen` 真 `.so` 的前提下，观测门面对 ctx 句柄注册表做了什么。

为什么用 fake shim 而不是真产物（agents-rules §1/§10，L1 非 L3）
--------------------------------------------------------------
* 真产物的成功 `layer_create_context` 需要一条活的 Wayland 会话；CI / 本地无合成器
  时它先被 init 门拒成 -1（见 `test_layer_bridge_abi.py` 的 no-session 路径），
  于是"二次 enable 是否泄漏"根本触发不到——探针会测量自己的缺席。
* 本文件要判的是**门面 Python 侧**那条"覆盖 self._ctx 而不回收旧句柄"的路径，
  它与桥接层实现无关，因此用一个忠实复刻桥语义的 fake 反而判得更准、更可复现。

fake shim 复刻的三条桥语义（源自 render_host.py:715-733 记录的 L3 与 spec §4.7）
------------------------------------------------------------------------------
1. `layer_create_context` 每次返回一个**新**唯一句柄，登记进存活表；
2. `layer_destroy_context(ctx)` 只释放**点名的那一只**（§4.7 第 12 行），不连坐；
3. `layer_shell_cleanup()` **连坐销毁全部**存活 ctx 并计一次（§4.7 第 9 行）。

本文件能证明：门面二次 `enable()` 不留孤儿、`disable()` 归零、`destroy_context()`
不触发 cleanup。不能证明：真合成器上的 fd / RING_DEPTH 收支（属 L3，见 render_host 记录）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from meapet.desktop.wayland_layer import WaylandLayerBackend


class FakeShim:
    """忠实复刻桥的 ctx 注册表语义，供门面在无合成器环境下被逐条判决。"""

    def __init__(self, init_rc: int = 0):
        self._init_rc = init_rc
        self._next_handle = 1
        self.live: set[int] = set()          # 桥认为存活、尚未回收的 ctx
        self.destroy_calls: list[int] = []   # 每次 destroy 点名的句柄
        self.cleanup_calls = 0               # layer_shell_cleanup 次数（连坐）
        self.init_calls = 0

    def layer_shell_init(self) -> int:
        self.init_calls += 1
        return self._init_rc

    def layer_shell_cleanup(self) -> None:
        self.cleanup_calls += 1
        self.live.clear()

    def layer_create_context(self, state, width, height, pos_x, pos_y) -> int:
        # 真桥在无会话时返回 NULL；fake 恒成功，以触发门面的成功路径。
        handle = self._next_handle
        self._next_handle += 1
        self.live.add(handle)
        return handle

    def layer_destroy_context(self, ctx) -> None:
        # §4.7 第 12 行：只点名释放，不连坐其它存活 ctx。
        self.destroy_calls.append(ctx)
        self.live.discard(ctx)

    def layer_set_click_through(self, ctx, enabled) -> None:
        pass


class FacadeLifecycleTest(unittest.TestCase):
    def _backend(self, init_rc: int = 0) -> tuple[WaylandLayerBackend, FakeShim]:
        be = WaylandLayerBackend()
        shim = FakeShim(init_rc=init_rc)
        be._shim = shim          # 绕开 _load() 的真 ctypes.CDLL
        return be, shim

    def test_enable_twice_leaves_no_orphan(self):
        """核心：二次 enable() 不得把第一只 ctx 变成孤儿（桥存活表须恰为 1）。

        失效模式（修复前）：enable() 直接 `self._ctx = create()`，旧句柄既没引用
        也没 destroy → 桥侧存活表停在 2（render_host:715-733 记 L3 实测过 memfd 恒 6）。
        """
        be, shim = self._backend()
        be.enable(None, 100, 100, 0, 0)
        first = be._ctx
        be.enable(None, 200, 200, 10, 10)
        second = be._ctx

        self.assertNotEqual(first, second, "二次 enable 应换一只新 ctx")
        # 门面字段只指向最新那只——所以桥的存活表才是判"有没有孤儿"的真值。
        self.assertEqual(be._ctx, second)
        self.assertEqual(
            shim.live,
            {second},
            f"旧 ctx 未被回收即泄漏：桥存活表={sorted(shim.live)}（应只剩最新 {second}）",
        )
        # 旧句柄被显式 destroy 过，而不是靠 cleanup 连坐抹掉。
        self.assertIn(first, shim.destroy_calls)
        self.assertEqual(
            shim.cleanup_calls,
            0,
            "幂等守卫应用 destroy_context 精确回收，不该动用连坐 cleanup",
        )

    def test_enable_without_prior_ctx_does_not_destroy(self):
        be, shim = self._backend()
        be.enable(None, 100, 100, 0, 0)
        self.assertEqual(shim.destroy_calls, [], "首次 enable 无旧 ctx 可回收")
        self.assertEqual(shim.live, {be._ctx})

    def test_disable_reclaims_current_and_zeroes_live(self):
        be, shim = self._backend()
        be.enable(None, 100, 100, 0, 0)
        handle = be._ctx
        be.disable()
        self.assertIsNone(be._ctx)
        self.assertEqual(shim.live, set(), "disable 后桥存活表须归零")
        self.assertIn(handle, shim.destroy_calls)
        self.assertEqual(shim.cleanup_calls, 1)

    def test_enable_after_disable_reinitializes_cleanly(self):
        be, shim = self._backend()
        be.enable(None, 100, 100, 0, 0)
        be.disable()
        be.enable(None, 100, 100, 0, 0)
        self.assertEqual(shim.live, {be._ctx}, "cleanup 后重建应回到单只存活")

    def test_destroy_context_does_not_trigger_cleanup(self):
        """destroy_context 的作用域＝只送这一只退场，后端留给别人（不连坐）。"""
        be, shim = self._backend()
        be.enable(None, 100, 100, 0, 0)
        handle = be._ctx
        be.destroy_context()
        self.assertIsNone(be._ctx)
        self.assertEqual(shim.live, set())
        self.assertEqual(shim.cleanup_calls, 0, "destroy_context 不应连坐清理后端")
        # 旧句柄再被 destroy 一次（幂等/误用）不应复活或崩。
        be.destroy_context()
        self.assertEqual(shim.live, set())

    def test_enable_failure_leaves_no_handle(self):
        """init 失败必须响亮 raise 且不留句柄（§4.7 第 10 行的 Python 侧对应物）。"""
        be, shim = self._backend(init_rc=-1)
        with self.assertRaises(RuntimeError):
            be.enable(None, 100, 100, 0, 0)
        self.assertIsNone(be._ctx)
        self.assertEqual(shim.live, set())


if __name__ == "__main__":
    unittest.main()
