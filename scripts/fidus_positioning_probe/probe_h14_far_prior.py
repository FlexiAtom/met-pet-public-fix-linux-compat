#!/usr/bin/env python3
"""H14 · 远偏先验：**产品的动机场景**——我方信念是错的，而且错得远。

H12/H13 全部跑在"信念恰好为真"这一最好档上（本机 niri 照请求摆了位，差 0.00 px）。
但本功能的**前提**恰恰相反：layer-shell 会改写我们请求的位置（§12r：请求 (480,300)
被摆到屏幕正中），所以生产里喂给 `locate()` 的信念可能偏上百像素。冷获取的先验偏开时
会发生三件事之一，本件要分清是哪一件：

* **窗内**：`half = 1.5·s + 48`，真位仍落在搜索窗里 ⇒ 一发命中（H13 B 相已证 32 px 档）；
* **靠失稳展宽救回**：`+64 px/失稳连读`，所以多读几发窗会自己长大（H11 在修窗前的 `-58` 上：
  偏 440 px ⇒ 第 6 发才锁；中心系修法到场后同臂实测第 5 发）⇒ 我方 `ACQUIRE_READS` 够不够；
* **救不回**：整段退回 `None` ⇒ 产品沿用合成数，**不跳位**（这是可接受的出口，不是失败）。

判据一律是**独立尺子**（grim + 宿主 NCC，不经 fidus）。用法：

    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h14_far_prior.py \\
        --offsets 0,50,100,150,200,250,300,350,400,450 --y-offsets 0,50,150,300

`--y-offsets` 是第二轮加的：横向那一维有**屏宽**可寻址（先验偏到 450 时窗已被推到屏右沿外，
clamp 之后窗实际落在哪是一个未知量），纵向没有这条边界。若只有横档翻面，就得先排除
"翻面来自 clamp 而不是来自偏移本身"。

`--ambiguous` 是第三轮加的：y 档量到"锁到孪生峰 ⇒ 报出离真值 +102 px 的数**且照样过位移闭环**"
（不同先验给出同一个错答案）。位移闭环只证"读数跟着我摆的位走"，对**系统性偏**结构上是瞎的，
所以产品需要另一条拦截路。fidus 面上本来就写着 `ambiguous` 这颗桩，先问它能不能拦，
拦不住才轮到宿主自己造判据。

副作用：真桌面挂/拆 layer 浮层、一次 `calibrate_once`、每档 2 次摆位 + 若干次整屏抓取。
退出码：0 = 每档都跑到出口（退回也是出口）；1 = 量具/环境不可用。
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import probe_a10_geometry as A10  # noqa: E402
import probe_h12_product_path as H12  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402
from meapet.desktop import fidus_position as FP  # noqa: E402

OFFSETS = (0, 50, 150, 300, 450)   # 信念偏这么多 px（正的偏 = 我们以为自己更靠右）
FACT = H12.fact
SETTLE_RE = re.compile(r"第 (\d+) 发定住")


def parse_offsets(text: str) -> tuple[int, ...]:
    """`--offsets 0,100,200` ⇒ (0,100,200)。空串退回首档。"""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return tuple(int(p) for p in parts) if parts else OFFSETS


class Counting(FP.FidusEngine):
    """数 `estimate()` 的发数：产品要花几发才拿到答案，是要报给手感的东西。"""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def estimate(self):
        self.reads += 1
        return super().estimate()


class Ambiguous(Counting):
    """注册时把 `ambiguous=True` 递给引擎（fidus 面上本来就有的那颗桩）。

    动机：本轮 y 档量到"锁到孪生峰、报出离真值 +102 px 的数、且照样过位移闭环"。
    若 `ambiguous=True` 能让这类注册**直接抛**，产品就有一条不用自己造判据的出路。
    """

    def register(self, box, initial_center):
        eng = self._ensure()
        try:
            eng.register_target(box, True, initial_center=initial_center)
            return float(eng.confidence_ceiling)
        except BaseException as exc:  # noqa: BLE001 - 引擎异常派生自 BaseException
            raise FP.EngineError(f"register_target(ambiguous=True): "
                                 f"{type(exc).__name__}") from exc


def main(argv: tuple[str, ...] = ()) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="H14 · 远偏先验逐档（真机）")
    ap.add_argument("--offsets", default=",".join(str(v) for v in OFFSETS),
                    help="信念偏移档位，逗号分隔的整数 px")
    ap.add_argument("--y-offsets", default="",
                    help="加一列**纵向**偏移档（同样逗号分隔）：横向有屏宽可寻址，纵向没有")
    ap.add_argument("--ambiguous", action="store_true",
                    help="注册时递 `ambiguous=True`（fidus 面自带的桩），看它能不能拦住孪生峰")
    ap.add_argument("--sizes", default="",
                    help="限定候选贴片阶梯（逗号分隔，如 `96` 或 `160,96`）：默认用产品阶梯。"
                         "本旗是为回答「孪生峰是不是小贴片的专利」而加")
    args = ap.parse_args(list(argv) or None)
    offsets = parse_offsets(args.offsets)
    y_offsets = parse_offsets(args.y_offsets) if args.y_offsets else (0,)
    sizes = parse_offsets(args.sizes) if args.sizes else FP.SIZES
    arms = [(dx, dy) for dy in y_offsets for dx in offsets]

    from PyQt5.QtWidgets import QApplication

    APP = QApplication.instance() or QApplication(sys.argv)
    FACT("锚点", __import__("fidus").__git_commit__)
    FACT("被测代码", FP.__file__)
    FACT("档", f"横 {offsets} × 纵 {y_offsets} = {len(arms)} 臂")
    FACT("阶梯", f"{tuple(sizes)}")

    frames, _rinfo, l_host, _l_widget = H7.H3.render_live2d_frames(APP, 1)
    if not frames:
        print("VERDICT-FAR-PRIOR : ✗ 活体渲染不可得 ⇒ 量具自身失效，不猜")
        return 1
    l_host.hide()
    H4.pump(APP, 400)
    frame = frames[0]
    fh, fw = frame.shape[:2]

    geo = APP.primaryScreen().geometry()
    mask = frame[..., 3] > FP.ALPHA_MIN
    ys, xs = np.nonzero(mask)
    bx0, bx1 = int(xs.min()), int(xs.max()) + 1
    by0, by1 = int(ys.min()), int(ys.max()) + 1
    px = int(round((geo.width() - (bx1 - bx0)) / 2.0)) - bx0
    py = int(round((geo.height() - (by1 - by0)) / 2.0)) - by0
    base = (px, py)
    FACT("被测帧", f"{fw}×{fh} 指纹={H7.frames_digest([frame])}")
    FACT("基准位", f"请求 ({px},{py})，屏 {geo.width()}×{geo.height()}")

    host = H1.HostWindow()
    host.show()
    H4.pump(APP, 500)
    backend = H1.FACADE.get_backend()
    qf = H1.qimage_from(frame)
    backend.enable(host, fw, fh, px, py)
    cur = list(base)

    def place(x: int, y: int) -> None:
        cur[0], cur[1] = x, y
        backend.set_position(x, y)
        H4.pump(APP, 400)
        backend.update_pixels(qf)       # 冻在帧 0：只谈几何
        H4.pump(APP, 400)

    def move(dx: float, dy: float) -> None:
        place(cur[0] + int(round(dx)), cur[1] + int(round(dy)))

    H4.pump(APP, 1200)
    backend.update_pixels(qf)
    H4.pump(APP, 1400)
    box, off = H12.probe_box(frame)
    if box is None:
        print("VERDICT-FAR-PRIOR : ✗ 选不出独立量具盒 ⇒ 量具自身失效，不猜")
        backend.destroy_context()
        return 1

    def truth(tag: str):
        shot = A10.capture(tag)
        if shot is None:
            return None
        peak, x, y = A10.Correlator(H12.luma(shot)).peak(H12.luma(box))
        edge = box.shape[0]
        return (float(x + edge / 2.0 - off[0]), float(y + edge / 2.0 - off[1]), float(peak))

    t = truth("h14_gate")
    if t is None or t[2] < H12.PRESENT_PEAK:
        print(f"VERDICT-VISIBLE : ✗ 独立尺子峰 {'-' if t is None else f'{t[2]:.4f}'}"
              f" < {H12.PRESENT_PEAK} ⇒ 我方内容不在屏上，本件不产出读数")
        backend.destroy_context()
        return 1
    FACT("在位闸", f"真值 surface 中心 ({t[0]:.2f},{t[1]:.2f}) 峰={t[2]:.4f}"
                   "（grim＋宿主 NCC，不经 fidus）")

    engine = Ambiguous() if args.ambiguous else Counting()
    engine.calibrate()
    FACT("注册臂", "ambiguous=True" if args.ambiguous else "默认（ambiguous=False）")
    print(f"\n=== 逐档远偏先验（每档一次完整 `FP.locate`，含 +{FP.MOVE_PX:.0f} px 探针位移）===")
    print(f"{'信念偏':>12} {'出口':>6} {'贴片':>6} {'ceiling':>9} {'报出−真值':>14} "
          f"{'发数':>5} {'秒':>6}")
    rows = []
    for bias_x, bias_y in arms:
        tag = f"{bias_x}_{bias_y}"
        place(*base)
        ta = truth(f"h14_b{tag}")
        if ta is None or ta[2] < H12.PRESENT_PEAK:
            print(f"{tag:>12}  ✗ 独立尺子看不见 ⇒ 该档作废（不猜）")
            continue
        believed = (ta[0] + bias_x, ta[1] + bias_y)   # 故意告诉产品一个错中心
        notes = []
        engine.reads = 0
        t0 = time.perf_counter()
        fix = FP.locate(engine, frame, believed, move=move, sizes=sizes,
                        log=lambda k, v: notes.append((k, v)))
        secs = time.perf_counter() - t0
        settles = [int(m.group(1)) for k, v in notes
                   if k == "读数" for m in [SETTLE_RE.search(v)] if m]
        why = "" if fix is not None else (notes[-1][1] if notes else "无 log")
        label = f"({bias_x:+d},{bias_y:+d})"
        if fix is None:
            print(f"{label:>12} {'退回':>6} {'-':>6} {'-':>9} {'-':>14} "
                  f"{engine.reads:>5} {secs:>5.1f}s　{why}")
        else:
            err = (fix.center[0] - ta[0], fix.center[1] - ta[1])
            print(f"{label:>12} {'有值':>6} {fix.edge:>5}² {fix.ceiling:>9.6f} "
                  f"({err[0]:+7.2f},{err[1]:+6.2f}) {engine.reads:>5} {secs:>5.1f}s　"
                  f"定住发数={settles}")
        rows.append({"bias": label, "fix": fix,
                     "err": None if fix is None else
                     (fix.center[0] - ta[0], fix.center[1] - ta[1]),
                     "reads": engine.reads, "secs": secs})
        for k, v in notes:
            if fix is None and k in ("候选被拒", "获取读数", "位移闭环", "闭环重注册", "读数"):
                print(f"        · {k}: {v}")

    place(*base)
    backend.destroy_context()
    H4.pump(APP, 600)

    print("\n=== 汇总 ===")
    if not rows:
        print("VERDICT-FAR-PRIOR : ✗ 无有效档 ⇒ 量具没跑到出口，本件不产出任何主张")
        return 1
    valued = [r for r in rows if r["fix"] is not None]
    worst = max((max(abs(r["err"][0]), abs(r["err"][1])) for r in valued), default=None)
    print(f"VERDICT-FAR-PRIOR : {len(valued)}/{len(rows)} 档有值；"
          f"有值档最坏误差 {'-' if worst is None else f'{worst:.2f} px'}；"
          f"退回档 = {[r['bias'] for r in rows if r['fix'] is None] or '无'}")
    print(f"VERDICT-BUDGET : 有值档发数 {[r['reads'] for r in valued]}／"
          f"秒 {[round(r['secs'], 1) for r in valued]}（不含每会话一次的校准 "
          f"{engine.calibrated_ms:.0f} ms）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
