#!/usr/bin/env python3
"""H12 · 产品路径的真机首跑：走 `meapet.desktop.fidus_position`，不走探针自己的复刻。

前面十一件量具（H1–H11）证的是**引擎能力**；本件证的是**我方那段产品代码**：
候选枚举、ceiling 选尺、喂 `initial_center` 的换算、位移闭环、把读数换算回 surface 中心。
它同时也是 `fidus-switch-positioning` §2 反证表第 1 行（"换源后切换实测不跳位"）
迄今唯一能给的量具 —— 因为对表用的是**grim + 宿主 NCC 那把独立尺子**，不经 fidus。

副作用（真机）：挂一只 Live2D 尺寸的 layer 浮层、一次 `calibrate_once`（约 2 s + 屏幕闪）、
若干次整屏抓取、两次摆位请求（+48 px 与归位）。

用法：`.venv/bin/python scripts/fidus_positioning_probe/probe_h12_product_path.py`
"""
from __future__ import annotations

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
import probe_h1_coexistence as H1  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h6_dense_gate as H6  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402
import probe_h8_roi_on_screen as H8  # noqa: E402
from meapet.desktop import fidus_position as FP  # noqa: E402

PRESENT_PEAK = 0.90      # 独立尺子的在位闸：过不去就是量具断，不产出读数
TOL_PX = 2.0             # 产品判据允许与真值差这么多（H8 首锁量到的是 0.00 px）


def fact(label, value) -> None:
    H6.fact(label, value)


def luma(arr) -> np.ndarray:
    return H8.luma_of(arr)


def probe_box(frame):
    """**独立**选一只"质心处、整盒落在不透明轮廓里"的最大方盒，返回 (box, 盒心在帧内的偏移)。

    刻意不复用被测模块的 `iter_candidates` —— 独立尺子若和被测代码走同一条选盒路，
    选错盒这件事就永远不会暴露。
    也刻意不拿**整帧**去相关：真实产品帧四周是透明的，屏幕上那里是桌面，
    整帧相关的峰值天然掉到 1.0 以下（首跑就是 0.8950 被在位闸拦下），
    那种量具只会产假红，不会产出精度数字。
    """
    mask = frame[..., 3] > FP.ALPHA_MIN
    ys, xs = np.nonzero(mask)
    cx, cy = int(xs.mean()), int(ys.mean())
    h, w = frame.shape[:2]
    for edge in range(min(h, w), 15, -2):
        x0, y0 = cx - edge // 2, cy - edge // 2
        if x0 < 0 or y0 < 0 or x0 + edge > w or y0 + edge > h:
            continue
        if mask[y0:y0 + edge, x0:x0 + edge].all():
            return np.ascontiguousarray(frame[y0:y0 + edge, x0:x0 + edge]), \
                (x0 + edge / 2.0 - w / 2.0, y0 + edge / 2.0 - h / 2.0)
    return None, None


def box_truth(box, frame_offset, tag: str):
    """抓屏并对该小盒求峰 ⇒ surface 中心的独立真值（不经 fidus）。"""
    shot = A10.capture(tag)
    if shot is None:
        return None
    peak, x, y = A10.Correlator(luma(shot)).peak(luma(box))
    edge = box.shape[0]
    return (float(x + edge / 2.0 - frame_offset[0]),
            float(y + edge / 2.0 - frame_offset[1]), float(peak))


