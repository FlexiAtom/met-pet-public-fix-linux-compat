#!/usr/bin/env python3
"""H19 · `A0 挂载` 那一秒到底花在哪：把 `_init_layer_overlay_mode()` 拆成逐段的账。

出处是挂账 `~/.Athena/projects/meapet/working/fidus-switch-positioning.md` 那条
"1.5 档时间散布"（既有日志里 `A0 挂载` 共 10 个数，跨 12–1907 ms，scale 1.0 与 1.5
都有散布）。H15 只量到**整段**（`time.perf_counter()` 圈住
`host._init_layer_overlay_mode()`），而它另外量到的一件已经把嫌疑面砍掉一半：真正
创建/映射 layer surface 的 `_set_layer_mode(True)` 在 1907 ms 那一跑里只用 **1 ms**
（`/tmp/h15_s15.log:42`）⇒ "合成器首次给这个尺寸的 surface 分配"那条候选**已被既有
证据否掉**，本探针不重跑去信它。

剩下没账的是函数体里其余的六件事：

    ① 函数体内那次 import（`wayland_layer` + `layer_debug_panel`，进程内首次）
    ② `layer_available()` = 后端单例 `is_available()` = `_load()` + `layer_shell_init()`
       其中 `_load()` → `open_shim()` → `shim_candidates()` → **`ctypes.util.find_library()`**
       （Linux 上它 spawn `ldconfig -p`，找不到再 spawn C 编译器）
    ③ `get_backend()`（返回单例，理论 0）
    ④ `QTimer(self)` + setInterval + connect
    ⑤ `LayerDebugPanel(...)` 构造 → `_position_layer_panel()` → `.show()`
       （⑤ 是本仓库已知的痛处：H18 量到 Qt 原生窗口在 niri 下 xcb/wayland 两条路都坏，
        而这只面板正是**第二只 Qt 顶层窗口**）
    ⑥ `_set_layer_mode(True)`（②的 surface 创建在这里发生，旧读数 0–1 ms）

量法：**产品码一行不改**。`_init_layer_overlay_mode` 里 `is_available` / `get_backend`
/ `LayerDebugPanel` 三个名字都是**函数级 import**，绑定发生在调用那一刻 ⇒ 在源模块上
打补丁就被本跑吃到；`_position_layer_panel` / `_set_layer_mode` 用实例属性遮蔽；① 走
`builtins.__import__` 记账（只记这两个模块名，其余原样转发）。每段报 `[起点 → 耗时]`
（距本段开跑的 ms），树形缩进表嵌套，顶层减去各子段之和 = 未记账的差。

两臂，各跑在自己的进程里：

* `--full`  与 H15 同形：先跑 Live2D 活体渲染取出真帧再挂 ⇒ 与既有那 10 个数可比。
* `--bare`  跳过渲染前奏（`render_offscreen` 给一张全透明 QImage）⇒ 只留挂载这一段。

两臂都打印**计时之前** `sys.modules` 里那两个模块在不在：① 的成本是否已被前奏付掉，
由打印出来的事实决定，不由"应该没被 import 过"的猜测决定。

`--repeat N` 在同进程里再挂 N-1 次（每次一只新宿主）。第 2 次起 ①②里的 `_load` 都命中
缓存，读数是"暖态还剩多少"，用来把**每进程一次**的成本与**每次挂载**的成本分开。
※ 附带后果要写明：同进程二次挂载会再走一遍 `enable()`，它自带幂等回收（先销毁旧 ctx）
  ⇒ 合成器侧只留一只 meapet 浮层，但**面板窗口会多一只**（每宿主各一只，进程退出才散）。

副作用（真机）：起桌宠宿主窗口 + 面板窗口 + layer 浮层，进程退出即散。
**不改 scale、不改配置、不落盘**（`_save_config` 换记录器）。

用法：

    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h19_mount_breakdown.py --full

退出码：0 = 拆分账本成立（未记账差 ≤ max(20 ms, 总段 5%)）；
2 = 对不上 ⇒ 还有没被这六段覆盖的耗时，本探针的覆盖假设不成立。
本件**不判产品对错**，只把"是哪一段"从猜测变成读数。
"""
from __future__ import annotations

import argparse
import builtins
import ctypes.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MEASURED = ("meapet.desktop.wayland_layer", "meapet.desktop.layer_debug_panel")


