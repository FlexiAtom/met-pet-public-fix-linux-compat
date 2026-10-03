"""关闭事件与 Live2D 首帧预算表的回归（工作项 #84 的 L2 那一半，人工裁决「批乙类」放行新增测试）。

被测事实（`~/.Athena/projects/meapet/pool/layer-shell-live2d-close-forensics.md` §2）：
`MeaPet` 类体里的 `closeEvent`（`meapet/desktop/app.py:587`）**压过** `PetRenderHostMixin`
那一版（`meapet/desktop/render_host.py:2302`）——MRO 上类体自己的定义优先。生效那一版只
`event.ignore()` + `self.hide()`，**不取消首帧预算表**；被压过那一版的 docstring 恰恰明写它
存在的理由是「避免关闭后被超时回退重新显示」。于是关掉的桌宠可能自己冒回来：表到点 →
`_on_live2d_startup_timeout` → `_fallback_to_png` → 末尾 `show()`（`show()` 那一跳由
`tests/test_live2d_startup.py` 覆盖，本文件不重复判）。

为什么是 L2 而不是真机：这里判的是**哪个定义生效**与**它做了什么**，两者都不需要真实
合成器；真机那一半只补"关不掉的表征"（该件的臂 A）。本文件不断言任何屏幕位置／上屏行为。

**钉的是当前行为，不是理想行为**：把遮蔽修掉（删掉 mixin 那一版、或让它成为生效版）之后
第 1、2 条会红——那是提醒一并更新这里记录的口径，不是回归。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer

from meapet.desktop.app import MeaPet
from meapet.desktop.render_host import LIVE2D_STARTUP_TIMEOUT_MS, PetRenderHostMixin

STARTUP_KEY = "_live2d_startup_timer"     # 首帧预算表的名字，见 _ensure_live2d_startup_timer


class _CloseStandin(QObject):
    """只长 `closeEvent` 用到的那几个器官的真实 QObject。

    必须是真的 QObject：`_ensure_live2d_startup_timer()` 里是 `QTimer(self)`，
    SimpleNamespace 会抛 TypeError（这条坑在 `tests/test_app_standby.py` 已经踩过一次）。
    """

    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.fallbacks = []
        self._idle_timer = QTimer(self)
        self._l2d_pending = True
        self.sprite_label = object()
        self._live2d_startup_widget = self.sprite_label

    def hide(self):
        self.hidden += 1

    def _on_live2d_startup_timeout(self):
        # 表是用 `timer.timeout.connect(self._on_live2d_startup_timeout)` 起的，
        # 所以替身必须真摊这一个——不摊就连不上表，那测的就不是这条链了。
        PetRenderHostMixin._on_live2d_startup_timeout(self)

    def _fallback_to_png(self, reason):
        self.fallbacks.append(reason)


class _CloseEvent:
    """`QCloseEvent` 的替身：本文件只判 `ignore()` 有没有被走到。"""

    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True


def _armed(host) -> QTimer:
    """按产品自己的路子起表（不在测试里手搓 QTimer 的形状）。"""
    PetRenderHostMixin._ensure_live2d_startup_timer(host)
    timer = getattr(host, STARTUP_KEY)
    timer.start(LIVE2D_STARTUP_TIMEOUT_MS)
    return timer


class TestWhichCloseEventIsEffective(unittest.TestCase):
    def test_the_class_body_version_shadows_the_mixin_one(self):
        effective = MeaPet.closeEvent
        self.assertNotEqual(effective, PetRenderHostMixin.closeEvent,
                            "两版已是同一个函数 ⇒ 遮蔽已被修掉，本文件的口径需要一并更新")
        self.assertEqual(Path(effective.__code__.co_filename).name, "app.py")
        self.assertEqual(
            effective.__qualname__.split(".")[0], "MeaPet",
            "生效的 closeEvent 必须来自 MeaPet 类体（render_host 那一版不可达）",
        )

    def test_the_effective_version_never_mentions_the_startup_budget(self):
        """生效那一版的函数体里没有取消动作——这正是"表活过关闭"的成因。"""
        names = set(MeaPet.closeEvent.__code__.co_names)
        self.assertNotIn(
            "_cancel_live2d_startup_timeout", names,
            "app.py 那一版若开始取消首帧表，本件的前提就不成立了",
        )
        self.assertIn("_idle_timer", names, "顺带钉住它确实会停空闲定时器（只不停首帧表）")


class TestClosingLeavesTheBudgetRunning(unittest.TestCase):
    def test_hide_and_stop_idle_but_keep_the_first_frame_timer_armed(self):
        host = _CloseStandin()
        host._idle_timer.start(20000)      # 不先起表，"它被停了"就是句空话
        timer = _armed(host)
        self.assertTrue(timer.isActive(), "前提：表已经起好")

        event = _CloseEvent()
        MeaPet.closeEvent(host, event)

        self.assertTrue(event.ignored, "常驻悬浮窗：关闭只隐藏，不许放行")
        self.assertEqual(host.hidden, 1)
        self.assertFalse(host._idle_timer.isActive(), "空闲定时器是被停的那一个")
        self.assertTrue(timer.isActive(),
                        "首帧预算表在生效版里没人停它——这就是「关掉的桌宠会冒回来」的那一格")

    def test_the_timeout_still_fires_after_the_window_was_closed(self):
        """表活过关闭 ⇒ 到点照样走回退。`show()` 那一跳归 test_live2d_startup.py 判。"""
        host = _CloseStandin()
        _armed(host)
        MeaPet.closeEvent(host, _CloseEvent())

        host._on_live2d_startup_timeout()      # 表到点就是这一发
        self.assertEqual(host.fallbacks, ["等待 Live2D 首帧超时"],
                         "关闭之后守卫仍然放行 ⇒ 回退照跑，没有任何东西为这次关闭让路")

    def test_cancelling_would_have_been_the_mixin_behaviour(self):
        """对照：被压过的那一版**会**停表。两条用例合起来才说明遮蔽是有效差的。"""
        host = _CloseStandin()
        timer = _armed(host)
        PetRenderHostMixin._cancel_live2d_startup_timeout(host)
        self.assertFalse(timer.isActive())


if __name__ == "__main__":
    unittest.main()