def main() -> int:
    from PyQt5.QtWidgets import QApplication

    APP = QApplication.instance() or QApplication(sys.argv)
    H6.fact("锚点", __import__("fidus").__git_commit__)
    H6.fact("被测代码", FP.__file__)

    frames, rinfo, l_host, l_widget = H7.H3.render_live2d_frames(APP, 1)
    if not frames:
        print("VERDICT-PRODUCT-PATH : ✗ 活体渲染不可得（模型目录／`_ready`）⇒ 量具自身失效，不猜")
        return 1
    # 产品的穿透模式也这么做：藏掉 Qt 顶层，靠 render_offscreen 主动取帧
    l_host.hide()
    H4.pump(APP, 400)
    frame = frames[0]
    fh, fw = frame.shape[:2]
    fact("被测帧", f"{fw}×{fh} 指纹={H7.frames_digest([frame])}")
    fact("静态化说明", "本件把动画冻在帧 0（反复推同一张）⇒ 只核几何与判据；"
                       "动画相的读数已在 H8／H9 量过，这里不重复主张")

    geo = APP.primaryScreen().geometry()
    mask = frame[..., 3] > FP.ALPHA_MIN
    ys, xs = np.nonzero(mask)
    bx0, bx1 = int(xs.min()), int(xs.max()) + 1
    by0, by1 = int(ys.min()), int(ys.max()) + 1
    px = int(round((geo.width() - (bx1 - bx0)) / 2.0)) - bx0
    py = int(round((geo.height() - (by1 - by0)) / 2.0)) - by0
    fact("摆位", f"请求 ({px},{py})，不透明 bbox=({bx0},{by0})-({bx1},{by1}) "
                f"⇒ 让轮廓整体落在 {geo.width()}×{geo.height()} 屏内")

    host = H1.HostWindow()
    host.show()
    H4.pump(APP, 500)
    backend = H1.FACADE.get_backend()
    qf = H1.qimage_from(frame)
    backend.enable(host, fw, fh, px, py)
    cur = [px, py]

    def push() -> None:
        backend.update_pixels(qf)

    def move(dx: float, dy: float) -> None:
        cur[0] += int(round(dx))
        cur[1] += int(round(dy))
        backend.set_position(cur[0], cur[1])
        H4.pump(APP, 400)
        push()
        H4.pump(APP, 400)

    H4.pump(APP, 1200)
    push()
    H4.pump(APP, 1400)
    box, off = probe_box(frame)
    if box is None:
        print("VERDICT-PRODUCT-PATH : ✗ 选不出独立量具盒 ⇒ 量具自身失效，不猜")
        backend.destroy_context()
        return 1
    t0 = box_truth(box, off, "h12_gate")
    if t0 is None or t0[2] < PRESENT_PEAK:
        peak = "-" if t0 is None else f"{t0[2]:.4f}"
        print(f"VERDICT-VISIBLE  : ✗ 独立尺子峰 {peak} < {PRESENT_PEAK} ⇒ 我方内容不在屏上，"
              "本行不判（不是 fidus 的否证）")
        backend.destroy_context()
        return 1
    fact("在位闸", f"{box.shape[0]}² 独立盒真值 surface 中心 ({t0[0]:.2f},{t0[1]:.2f}) "
                f"峰={t0[2]:.4f}（grim＋宿主 NCC，不经 fidus）")

    print("\n=== 走产品代码：FP.locate（真引擎、真桌面）===")
    engine = FP.FidusEngine()
    believed = (px + fw / 2.0, py + fh / 2.0)
    fact("信念", f"surface 中心 ({believed[0]:.2f},{believed[1]:.2f}) —— 与真值差 "
                f"({believed[0] - t0[0]:+.2f},{believed[1] - t0[1]:+.2f}) px（niri 改写请求位的既有事实）")
    t_start = time.perf_counter()
    fix = FP.locate(engine, frame, believed, move=move,
                    log=lambda k, v: fact(k, v))
    secs = time.perf_counter() - t_start

    if fix is None:
        print(f"VERDICT-PRODUCT-PATH : 出口=退回（None）｜{secs:.1f} s ⇒ §5.4 的整段退回被触发；"
              "本行不判精度，只记这一跑没能定位（上面每条 log 是原因）")
        backend.destroy_context()
        return 0

    t1 = box_truth(box, off, "h12_after")
    if t1 is None or t1[2] < PRESENT_PEAK:
        print("VERDICT-PRODUCT-PATH : ✗ 事后独立尺子看不见内容 ⇒ 量具断，不判精度")
        backend.destroy_context()
        return 1
    # locate 报的是"探针位移之前"那一刻的 surface 中心
    expect = (t1[0] - FP.MOVE_PX, t1[1])
    err = (fix.center[0] - expect[0], fix.center[1] - expect[1])
    fact("结果", f"贴片 {fix.edge}² conf={fix.conf:.6f} ceiling={fix.ceiling:.6f}　"
                f"报出 surface 中心 ({fix.center[0]:.2f},{fix.center[1]:.2f}) vs "
                f"独立真值 ({expect[0]:.2f},{expect[1]:.2f})")
    verdict = "PASS" if max(abs(err[0]), abs(err[1])) <= TOL_PX else "FAIL"
    print(f"VERDICT-PRODUCT-PATH : 误差 ({err[0]:+.2f},{err[1]:+.2f}) px ≤ {TOL_PX} ⇒ {verdict}"
          f"｜全程 {secs:.1f} s（含首条整帧读数与闭环第二发）")
    print(f"VERDICT-CALIBRATIONS : calibrate_once 次数={engine.calibrated_ms is not None}"
          f"｜单会话一次即为此跑的全部校准代价")
    backend.set_position(px, py)
    H4.pump(APP, 400)
    backend.destroy_context()
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
