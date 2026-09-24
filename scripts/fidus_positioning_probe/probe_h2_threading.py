#!/usr/bin/env python3
"""H2 · fidus 调用的线程亲和与 GUI 线程可接受性（真机 L3）

问什么（三条，全部只答"线程/timing"，不答落点精度——那是 §12e A10 的事）：

* **[T] 线程亲和**：主线程建的 `Fidus` 被工作线程用，会怎样？失败是响亮还是静默？
  失败形态若**不是** `Exception` 子类，宿主写 `except Exception` 就接不住（这是本探针要钉的坑）。
* **[A]/[B] GUI 可接受性**：把"建→注册→标定→estimate×N"整段放到工作线程上跑，
  GUI 线程（Qt 事件循环 + 2ms 精密计时器）的 tick 间隙被拉到多大？
* **[A] vs [B] 归因**：同一段序列，A 臂 GUI 空转、B 臂 GUI 以 60Hz 重绘目标窗（**只重绘、不改像素**
  ⇒ fidus 看到的画面恒定，两臂之差只归因到"GUI 忙不忙"）。两臂 `estimate` 墙钟之差 ⇒ 回答
  "秒级 estimate 到底是 fidus 固有，还是被 GUI/GIL 拖累"。

目标窗**全程静止**：niri 会吞掉/改判 `move()` 请求（§12e 实测 Qt 自报 (0,0) 而画面在别处），
搬窗只会让"目标是否上屏"变成噪声源，而本件三问都不需要位移。

用法（真机，会显示一个目标窗、抓屏做存在性闸门）：
```
WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \
  .venv/bin/python scripts/fidus_positioning_probe/probe_h2_threading.py --est 12
```

失效边界：单输出、本机 scale、niri 合成器；60Hz 重绘一个 200×200 图 ≠ 产品 Live2D 的真实绘制代价；
`estimate` 的绝对值随桌面内容复杂度变（§12f-8 那个双峰），**两臂之差**才是本件主张。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt5.QtCore import QEventLoop, Qt, QTimer  # noqa: E402
from PyQt5.QtGui import QColor, QPainter  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

import fidus  # noqa: E402
import probe_a10_geometry as A10  # noqa: E402

TARGET_X0, TARGET_Y0 = 640, 360
TICK_MS = 2
BUSY_MS = 16
EST_GAP_S = 0.03


def fact(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


def pump(app, ms: int) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents(QEventLoop.AllEvents, 20)


def timed(fn):
    """跑一次调用，返回 (起点, 墙钟秒, 判决串, 结果/消息)。判决串**不翻译**，直接用异常类名。"""
    t0 = time.perf_counter()
    try:
        obj = fn()
        return t0, time.perf_counter() - t0, "ok", obj
    except BaseException as exc:  # noqa: BLE001  这里要的就是"到底抛哪一个"
        msg = f"{type(exc).__name__}: {str(exc).replace(chr(10), ' ')[:180]}"
        return t0, time.perf_counter() - t0, "RAISED", msg


# ─────────────────────────────────────────────  GUI 侧
class PatternWindow(QWidget):
    """把 `A10.make_pattern()` 原样画出的可移动目标窗（结构够杂 ⇒ 不被方差门当平坦块拒）。

    忙臂只 `update()` **不改像素**：GUI 线程的绘制代价是真的（Qt 一定回调 paintEvent），
    而 fidus 看到的模板恒定 ⇒ 两臂之差只归因到"GUI 忙不忙"，不混进"目标变了"。
    """

    def __init__(self, img):
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(img.width(), img.height())
        self._img = img
        self.paint_events = 0

    def paintEvent(self, ev):  # noqa: N802
        self.paint_events += 1
        p = QPainter(self)
        p.drawImage(0, 0, self._img)
        p.end()


class Jitter:
    """GUI 线程上 2ms 精密计时器的间隙统计：间隙大 ⇒ 事件循环被谁占住了。"""

    def __init__(self):
        self.pairs: list[tuple[float, float]] = []
        self._last = 0.0

    def on_tick(self):
        now = time.perf_counter()
        if self._last:
            self.pairs.append((now, now - self._last))
        self._last = now


def preflight(app, win, pattern, scales) -> dict:
    """存在性闸门：目标窗必须真的在屏上、没被遮住，否则后面所有 timing 都是失败路径的 timing。

    这是 §12f 那条坑的复用——"探针必须在抓帧前做存在性闸门"：本轮首跑 24 次 estimate 全部
    `FidusTargetLost`，而独立 NCC 却以峰值 1.0 找到图案 ⇒ 差别在**屏幕上有没有别的东西盖住它**，
    不在 fidus。没有这道闸门，探针会把"被遮挡"误报成"调用很慢"。
    """
    out: dict = {}
    for attempt in range(5):
        win.raise_()
        pump(app, 500)
        shot = A10.capture(f"h2_gate{attempt}")
        if shot is None:
            out["verdict"] = "抓帧失败"
            return out
        m = A10.measure(shot, pattern, scales)
        out["peak"] = m["peak"]
        out["meas"] = {k: m[k] for k in ("scale", "x", "y", "w", "h", "cx", "cy")}
        out["shot"] = (shot.shape[1], shot.shape[0])
        out["attempts"] = attempt + 1
        if m["peak"] > 0.5:
            out["verdict"] = "ok"
            return out
    out["verdict"] = "被遮/未上屏"
    return out


# ─────────────────────────────────────────────  工作线程：建、用、释放全在本线程
def run_seq(n_est: int, pattern):
    """任何一步失败就停，但**不抛**——失败形态本身是数据。"""
    seq: list[dict] = []
    tid = threading.get_ident()
    t0, dur, verdict, obj = timed(fidus.Fidus.build_wayland)
    seq.append({"step": "build_wayland", "t0": t0, "dur": dur, "verdict": verdict,
                "detail": "" if verdict == "ok" else str(obj)[:160]})
    if verdict != "ok":
        return seq, tid
    eng = obj
    t0, dur, verdict, out = timed(eng.gate_status)
    seq.append({"step": "gate_status", "t0": t0, "dur": dur, "verdict": verdict,
                "detail": str(out)[:120]})
    t0, dur, verdict, out = timed(lambda: eng.register_target(np.ascontiguousarray(pattern), False))
    seq.append({"step": "register_target", "t0": t0, "dur": dur, "verdict": verdict,
                "detail": str(out)[:120]})
    if verdict != "ok":
        return seq, tid
    t0, dur, verdict, out = timed(eng.calibrate_once)
    seq.append({"step": "calibrate_once", "t0": t0, "dur": dur, "verdict": verdict,
                "detail": str(out)[:160]})
    if verdict != "ok":
        return seq, tid
    for i in range(n_est):
        time.sleep(EST_GAP_S)  # 留出 GUI 呼吸：测的是"单次调用代价 + 事件循环间隙"，不是吞吐
        t0, dur, verdict, out = timed(eng.estimate)
        seq.append({"step": f"estimate#{i}", "t0": t0, "dur": dur, "verdict": verdict,
                    "detail": str(out)[:120]})
    # `eng` 是本帧局部名：函数返回即在工作线程（它的建立线程）上释放，不跨线程 del。
    return seq, tid



def stats(vals: list[float]) -> str:
    if not vals:
        return "n=0"
    a = np.array(vals, dtype=np.float64)
    return (f"n={len(a)} 中位 {np.median(a) * 1000:.1f} ms "
            f"p95 {np.percentile(a, 95) * 1000:.1f} ms 最大 {a.max() * 1000:.1f} ms")


def gaps_by_phase(jit: Jitter, seq: list[dict]) -> dict[str, list[float]]:
    """把 GUI tick 间隙按"该时刻哪一步 fidus 调用在飞"分桶；不在任何调用内 ⇒ idle。"""
    out: dict[str, list[float]] = {}
    for t_end, gap in jit.pairs:
        key = "idle"
        for e in seq:
            name = e["step"].split("#")[0]
            if e["t0"] <= t_end <= e["t0"] + e["dur"]:
                key = name
                break
        out.setdefault(key, []).append(gap)
    return out


def est_vals(seq: list[dict]) -> list[float]:
    return [e["dur"] for e in seq if e["step"].startswith("estimate") and e["verdict"] == "ok"]


def one_durs(seq: list[dict], step: str) -> list[float]:
    return [e["dur"] for e in seq if e["step"] == step and e["verdict"] == "ok"]


def affinity_probe(app) -> None:
    print("\n[T] 线程亲和（`Fidus` = `#[pyclass(unsendable)]`）")
    _, _, verdict, obj = timed(fidus.Fidus.build_wayland)
    if verdict != "ok":
        fact("主线程 build", f"{obj} ⇒ [T] 后续不可判")
        return
    pump(app, 200)
    main_obj = fidus.Fidus.build_wayland()
    box: dict = {}

    def worker():
        try:
            box["r"] = main_obj.gate_status()
        except BaseException as exc:  # noqa: BLE001
            box["type"] = type(exc).__name__
            box["mro"] = " -> ".join(c.__name__ for c in type(exc).__mro__[:6])
            box["is_exc"] = isinstance(exc, Exception)
            box["msg"] = str(exc).replace("\n", " ")[:200]

    th = threading.Thread(target=worker)
    th.start()
    while th.is_alive():
        pump(app, 50)
    th.join()
    fact("跨线程用主线程建的对象", box.get("type", f"未抛异常（返回 {box.get('r')}）"))
    if "type" in box:
        fact("是否 Exception 子类",
             f"{box['is_exc']} ⇒ {'`except Exception` 接得住' if box['is_exc'] else '`except Exception` **接不住**，须 `except BaseException`'}")
        fact("MRO", box["mro"])
        fact("消息", box["msg"])
    # `main_obj` 是本帧局部名：函数返回即在主线程（它的建立线程）上释放，不跨线程 del。


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--est", type=int, default=12, help="每臂 estimate 次数")
    ap.add_argument("--arm", default="both", choices=["A", "B", "both"], help="A=GUI 空转 B=GUI 忙")
    args = ap.parse_args()

    print("=== H2 · fidus 线程亲和与 GUI 可接受性 ===")
    so = getattr(getattr(fidus, "fidus", None), "__file__", "")
    fact("fidus", f"{getattr(fidus, '__version__', '?')} so={Path(so).name if so else '?'}")
    app = QApplication(sys.argv)
    try:
        outs = json.loads(subprocess.run(["niri", "msg", "--json", "outputs"],
                                         capture_output=True, text=True, timeout=8).stdout)
        name = next(iter(outs))
        fact("输出", f"{name} size={outs[name].get('size')} scale={outs[name].get('scale')}")
    except Exception as exc:  # noqa: BLE001
        fact("输出", f"读不到 {type(exc).__name__}")

    affinity_probe(app)

    pattern = A10.make_pattern()
    win = PatternWindow(A10.qimage_from(pattern))
    win.move(TARGET_X0, TARGET_Y0)
    win.show()
    pump(app, 900)
    fact("目标窗", f"{win.width()}x{win.height()} @ ({win.x()},{win.y()}) 逻辑像素")

    scales = A10.scale_grid(1.0)
    gate = preflight(app, win, pattern, scales)
    fact("存在性闸门", f"{gate.get('verdict')} 尝试 {gate.get('attempts')} 次 "
         f"峰值 NCC={gate.get('peak', float('nan')):.4f} 抓帧={gate.get('shot')}")
    if gate.get("verdict") != "ok":
        print("[harness] ✗ 目标未稳定上屏：本轮不产出 timing 结论"
              "（失败路径的墙钟不是「estimate 有多慢」）", flush=True)
        return 1
    fact("独立实测(物理)", gate.get("meas"))

    arms = ["A", "B"] if args.arm == "both" else [args.arm]
    summary: dict[str, list[dict]] = {}
    gui_tid = threading.get_ident()
    for arm in arms:
        print(f"\n[{arm}] {'GUI 空转' if arm == 'A' else f'GUI 忙（{1000 // BUSY_MS}Hz 重绘目标窗、不改像素）'} · "
              f"工作线程跑完整序列 + {args.est} 次 estimate")
        jit = Jitter()
        tick_t = QTimer()
        tick_t.setTimerType(Qt.PreciseTimer)
        tick_t.timeout.connect(jit.on_tick)
        tick_t.start(TICK_MS)
        busy_t = QTimer()
        paints_before = win.paint_events
        if arm == "B":
            busy_t.timeout.connect(win.update)
            busy_t.start(BUSY_MS)

        result: dict = {}

        def runner():
            result["seq"], result["tid"] = run_seq(args.est, pattern)

        t_start = time.perf_counter()
        th = threading.Thread(target=runner, daemon=True)
        th.start()
        while th.is_alive():
            app.processEvents(QEventLoop.AllEvents, 20)
        th.join()
        for timer in (tick_t, busy_t):
            timer.stop()
        seq = result.get("seq", [])
        wall = time.perf_counter() - t_start
        fact("worker 线程", f"tid={result.get('tid')} vs GUI tid={gui_tid} ⇒ "
             f"{'不同（确在工作线程）' if result.get('tid') != gui_tid else '相同（异常！）'}")
        for e in seq:
            fact(f"  {e['step']}", f"{e['dur'] * 1000:.1f} ms {e['verdict']} {e['detail'][:80]}")
        summary[arm] = seq
        repaints = win.paint_events - paints_before
        fact("目标窗重绘", f"{repaints / max(wall, 1e-9):.1f} Hz（{repaints} 次 / {wall:.1f} s）")
        for phase, gaps in sorted(gaps_by_phase(jit, seq).items()):
            fact(f"GUI tick[{phase}]", f"{stats(gaps)}（名义 {TICK_MS} ms，n={len(gaps)}）")
        gate_after = preflight(app, win, pattern, [gate["meas"]["scale"]])
        fact("臂后闸门", f"{gate_after.get('verdict')} 峰值 NCC="
             f"{gate_after.get('peak', float('nan')):.4f}")

    print("\n[归因] 两臂 estimate 墙钟对比（本件唯一主张的结论形态）")
    for arm in arms:
        seq = summary.get(arm, [])
        n_all = len([e for e in seq if e["step"].startswith("estimate")])
        n_ok = len(est_vals(seq))
        fails = sorted({e["detail"][:60] for e in seq
                        if e["step"].startswith("estimate") and e["verdict"] != "ok"})
        fact(f"臂 {arm}", f"estimate {stats(est_vals(seq))}（成功 {n_ok}/{n_all}） | "
             f"calibrate {stats(one_durs(seq, 'calibrate_once'))}")
        for f in fails:
            fact(f"臂 {arm} 失败形态", f"{n_all - n_ok} 次 ⇒ {f}")
    if "A" in arms and "B" in arms:
        ea, eb = est_vals(summary["A"]), est_vals(summary["B"])
        if ea and eb:
            ratio = float(np.median(eb)) / float(np.median(ea))
            fact("B/A 中位比", f"{ratio:.2f} ⇒ "
                 + ("GUI 忙显著拖慢 estimate ⇒ 秒级代价有一部分在 GIL/合成器侧，宿主可通过降噪获益"
                    if ratio > 1.25 else
                    "GUI 忙几乎不影响 estimate ⇒ 秒级代价是 fidus 固有，与宿主忙闲无关"))
        else:
            fact("B/A 中位比", f"不可判（A 成功 {len(ea)} 次、B 成功 {len(eb)} 次 ⇒ 无有效样本）")
    fact("边界重申", "本件只答线程/timing。落点精度见 §12e(A10)，内容可定位性见 §12f(H3)；"
         "60Hz 重绘一个静态 200×200 图 ≠ 产品 Live2D 绘制代价。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