class Ledger:
    """嵌套计时账本：rows 里每条是 (深度, 名字, 起点偏移 ms, 耗时 ms)。"""

    def __init__(self, t0: float):
        self.t0 = t0
        self.depth = 0
        self.rows: list[tuple[int, str, float, float]] = []

    def span(self, name: str):
        ledger = self

        class _S:
            def __enter__(self):
                ledger.depth += 1
                self.a = (time.perf_counter() - ledger.t0) * 1000.0
                return ledger

            def __exit__(self, *exc):
                b = (time.perf_counter() - ledger.t0) * 1000.0
                ledger.rows.append((ledger.depth - 1, name, self.a, b - self.a))
                ledger.depth -= 1
                return False

        return _S()

    def leaf(self, name: str, fn, *a, **k):
        t = time.perf_counter()
        r = fn(*a, **k)
        self.rows.append((self.depth, name, (t - self.t0) * 1000.0,
                          (time.perf_counter() - t) * 1000.0))
        return r

    def dump(self) -> tuple[float, float]:
        """打印树；返回 (顶层那一段的耗时, 未被深度-1 各段覆盖的部分)。

        rows 是**完成**顺序（子段先关 ⇒ 先入表），所以打印前按起点排，父段才在子段之前。
        只把深度 1 的段算作"覆盖"：更深的段必然嵌在某个深度-1 段里，加进来就是重复计账。
        """
        top = next((r for r in self.rows if r[0] == 0), None)
        total = top[3] if top else 0.0
        covered = sum(r[3] for r in self.rows if r[0] == 1)
        for depth, name, off, dur in sorted(self.rows, key=lambda r: r[2]):
            print(f"  {'    ' * depth}[{off:8.1f} → {dur:8.1f} ms]  {name}")
        return total, total - covered


ACTIVE = {"led": None}
ORIG: dict = {}


def _timed(name: str, fn):
    def wrapper(*a, **k):
        led = ACTIVE["led"]
        if led is None:
            return fn(*a, **k)
        with led.span(name):
            return fn(*a, **k)
    return wrapper


def _timed_leaf(name: str, fn):
    def wrapper(*a, **k):
        led = ACTIVE["led"]
        if led is None:
            return fn(*a, **k)
        return led.leaf(name, fn, *a, **k)
    return wrapper


def _patch_wayland_layer(wl) -> None:
    for attr, key, label in (
        ("is_available", "wl_is_available", "② layer_available()  = 单例 is_available()"),
        ("get_backend", "wl_get_backend", "③ get_backend()"),
        ("open_shim", "wl_open_shim", "  open_shim()  [dlopen .so]"),
        ("shim_candidates", "wl_shim_candidates", "  shim_candidates()"),
    ):
        ORIG[key] = getattr(wl, attr)
        setattr(wl, attr, _timed(label, ORIG[key]))


def _patch_panel(ldp) -> None:
    cls = ldp.LayerDebugPanel
    ORIG["ldp_panel_init"] = cls.__init__
    ORIG["ldp_panel_show"] = cls.show
    cls.__init__ = _timed("⑤ LayerDebugPanel(...) 构造", ORIG["ldp_panel_init"])
    cls.show = _timed("⑤ panel.show()  [第二只 Qt 顶层窗口]", ORIG["ldp_panel_show"])


def install_hooks() -> None:
    """装钩子（一次性）。**不在这里 import 那两个被测模块** —— 先 import 就把 ① 那笔
    成本付掉了，探针要量的东西随之消失。补丁改由 `__import__` 追踪器在模块**装完之后**
    立刻打（函数级 import 的绑定发生在追踪器返回之后，所以赶得上）。"""
    ORIG["__import__"] = builtins.__import__
    ORIG["find_library"] = ctypes.util.find_library
    ORIG["patched"] = set()
    real_import = ORIG["__import__"]

    def traced_import(name, globals=None, locals=None, fromlist=(), level=0):
        measured = name in MEASURED
        fresh = measured and name not in sys.modules
        led = ACTIVE["led"]
        if fresh and led is not None:
            r = led.leaf(f"① import {name}", real_import, name, globals, locals,
                         fromlist, level)
        else:
            r = real_import(name, globals, locals, fromlist, level)
        if measured:
            _patch_now(name)
        return r

    def _patch_now(name):
        """模块一装完就补它的钩子：函数级 import 的绑定发生在返回之后，赶得上。
        已在前奏里 import 过的模块也走这里 —— 否则 ②③⑤ 三段根本没有读数。"""
        patched = ORIG["patched"]
        if name in patched or name not in sys.modules:
            return
        patched.add(name)
        mod = sys.modules[name]
        if name.endswith("wayland_layer"):
            _patch_wayland_layer(mod)
        else:
            _patch_panel(mod)

    builtins.__import__ = traced_import
    ctypes.util.find_library = _timed_leaf(
        "  ctypes.util.find_library(...)  [spawn 子进程]", ORIG["find_library"])
    for name in MEASURED:
        _patch_now(name)


def remove_hooks() -> None:
    import meapet.desktop.layer_debug_panel as ldp
    import meapet.desktop.wayland_layer as wl

    builtins.__import__ = ORIG["__import__"]
    ctypes.util.find_library = ORIG["find_library"]
    for attr, key in (("is_available", "wl_is_available"),
                      ("get_backend", "wl_get_backend"),
                      ("open_shim", "wl_open_shim"),
                      ("shim_candidates", "wl_shim_candidates")):
        if key in ORIG:
            setattr(wl, attr, ORIG[key])
    if "ldp_panel_init" in ORIG:
        ldp.LayerDebugPanel.__init__ = ORIG["ldp_panel_init"]
        ldp.LayerDebugPanel.show = ORIG["ldp_panel_show"]


