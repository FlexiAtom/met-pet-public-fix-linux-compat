#!/usr/bin/env python3
"""H13 · 逐个候选贴片跑位移闭环：H12 那一次失败到底赖谁。

H12（产品路径真机首跑）的两条线索把"低 ceiling 所以跟不上"这条现成解释顶回去了：

* 首条读数 `(656.02,392.00)` 与我们喂进去的**信念** `(656.00,392.00)` 差 0.02 px ——
  它等于先验本身，而不是"搜索出来的峰"（当轮信念恰好为真：真值也是 670.50/386.00）。
* 请求 +48 px 后读数只走 **+4.31 px**。而 §12p.4 的 H8 在同一只模型、同一张屏上，
  用的是一片 ceiling 只有 **0.086081** 的 96² 贴片，跟随残差 **−0.08 px**。
  ⇒ ceiling **低**不是失败因；ceiling **高**（本轮 0.3677）也不背书跟得上。

于是剩下的可能只有三类，且**互相顶替不了**，本件一次分开：

1. **贴片内容**：我方枚举按"离不透明质心最近"取盒，可能系统性挑了平滑渐变区
   （H7 的 census 是按 `s_fine` 在**全体**可行盒里取 top-K，选出的盒位置完全不同）。
2. **喂先验这一手本身**：信念为真时首读可能只是**回声**，随后的更新窗太小／判据太松，
   使引擎留在原地（`conf == ceiling` 恒成立 ⇒ 满值置信不报"丢"，见 §12p.4 新事实 1）。
3. **surface 根本没真动 48 px**：niri 改写请求位是既有事实；若实际只走了 4 px，
   那 fidus 的读数就是**对的**，错的是我方对"闭环该拦什么"的想象。

判别办法是把第 3 类**单独钉死**：每一轮都先用独立尺子（grim + 宿主 NCC，不经 fidus）
量一次真实 surface 中心，再量移动后的真实中心 —— 于是"请求位移"与"实测位移"同表并排，
第 3 类一出现就会在"实测"那一列显形。

副作用（真机）：挂一只 layer 浮层、一次 `calibrate_once`（约 2 s）、每轮 2 次摆位请求 +
2 次整屏抓取。用法：

    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h13_loop_per_candidate.py
退出码：0 = 每轮都跑到出口（结论再否定也是 0）；1 = 量具／环境不可用。
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
import probe_h12_product_path as H12  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h6_dense_gate as H6  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402
from meapet.desktop import fidus_position as FP  # noqa: E402

MOVE_PX = FP.MOVE_PX      # 与产品判据同一个位移（48 px）
READS_AFTER_MOVE = 8      # 与 H8 的 `--est` 同档：连读才能把"第几发收敛"读出来
SETTLE = 3                # 稳态 = 末 3 发里取一发（见 C 相：瞬态会渗进均值）
CONVERGED_PX = 2.0        # 离独立真值这么近就叫"收敛"（与 H12 的判据同宽）
FACT = H12.fact


def main() -> int:
    from PyQt5.QtWidgets import QApplication

    APP = QApplication.instance() or QApplication(sys.argv)
    FACT("锚点", __import__("fidus").__git_commit__)
    FACT("被测代码", FP.__file__)

    frames, _rinfo, l_host, _l_widget = H7.H3.render_live2d_frames(APP, 1)
    if not frames:
        print("VERDICT-LOOP : ✗ 活体渲染不可得（模型目录／`_ready`）⇒ 量具自身失效，不猜")
        return 1
    l_host.hide()                      # 产品的穿透模式也这么做：只留 layer 那一张
    H4.pump(APP, 400)
    frame = frames[0]
    fh, fw = frame.shape[:2]
    FACT("被测帧", f"{fw}×{fh} 指纹={H7.frames_digest([frame])}")

    geo = APP.primaryScreen().geometry()
    mask = frame[..., 3] > FP.ALPHA_MIN
    ys, xs = np.nonzero(mask)
    bx0, bx1 = int(xs.min()), int(xs.max()) + 1
    by0, by1 = int(ys.min()), int(ys.max()) + 1
    px = int(round((geo.width() - (bx1 - bx0)) / 2.0)) - bx0
    py = int(round((geo.height() - (by1 - by0)) / 2.0)) - by0
    base = (px, py)
    FACT("基准位", f"请求 ({px},{py})　不透明 bbox=({bx0},{by0})-({bx1},{by1})")

    host = H1.HostWindow()
    host.show()
    H4.pump(APP, 500)
    backend = H1.FACADE.get_backend()
    qf = H1.qimage_from(frame)
    backend.enable(host, fw, fh, px, py)

    def place(x: int, y: int) -> None:
        backend.set_position(x, y)
        H4.pump(APP, 400)
        backend.update_pixels(qf)       # 冻在帧 0：只谈几何，不谈姿态
        H4.pump(APP, 400)

    H4.pump(APP, 1200)
    backend.update_pixels(qf)
    H4.pump(APP, 1400)

    box, off = H12.probe_box(frame)
    if box is None:
        print("VERDICT-LOOP : ✗ 选不出独立量具盒 ⇒ 量具自身失效，不猜")
        backend.destroy_context()
        return 1

    def truth(tag: str):
        shot = A10.capture(tag)
        if shot is None:
            return None
        peak, x, y = A10.Correlator(H12.luma(shot)).peak(H12.luma(box))
        edge = box.shape[0]
        return (float(x + edge / 2.0 - off[0]), float(y + edge / 2.0 - off[1]), float(peak))

    t = truth("h13_gate")
    if t is None or t[2] < H12.PRESENT_PEAK:
        print(f"VERDICT-VISIBLE : ✗ 独立尺子峰 {'-' if t is None else f'{t[2]:.4f}'}"
              f" < {H12.PRESENT_PEAK} ⇒ 我方内容不在屏上，本件不产出读数")
        backend.destroy_context()
        return 1
    FACT("在位闸", f"独立盒真值 surface 中心 ({t[0]:.2f},{t[1]:.2f}) 峰={t[2]:.4f}"
                   "（grim＋宿主 NCC，不经 fidus）")

    engine = FP.FidusEngine()
    t0 = time.perf_counter()
    engine.calibrate()
    FACT("校准", f"{engine.calibrated_ms:.0f} ms（本件一次性付出）")

    cands = FP.iter_candidates(frame)
    FACT("候选数", f"{len(cands)} 只（产品枚举：每档取距质心最近的 3 只）")
    FACT("读法", f"移动后**连读 {READS_AFTER_MOVE} 发**并留轨迹——"
         "首跑只读 1 发，那正是 §12p.4 记过的「位移后前几读是阻尼收敛瞬态」。"
         "本件要的是**第几发收敛**与**收敛后误差**，不是第一发。")
    print(f"\n=== 逐候选闭环｜读数口径 = `estimate()` 的 (x,y)；"
          f"真值口径 = 独立尺子的 surface 中心 + 该候选锚点 ===")
    print(f"{'贴片':>6} {'锚点':>16} {'ceil':>7} {'首读−真':>13} {'实测位移':>12} "
          f"{'稳态fidus位移':>15} {'收敛@':>6} {'稳读−真':>13} {'每发ms':>9} {'闭环':>5}")
    rows = []
    registered: list[tuple] = []
    for cand in cands:
        place(*base)
        ta = truth("h13_a")
        if ta is None or ta[2] < H12.PRESENT_PEAK:
            print(f"{cand.edge:>5}²  ✗ 移动前独立尺子看不见 ⇒ 该轮作废（不猜）")
            continue
        believed = (base[0] + fw / 2.0, base[1] + fh / 2.0)
        try:
            ceiling = engine.register(cand.box, cand.belief_for(believed))
            r0 = engine.estimate()
        except FP.EngineError as exc:
            print(f"{cand.edge:>5}²  ✗ {exc}")
            continue
        expect0 = (ta[0] + cand.anchor[0], ta[1] + cand.anchor[1])
        err0 = (r0[0] - expect0[0], r0[1] - expect0[1])
        registered.append((cand, ceiling, expect0))
        place(base[0] + int(MOVE_PX), base[1])
        tb = truth("h13_b")
        if tb is None or tb[2] < H12.PRESENT_PEAK:
            print(f"{cand.edge:>5}²  ✗ 移动后独立尺子看不见 ⇒ 该轮作废（不猜）")
            continue
        expect1 = (tb[0] + cand.anchor[0], tb[1] + cand.anchor[1])
        trail = []
        ms = []
        for _ in range(READS_AFTER_MOVE):
            t_read = time.perf_counter()
            try:
                rn = engine.estimate()
            except FP.EngineError as exc:
                print(f"{cand.edge:>5}²  ✗ 移动后第 {len(trail) + 1} 发 estimate 抛 {exc}")
                break
            ms.append((time.perf_counter() - t_read) * 1e3)
            trail.append(rn)
        if len(trail) < SETTLE:
            continue
        moved = (tb[0] - ta[0], tb[1] - ta[1])
        r1 = trail[-SETTLE]                    # 稳态取"末 SETTLE 发里最靠后但已过瞬态"的那一发
        conv = next((i + 1 for i, rn in enumerate(trail)
                     if max(abs(rn[0] - expect1[0]), abs(rn[1] - expect1[1])) <= CONVERGED_PX),
                    None)
        fdelta = (r1[0] - r0[0], r1[1] - r0[1])
        err1 = (r1[0] - expect1[0], r1[1] - expect1[1])
        ok = abs(fdelta[0] - MOVE_PX) <= FP.TOL_PX and abs(fdelta[1]) <= FP.TOL_PX
        rows.append({"edge": cand.edge, "anchor": cand.anchor, "ceiling": ceiling,
                     "err0": err0, "moved": moved, "fdelta": fdelta, "err1": err1,
                     "ok": ok, "conv": conv, "ms": ms, "trail": trail})
        traj = " ".join(f"{rn[0] - expect1[0]:+.1f}" for rn in trail)
        print(f"{cand.edge:>5}² {str(tuple(round(v, 1) for v in cand.anchor)):>16} "
              f"{ceiling:>7.4f} ({err0[0]:+6.2f},{err0[1]:+5.2f}) "
              f"({moved[0]:+6.2f},{moved[1]:+5.2f}) "
              f"({fdelta[0]:+8.2f},{fdelta[1]:+5.2f}) "
              f"{(f'@{conv}' if conv else '未'):>6} ({err1[0]:+6.2f},{err1[1]:+5.2f}) "
              f"{sum(ms) / len(ms):>7.0f} {'过' if ok else '✗':>4}")
        print(f"       轨迹（每发离真值的 dx）：{traj}")

    place(*base)

    print("\n=== B · 先验回声判别：喂**故意偏 −32 px** 的信念，看首条读数跟先验还是跟真值 ===")
    print("     A 相里信念恰好为真 ⇒ 首读等于先验与首读命中真值**不可区分**。本相把先验偏开，"
          "专门分这一刀。")
    FACT("口径", "「离先验」≈0 ⇒ 引擎只是回声（没搜）；「离真值」≈0 ⇒ 它在窗内搜到了真峰")
    echo_rows = []
    for cand, ceiling, _old in sorted(registered, key=lambda r: -r[1])[:3]:
        ta = truth("h13_echo")
        if ta is None or ta[2] < H12.PRESENT_PEAK:
            print(f"{cand.edge:>5}²  ✗ 独立尺子看不见 ⇒ 该轮作废（不猜）")
            continue
        expect0 = (ta[0] + cand.anchor[0], ta[1] + cand.anchor[1])   # 现量，不沿用上相的真值
        prior = (expect0[0] - 32.0, expect0[1])
        try:
            engine.register(cand.box, prior)
            r = engine.estimate()
        except FP.EngineError as exc:
            print(f"{cand.edge:>5}²  ✗ {exc}")
            continue
        echo_rows.append({"edge": cand.edge, "prior": prior, "center": r,
                          "d_prior": (r[0] - prior[0], r[1] - prior[1]),
                          "d_truth": (r[0] - expect0[0], r[1] - expect0[1])})
        print(f"{cand.edge:>5}² ceiling={ceiling:.6f} 首读 ({r[0]:.2f},{r[1]:.2f})　"
              f"离先验 ({r[0] - prior[0]:+7.2f},{r[1] - prior[1]:+6.2f})　"
              f"离真值 ({r[0] - expect0[0]:+7.2f},{r[1] - expect0[1]:+6.2f})")

    print("\n=== C · 同位连读：不动它、连读 8 发 ⇒ 分离「读数噪声」与「每发成本」 ===")
    print("     A 相的每发 ms 是**移动之后**量到的，那里混着收敛瞬态；产品真正要报的"
          "「切换那一发要等多久」= 注册 + 首条 + 若干发稳态读，稳态那一发的耗时得单独量。")
    if registered:
        cand_c, ceil_c, expect_c = max(registered, key=lambda r: r[1])
        try:
            engine.register(cand_c.box, cand_c.belief_for(
                (base[0] + fw / 2.0, base[1] + fh / 2.0)))
        except FP.EngineError as exc:
            print(f"  ✗ C 相重注册被拒：{exc} ⇒ 本相不产出数字")
        else:
            ms_c, dx_c = [], []
            for _ in range(READS_AFTER_MOVE):
                t_read = time.perf_counter()
                try:
                    rc = engine.estimate()
                except FP.EngineError as exc:
                    print(f"  ✗ C 相 estimate 抛 {exc} ⇒ 本相停在第 {len(ms_c) + 1} 发")
                    break
                ms_c.append((time.perf_counter() - t_read) * 1e3)
                dx_c.append(rc[0] - expect_c[0])
            if ms_c:
                print(f"  {cand_c.edge}² ceiling={ceil_c:.6f}　每发 ms="
                      f"{[round(v) for v in ms_c]}")
                print(f"  离真值 dx={[round(v, 2) for v in dx_c]}　"
                      f"极差 {max(dx_c) - min(dx_c):.2f} px")
                FACT("稳态一发", f"中位 {sorted(ms_c)[len(ms_c) // 2]:.0f} ms／"
                     f"读数抖动 {max(dx_c) - min(dx_c):.2f} px（同位不动）")

    print("\n=== D · 两次冷获取：移动后**重注册**（先验跟着请求走），只取注册后首读 ===")
    print("     A 相的轨迹说明：**更新路径**（移动后连读）收敛到的不是真位，而是与贴片相关的"
          "固定偏置（本轮 96² 单调爬到 +11 仍未停）。而 B 相证明**冷获取**在先验偏 32 px 时"
          "一发就命中真值。若 D 相成立，产品闭环的两端都该走冷获取：值取自冷读，"
          "第二发只当证伪。")
    d_rows = []
    for cand in cands:
        place(*base)
        ta = truth("h13_d0")
        if ta is None or ta[2] < H12.PRESENT_PEAK:
            print(f"{cand.edge:>5}²  ✗ D 相移动前尺子看不见 ⇒ 作废（不猜）")
            continue
        e0 = (ta[0] + cand.anchor[0], ta[1] + cand.anchor[1])
        believed = (base[0] + fw / 2.0, base[1] + fh / 2.0)
        try:
            engine.register(cand.box, cand.belief_for(believed))
            c0 = engine.estimate()
        except FP.EngineError as exc:
            print(f"{cand.edge:>5}²  ✗ D 相首注册 {exc}")
            continue
        place(base[0] + int(MOVE_PX), base[1])
        tb = truth("h13_d1")
        if tb is None or tb[2] < H12.PRESENT_PEAK:
            print(f"{cand.edge:>5}²  ✗ D 相移动后尺子看不见 ⇒ 作废（不猜）")
            continue
        e1 = (tb[0] + cand.anchor[0], tb[1] + cand.anchor[1])
        try:
            engine.register(cand.box, cand.belief_for((believed[0] + MOVE_PX, believed[1])))
            c1 = engine.estimate()
        except FP.EngineError as exc:
            print(f"{cand.edge:>5}²  ✗ D 相重注册/读 {exc}")
            continue
        d0 = (c0[0] - e0[0], c0[1] - e0[1])
        d1 = (c1[0] - e1[0], c1[1] - e1[1])
        cd = (c1[0] - c0[0], c1[1] - c0[1])
        ok2 = abs(cd[0] - MOVE_PX) <= FP.TOL_PX and abs(cd[1]) <= FP.TOL_PX
        tight = max(abs(d0[0]), abs(d0[1]), abs(d1[0]), abs(d1[1]))
        d_rows.append({"edge": cand.edge, "anchor": cand.anchor, "d0": d0, "d1": d1,
                       "cd": cd, "ok": ok2, "tight": tight})
        print(f"{cand.edge:>5}² {str(tuple(round(v, 1) for v in cand.anchor)):>16} "
              f"冷读1 ({d0[0]:+6.2f},{d0[1]:+5.2f}) 冷读2 ({d1[0]:+6.2f},{d1[1]:+5.2f}) "
              f"冷读差 ({cd[0]:+6.2f},{cd[1]:+5.2f}) 最坏 {tight:5.2f} px "
              f"{'过' if ok2 else '✗':>4}")

    place(*base)
    backend.destroy_context()
    H4.pump(APP, 600)

    print("\n=== 汇总 ===")
    FACT("全程", f"{time.perf_counter() - t0:.1f} s／{len(rows)} 轮有效（一次性校准已付）")
    passing = [r for r in rows if r["ok"]]
    if not rows:
        print("VERDICT-LOOP : ✗ 无有效轮 ⇒ 量具没跑到出口，本件不产出任何主张")
        return 1
    real_move = [r for r in rows if abs(r["moved"][0] - MOVE_PX) <= 2.0]
    if not real_move:
        print(f"VERDICT-LOOP : ✗ 实测位移一列没有任何一轮 ≈ +{MOVE_PX:.0f} px"
              " ⇒ **第 3 类**：surface 根本没被摆过去，fidus 的读数未必是错的。"
              "本件的闭环判据（对照 fidus 自己的读数差）在此几何下无意义，先修摆位。")
        return 0
    tail = "第 3 类被排除：surface 确实走了 +48 px" if len(real_move) == len(rows) \
        else "部分轮次没到位"
    print(f"VERDICT-SURFACE-MOVE : 实测到位 {len(real_move)}/{len(rows)} 轮（{tail}）")
    convs = [r["conv"] for r in rows if r["conv"]]
    dxs = [r["err1"][0] for r in rows if r["conv"]]
    if convs:
        print(f"VERDICT-CONVERGE : {len(convs)}/{len(rows)} 轮在 {READS_AFTER_MOVE} 发内收敛"
              f"（≤{CONVERGED_PX} px）；收敛发数 {sorted(set(convs))}；"
              f"稳读 dx 区间 [{min(dxs):+.2f},{max(dxs):+.2f}] px")
    else:
        print(f"VERDICT-CONVERGE : 0/{len(rows)} 轮收敛——连读 {READS_AFTER_MOVE} 发内"
              f"没有任何一发离真值 ≤{CONVERGED_PX} px")
    if passing:
        best = min(passing, key=lambda r: max(abs(r["err1"][0]), abs(r["err1"][1])))
        print(f"VERDICT-LOOP : ✓ {len(passing)}/{len(rows)} 轮闭环通过（稳态读法，末 "
              f"{SETTLE} 发里取一发）；最好一轮 {best['edge']}² "
              f"ceiling={best['ceiling']:.6f} 稳读误差 "
              f"({best['err1'][0]:+.2f},{best['err1'][1]:+.2f}) px、第 {best['conv']} 发收敛"
              "⇒ 产品判据该读**稳态**那发，不是移动后第一发")
    else:
        print(f"VERDICT-LOOP : ✗ {len(rows)} 轮全不通过（surface 实测确实走了 "
              f"{MOVE_PX:.0f} px，且连读 {READS_AFTER_MOVE} 发）⇒ 不是瞬态窗宽问题，"
              "引擎在**更新**那一发就是不跟")
    print(f"VERDICT-CEILING : 通过轮 ceiling "
          f"{sorted({round(r['ceiling'], 4) for r in passing}) or '（无）'}　"
          f"全部轮 {sorted({round(r['ceiling'], 4) for r in rows})}")
    if echo_rows:
        near_prior = [r for r in echo_rows
                      if max(abs(r["d_prior"][0]), abs(r["d_prior"][1])) <= 2.0]
        near_truth = [r for r in echo_rows
                      if max(abs(r["d_truth"][0]), abs(r["d_truth"][1])) <= 8.0]
        kind = ("全部是回声 ⇒ 第 2 类坐实"
                if len(near_prior) == len(echo_rows) else "有发离开先验去搜到了真值")
        print(f"VERDICT-ECHO : 偏先验 {len(echo_rows)} 发里，落在先验 ±2 px 的 "
              f"{len(near_prior)} 发、落在真值 ±8 px 的 {len(near_truth)} 发｜{kind}")
    if d_rows:
        dp = [r for r in d_rows if r["ok"]]
        worst = max(r["tight"] for r in d_rows)
        best = min(r["tight"] for r in d_rows)
        print(f"VERDICT-COLD-PAIR : {len(dp)}/{len(d_rows)} 轮两次冷读闭环通过；"
              f"冷读离真值最坏 {worst:.2f} px、最好 {best:.2f} px"
              + ("　⇒ 闭环两端都走冷获取（重注册）成立，产品不必碰更新路径"
                 if len(dp) == len(d_rows) and worst <= CONVERGED_PX else
                 "　⇒ 冷读并非每发都落在真值，还需逐贴片区划"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