def wrap_host(host) -> None:
    """实例属性遮蔽，只影响这只宿主。"""
    host._position_layer_panel = _timed("⑤ _position_layer_panel()",
                                         host._position_layer_panel)
    host._set_layer_mode = _timed("⑥ _set_layer_mode(penetrate)  [建/映射 layer surface]",
                                  host._set_layer_mode)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="与 H15 同形：先跑活体渲染取真帧")
    ap.add_argument("--bare", action="store_true", help="跳过渲染前奏，只留挂载这一段")
    ap.add_argument("--repeat", type=int, default=1, help="同进程内重复挂载的次数")
    args = ap.parse_args()
    if not (args.full ^ args.bare):
        print("必须二选一 --full / --bare（两臂的 import 面不同，不许混着跑）")
        return 1

    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage
    from PyQt5.QtWidgets import QApplication, QLabel, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    print(f"QGuiApplication.platformName = {app.platformName()!r}")

    import probe_h12_product_path as H12  # noqa: E402
    import probe_h4_ceiling_infer as H4  # noqa: E402
    from meapet.desktop.render_host import PetRenderHostMixin  # noqa: E402

    if args.full:
        import probe_h1_coexistence as H1  # noqa: E402
        import probe_h7_roi_census as H7  # noqa: E402
        frames, _ri, l_host, _lw = H7.H3.render_live2d_frames(app, 1)
        if not frames:
            print("VERDICT-BREAKDOWN : ✗ 活体渲染不可得 ⇒ 量具自身失效，不猜")
            return 2
        l_host.hide()
        H4.pump(app, 400)
        qf = H1.qimage_from(frames[0])
        H12.fact("臂", f"full（与 H15 同形；帧 {qf.width()}×{qf.height()}）")
    else:
        qf = QImage(240, 320, QImage.Format_ARGB32)
        qf.fill(Qt.transparent)
        H12.fact("臂", "bare（前奏只到 Qt；帧是 240×320 全透明）")

    H12.fact("计时前的 import 面",
             json.dumps({m: (m in sys.modules) for m in MEASURED}, ensure_ascii=False)
             + "　⇒ ① 是否已被前奏付掉，看这一行")

    from meapet.config.store import load_config
    cfg = load_config()
    cfg.setdefault("fidus", {})["enabled"] = False

    class WiringHost(QWidget, PetRenderHostMixin):
        """与 H15 的 `WiringHost` 同形：挂载/定位/套用三件是产品原文，外围依赖换记录器。"""

        def __init__(self, cfg):
            super().__init__()
            self.config = cfg
            self.bubbles: list[str] = []
            self.saves = 0

        def _show_bubble(self, text, duration_ms=None, mood=None):
            self.bubbles.append(str(text))

        def _save_config(self):
            self.saves += 1

    install_hooks()
    hosts, totals, gaps = [], [], []
    try:
        for n in range(max(1, args.repeat)):
            host = WiringHost(cfg)
            label = QLabel(host)
            label.render_offscreen = lambda: qf             # type: ignore[attr-defined]
            host.sprite_label = label                       # type: ignore[attr-defined]
            host.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            host.setAttribute(Qt.WA_QuitOnClose, False)
            host.show()
            H4.pump(app, 600)
            host.move(120 + 24 * n, 60 + 24 * n)
            H4.pump(app, 200)
            hosts.append(host)
            wrap_host(host)

            ACTIVE["led"] = Ledger(time.perf_counter())
            led = ACTIVE["led"]
            with led.span(f"#{n} host._init_layer_overlay_mode()"
                          "  ← H15 的 `A0 挂载` 就是这一段"):
                host._init_layer_overlay_mode()
            H4.pump(app, 300)
            total, gap = led.dump()
            ACTIVE["led"] = None
            print(f"  → 第 {n} 次（{'冷，本进程首次' if n == 0 else '暖'}）："
                  f"总 {total:.1f} ms｜未记账差 {gap:.1f} ms")
            totals.append(total)
            gaps.append(gap)
    finally:
        for host in hosts:
            try:
                host._set_layer_mode(False)
            except Exception as exc:
                print(f"  收尾 disable 失败（不影响读数）: {type(exc).__name__}: {exc}")
            host.hide()
        H4.pump(app, 300)
        remove_hooks()

    for i in range(3):
        t = time.perf_counter()
        r = ORIG["find_library"]("layer_shell_shim")
        dt = (time.perf_counter() - t) * 1000.0
        print(f"  find_library 暖态第 {i + 1} 次：{dt:8.1f} ms → {r!r}")

    line = max(totals)
    worst = max((abs(g) for g in gaps), default=0.0)
    ok = worst <= max(20.0, 0.05 * line)
    print(f"\nVERDICT-BREAKDOWN : {'PASS' if ok else 'RED'}"
          f"　未记账差最大 {worst:.1f} ms（判据 ≤ max(20 ms, 总段 5%)）")
    print("  ※ 这一条判的是**量具覆盖是否成立**，不是产品快不快。"
          "红了 = 还有第七段没被这六段吃掉。")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
