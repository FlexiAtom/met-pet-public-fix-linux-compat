#!/usr/bin/env python3
"""H8 · 新 ROI 的**屏上**首锁与位移跟随（把 H7 的 CPU 结论拿到真机上判）

H7 在纯 CPU 上量到：判据化选框能把 `confidence_ceiling` 从"6/6 全等地板"抬到 ~0.08，
收益**全部来自 argmin 搜索**（缩尺寸、进轮廓各买不到 0.002），且**固定几何盒**（选定一次后
逐帧重切同一坐标）只让出 ~0.005–0.013 的 s ⇒ 产品口径应为"注册时搜一次、冻结盒 + 冻结偏移"。
但 H7 自己在 `VERDICT-NOTTESTED` 里写明了它答不了的三件：**屏上首锁、空桌面误锁、位移后跟随**。
本探针把三件全接下来：首锁 + 跟随走 C/D/E 相，误锁走 F 相（撤场后先用**整窗内容的 NCC 峰**证明
浮层真已消失，再问"空桌面上还有没有过 0.35 那条线的替身"，并让新 ROI 与现状 patch160 在
**同一张抓屏**上对表——加窗口不是中立操作（诱饵窗事故），所以这一相刻意不加任何窗口）。

为什么走**门面**而不是产品的 Live2D 窗口
--------------------------------------
niri 下 Qt `move()` 被忽略（本轮之前实测：请求 (480,300) 被摆在屏幕正中），拿它当"真位移"
会造出一条假读数；H5 的 C3 臂因此改用 `wayland_layer` 门面的 `set_position`。这不只是绕坑——
门面就是**产品自己在 Wayland 上的渲染路径**，所以这条路线同时把"产品像素经产品路径上屏"
纳进了被测面。代价：屏上是**提交的那一帧**（动画冻结），不是活体 Live2D ⇒ 本探针量不到
"动画带来的窗内位移"，那一半由 H7 的 `RIGID`（≤3 px）负责，两边不互相顶替。

真值口径
--------
每读一次 `estimate()` 就 `grim` 抓一次屏、用宿主侧 NCC 找**同一块 ROI 模板**的真实中心
（绝对基准，不经 fidus）。位移相的"真位移"由**两相各自实测的中心差**给出，不采信请求位移。
存在性闸门先行：任何一次读表之前，如果独立尺子在屏幕上找不到这块 ROI，**直接退出**，
不把失败路径的墙钟当耗时上报（§12g-4 那条诱饵窗事故的教训一般化）。

用法
----
    QT_QPA_PLATFORM=wayland .venv/bin/python \\
        scripts/fidus_positioning_probe/probe_h8_roi_on_screen.py
    # 默认吃 H7 落盘的那批帧（指纹可对照）：--batch /tmp/h7_frames
副作用：真实桌面上**挂一只 layer 浮层**（内容是产品帧，461×614），若干次 `grim` 整屏抓取，
`calibrate_once` 会在屏上投标定标记。退出即 `destroy_context()`。
退出码：0 = 测完（数字再难看也是 0）；1 = 量具/环境不可用（含存在性闸门不过）。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fidus  # noqa: E402
import probe_a10_geometry as A10  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h6_dense_gate as H6  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

PX = 1.0            # 独立尺子的判读精度量级（NCC 峰是整像素）
MOVE_SETTLE = 5     # 位移相的瞬态窗宽（本轮实测约 4 读才收住，取 5 留一格）
LOCK_ASK = 0.35     # fidus `template.rs:22` 注释自述 "~0.35 is a usable measurement"
                    # —— 与 H3 用的是同一条线，换 ROI 后是否越过它才是误锁一问的判据


def fact(label, value) -> None:
    H6.fact(label, value)


def _f(v) -> str:
    return H6._f(v)


def luma_of(arr: np.ndarray) -> np.ndarray:
    return H7.H3.luma709(arr[..., :3]) if arr.ndim == 3 else arr


def truth_of(roi: np.ndarray, tag: str) -> tuple[float, float, float] | None:
    """grim 抓整屏 + 宿主 NCC ⇒ ROI 在**屏幕上的真实中心**与峰值（不经 fidus）。"""
    shot = A10.capture(tag)
    if shot is None:
        return None
    peak, x, y = A10.Correlator(luma_of(shot)).peak(luma_of(roi))
    if peak < 0.5:
        return None
    h, w = roi.shape[:2]
    return x + w / 2.0, y + h / 2.0, float(peak)


def gate_or_abort(roi: np.ndarray, backend, frame: np.ndarray, tries: int = 4, feed=None):
    """存在性闸门：屏上找不到 ROI 就不产出任何读数（门面首帧缺陷：未见就再提交一帧）。"""
    qf = H1.qimage_from(frame)
    for i in range(tries):
        if feed is not None:
            feed()
            H4.pump(H8_APP, 120)
        t = truth_of(roi, f"h8_gate{i}")
        if t is not None:
            fact("存在性闸门", f"第 {i + 1} 次抓到 ⇒ 真中心=({t[0]:.2f},{t[1]:.2f}) "
                              f"峰值={t[2]:.4f}")
            return t
        fact("可见性", f"第 {i + 1}/{tries} 次未见 ⇒ 再提交一帧")
        if feed is None:
            backend.update_pixels(qf)
        H4.pump(H8_APP, 1200)
    return None


def read_phase(label: str, eng, roi: np.ndarray, n: int, settle: int, feed=None) -> list[dict]:
    rows: list[dict] = []
    for k in range(n):
        if feed is not None:
            feed()                       # 活体相：先把动画的**当前**帧推上屏，再读
            H4.pump(H8_APP, 80)
        try:
            x, y, c = eng.estimate()
        except BaseException as exc:  # noqa: BLE001
            fact(f"  {label} #{k}", f"estimate 抛 {type(exc).__name__}")
            rows.append({"k": k, "err": type(exc).__name__})
            H4.pump(H8_APP, 240)
            continue
        t = truth_of(roi, f"h8_{label}{k}")
        if t is None:
            fact(f"  {label} #{k}", "独立尺子未见 ⇒ 该读数不进入统计")
            rows.append({"k": k, "err": "no-truth"})
            H4.pump(H8_APP, 240)
            continue
        dx, dy = float(x) - t[0], float(y) - t[1]
        rows.append({"k": k, "x": float(x), "y": float(y), "conf": float(c),
                     "tx": t[0], "ty": t[1], "dx": dx, "dy": dy, "peak": t[2],
                     "settled": k >= settle})
        fact(f"  {label} #{k}", f"读数=({x:.2f},{y:.2f}) 真=({t[0]:.2f},{t[1]:.2f}) "
                              f"⇒ 误差 ({dx:+.2f},{dy:+.2f}) px conf={_f(float(c))} "
                              f"尺峰={t[2]:.4f}")
        H4.pump(H8_APP, 240)
    return rows


def stats(rows: list[dict], only_settled: bool = True) -> dict:
    use = [r for r in rows if "dx" in r and (r["settled"] if only_settled else True)]
    if not use:
        return {"n": 0}
    dxs = [r["dx"] for r in use]
    dys = [r["dy"] for r in use]
    return {"n": len(use), "dx_mean": float(np.mean(dxs)), "dy_mean": float(np.mean(dys)),
            "absmax": float(max(abs(min(dxs)), abs(max(dxs)), abs(min(dys)), abs(max(dys)))),
            "conf": [r["conf"] for r in use],
            "peak_lo": min(r["peak"] for r in use),
            "peak_hi": max(r["peak"] for r in use)}


H8_APP: QApplication | None = None


def main() -> int:
    global H8_APP
    ap = argparse.ArgumentParser(description="H8: 新 ROI 的屏上首锁与位移跟随")
    ap.add_argument("--batch", default="/tmp/h7_frames", help="H7 落盘的帧目录")
    ap.add_argument("--est", type=int, default=8, help="每相 estimate 次数")
    ap.add_argument("--shift", default="80,60", help="位移请求 dx,dy（**不采信**，仅请求）")
    ap.add_argument("--settle", type=int, default=2, help="前几读算瞬态、不入稳态统计")
    ap.add_argument("--live", action="store_true",
                    help="改挂**活体 Live2D**（产品 widget 自带定时器驱动，逐读推新帧）"
                         "，而非 H7 落盘的冻结帧")
    args = ap.parse_args()

    idy = H7.arm_identity()
    if args.live:
        # 与 H3 同一前置：QApplication **之前**请求 alpha+stencil，否则 QOpenGLWidget 拿不到
        # Live2D 遮罩缓冲需要的 stencil ⇒ 量的就不是产品那条渲染路径。
        from PyQt5.QtGui import QSurfaceFormat  # noqa: PLC0415
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        fmt.setStencilBufferSize(8)
        fmt.setRenderableType(QSurfaceFormat.OpenGL)
        QSurfaceFormat.setDefaultFormat(fmt)
        frames, rinfo = [], {}
    else:
        frames = H7.load_frames(Path(args.batch))
        rinfo = {}
    if not args.live and not frames:
        print(f"✗ 读不到 {args.batch}（先跑 H7 `--dump`；本探针刻意与 CPU 普查**同批**）")
        return 1
    if not idy["anchor_ok"] or not idy["so_ok"]:
        fact("⚠ 锚点与登记不符", "下面的数字仍会打印，但只能按**本轮实际锚点**读")

    H8_APP = QApplication(sys.argv)
    eng = fidus.Fidus.build_wayland()
    if args.live:
        frames, rinfo, l_host, l_widget = H7.H3.render_live2d_frames(H8_APP, 1)
        if not frames:
            print("✗ 活体渲染不可用（模型目录 / `_ready`）⇒ 量具自身失效，不猜")
            return 1
        # Qt 宿主窗口本身是**第二张**同名内容表面：留着它，独立尺子和引擎都会看到两只宠物，
        # 找到的中心就不再是我们挂的那一张。产品的穿透模式也是这么做的（hide 顶层窗口、
        # 靠 render_offscreen 主动取帧），所以这里跟着产品走。
        l_host.hide()
        H4.pump(H8_APP, 400)
        g1 = rinfo["grab"]()
        H4.pump(H8_APP, 200)
        g2 = rinfo["grab"]()
        moving = g1 is not None and g2 is not None and not np.array_equal(g1, g2)
        fact("隐藏 Qt 宿主后仍在推进动画",
             "是（连续两次 `render_offscreen` 像素不同）" if moving else
             "✗ 否 ⇒ 隐藏即停摆，活体相会量到一张假冻结帧，不跑")
        if not moving:
            print("\n[VERDICT]\nVERDICT-LIVE    : ✗ 活体不可得（隐藏后不再推进）⇒ 本相不产出数字")
            return 1
        fact("活体首帧", f"{frames[0].shape[1]}×{frames[0].shape[0]} "
             f"指纹={H7.frames_digest(frames)}　⇒ 该指纹**跨进程不可复现**；选框与模板一律锚在"
             "这一帧上，后面每读推的是**动画的下一帧**（不是这一帧）")
    else:
        fact("批次", f"{len(frames)} 帧 {frames[0].shape[1]}×{frames[0].shape[0]} "
                     f"指纹={H7.frames_digest(frames)}")

    print("\n=== A · CPU 侧选框（与 H7 同一个规则、同一批帧）===")
    cands = H7.candidates(frames[0])
    review = H7.score_exact(eng, cands)
    pick = H7.pick_by_rule(review)
    if pick is None:
        print("✗ 帧 0 选不出可行 ROI ⇒ 无从上屏")
        return 1
    roi = pick["box"]
    fact("选中", f"{pick['size']}²@({pick['x0']},{pick['y0']}) "
               f"s_eng={_f(pick['s_eng'])} ceiling={_f(pick['engine_cap'])} "
               f"锚点常量=({pick['off'][0]:+.1f},{pick['off'][1]:+.1f}) px "
               f"复刻对表={'逐位等' if pick['eq_replica'] else '✗ 不等'}")
    sha = hashlib.sha256(roi.tobytes()).hexdigest()[:16]
    fact("模板指纹", f"{sha}（要确认屏上锁的就是这一块，比对它而不是文件名）")

    fh, fw = frames[0].shape[:2]
    px, py = 260, 90
    dx_req, dy_req = (int(v) for v in args.shift.split(","))

    print("\n=== B · 上屏（产品门面：layer 浮层 + 产品帧像素）===")
    host = H1.HostWindow()
    host.show()
    H4.pump(H8_APP, 500)
    backend = H1.FACADE.get_backend()
    feed = None
    if args.live:
        def feed() -> None:                  # 一次"新帧上屏"＝产品 60 fps 里的一格
            fr = rinfo["grab"]()
            if fr is not None:
                backend.update_pixels(H1.qimage_from(fr))
    backend.enable(host, fw, fh, px, py)
    backend.update_pixels(H1.qimage_from(frames[0]))
    H4.pump(H8_APP, 1400)
    t0 = gate_or_abort(roi, backend, frames[0], feed=feed)
    if t0 is None:
        print("\n[VERDICT]\nVERDICT-VISIBLE : ✗ 屏上找不到该 ROI ⇒ 本探针不产出任何读数"
              "（不把失败路径的墙钟当耗时）")
        backend.destroy_context()
        return 1

    cap, err, ms = H7.engine_read(eng, roi)
    if cap is None:
        print(f"\n[VERDICT]\nVERDICT-REGISTER: ✗ 引擎拒注册（{err}）⇒ 不读陈旧 ceiling、不 estimate")
        backend.destroy_context()
        return 1
    fact("注册", f"ceiling={_f(cap)}（{ms:.1f} ms）")
    try:
        cal = eng.calibrate_once()
        fact("calibrate_once", f"scale={getattr(cal, 'scale', '?')}　⇒ 本机单输出，"
             "读数与真值同为物理像素当且仅当 scale=1")
    except BaseException as exc:  # noqa: BLE001
        fact("calibrate_once", f"RAISED ⇒ {type(exc).__name__} ⇒ 无法进入 estimate 相")
        backend.destroy_context()
        return 1

    print(f"\n=== C · 首相·静止（{args.est} 读，每读一次独立抓屏）===")
    rows_a = read_phase("A", eng, roi, args.est, args.settle, feed)
    tA = [r for r in rows_a if "tx" in r]

    print(f"\n=== D · 位移相（请求 +({dx_req},{dy_req}) px，真位移以实测为准）===")
    backend.set_position(px + dx_req, py + dy_req)
    H4.pump(H8_APP, 1200)
    tB0 = gate_or_abort(roi, backend, frames[0])
    rows_b = [] if tB0 is None else read_phase("B", eng, roi, max(args.est, 8), MOVE_SETTLE, feed)
    tB = [r for r in rows_b if "tx" in r]

    print("\n=== E · 归相位（移回原处，看要不要重注册才回得来）===")
    print("     瞬态说明：位移后前几读是**阻尼收敛瞬态**（本轮实测约 4 读），"
          "故本相取 8 读、settle=5，否则报出来的是量具的窗宽而不是产品的偏。")
    if tB0 is not None:
        backend.set_position(px, py)
        H4.pump(H8_APP, 1200)
    rows_c = [] if tB0 is None else read_phase("C", eng, roi, 8, MOVE_SETTLE, feed)

    print("\n=== F · 撤场：空桌面上会不会误锁（新 ROI 与现状基线在**同一张**背景上对表）===")
    backend.destroy_context()
    H4.pump(H8_APP, 1200)
    bg = A10.capture("h8_bg")
    if bg is None:
        fact("空背景", "grim 未出图 ⇒ 本相不可判，不猜")
        corr_bg, present = None, None
    else:
        corr_bg = A10.Correlator(luma_of(bg))
        whole = corr_bg.peak(luma_of(frames[0]))
        cx_w = whole[1] + frames[0].shape[1] / 2.0
        cy_w = whole[2] + frames[0].shape[0] / 2.0
        old = (tA[0]["tx"], tA[0]["ty"]) if tA else (float("nan"), float("nan"))
        present = float(whole[0])
        fact("撤场证明", f"整窗内容在空背景上的 NCC 峰={present:.4f}"
             f"@({cx_w:.0f},{cy_w:.0f})（在场时该尺子读到过 1.0000）⇒ "
             + ("浮层确已消失，下面的 conf 才谈得上「没东西可锁」"
                if present < 0.5 else
                f"✗ 峰仍在（离旧位 "
                f"{((cx_w - old[0]) ** 2 + (cy_w - old[1]) ** 2) ** 0.5:.0f} px）"
                " ⇒ 没撤干净，本相读数一律作废"))
    bg_peak: dict[str, tuple[float, float, float]] = {}
    if corr_bg is not None and present is not None and present < 0.5:
        base_tpl = H7.H3.make_feed(frames[0], None, "patch")
        for name, tpl in (("新 ROI", roi), ("现状 patch160", base_tpl)):
            pk, bx, by = corr_bg.peak(luma_of(tpl))
            bg_peak[name] = (float(pk), bx + tpl.shape[1] / 2.0, by + tpl.shape[0] / 2.0)
            fact(f"空桌面替身 · {name}", f"最强 NCC 峰={pk:+.4f}"
                 f"@({bg_peak[name][1]:.0f},{bg_peak[name][2]:.0f})　"
                 f"（fidus 自述可用线 {LOCK_ASK} ⇒ "
                 + ("够不到 ⇒ 背景上没有能被它当目标的替身" if pk < LOCK_ASK
                    else "✗ 够得到 ⇒ 存在可被锁定的替身") + "）")
        fact("背景内容记账", f"这一相只对**当前桌面上恰好摆着的东西**成立（壁纸 + 现有窗口），"
             "换一套窗口组合需重测")
    empty = []
    for k in range(8):
        try:
            x, y, c = eng.estimate()
            empty.append((float(x), float(y), float(c)))
            fact(f"  撤场 #{k}", f"读数=({x:.2f},{y:.2f}) conf={_f(float(c))}")
        except BaseException as exc:  # noqa: BLE001
            empty.append(None)
            fact(f"  撤场 #{k}", f"抛 {type(exc).__name__}")
        H4.pump(H8_APP, 300)

    sa, sb, sc = stats(rows_a), stats(rows_b), stats(rows_c)
    print("\n[VERDICT]")
    print(f"VERDICT-BATCH   : 指纹={H7.frames_digest(frames)} 模板 sha={sha}"
          f" ceiling={_f(cap)} 锚点常量=({pick['off'][0]:+.1f},{pick['off'][1]:+.1f}) px")
    pkr = (f"｜独立尺子峰区间 [{sa['peak_lo']:.3f},{sa['peak_hi']:.3f}]" if sa.get("n") else "")
    ok_a = bool(sa.get("n")) and sa["absmax"] <= 3.0 * PX
    if args.live:
        tail_a = ("窗口这一相**没有**被移动 ⇒ 该 |max| 就是**活体动画**加在位置通道上的抖动"
                  "（与 H7 离线 RIGID ≤3 px 同量级 ⇒ 两条独立路径互相印证）" if ok_a else
                  "✗ 首相就有 >3 px 的读动：活体动画抖动或引擎误差，逐读见 C 表")
    else:
        tail_a = ("首锁命中真位（误差在独立尺子的整像素量级内）" if ok_a else
                  "✗ 有读数偏离真位 >3 px，见 C 表明细")
    print(f"VERDICT-LOCK    : 首相稳态 {sa.get('n', 0)} 读，平均误差 "
          f"({sa.get('dx_mean', float('nan')):+.2f},{sa.get('dy_mean', float('nan')):+.2f}) px"
          f"｜最大 |{sa.get('absmax', float('nan')):.2f}| px{pkr} ⇒ {tail_a}")
    confs = [c for r in rows_a if "conf" in r for c in [r["conf"]]]
    over = [c for c in confs if c > cap + 1e-9]
    flat = bool(confs) and min(confs) == max(confs)
    print(f"VERDICT-CONF    : conf 观测 {['%.6f' % c for c in confs]}"
          f"｜注册期 ceiling={_f(cap)}｜越界 {len(over)} 读 ⇒ "
          + ("conf **逐读恒等于注册期 ceiling**（advertised==applied 的已知行为）⇒ 改 ROI 抬高的是"
             "「模板级上界」0.05→0.086，运行期 conf **不携带新信息**；产品侧不能把 conf 当活体"
             "健康信号，只能当静态质量上界与钳位证据"
             if flat and not over else
             ("conf 随读数变化且全部 ≤ ceiling ⇒ 通道确有活体信息"
              if confs and not over else "✗ conf 越过 ceiling，见明细")))
    if tA and tB:
        sA = [r for r in tA if r.get("settled")] or tA
        sB = [r for r in tB if r.get("settled")] or tB
        d_est = (np.mean([r["x"] for r in sB]) - np.mean([r["x"] for r in sA]),
                 np.mean([r["y"] for r in sB]) - np.mean([r["y"] for r in sA]))
        d_all = (np.mean([r["x"] for r in tB]) - np.mean([r["x"] for r in tA]),
                 np.mean([r["y"] for r in tB]) - np.mean([r["y"] for r in tA]))
        d_true = (np.mean([r["tx"] for r in tB]) - np.mean([r["tx"] for r in tA]),
                  np.mean([r["ty"] for r in tB]) - np.mean([r["ty"] for r in tA]))
        resid = (d_est[0] - d_true[0], d_est[1] - d_true[1])
        print(f"VERDICT-FOLLOW  : 稳态读数差=({d_est[0]:+.2f},{d_est[1]:+.2f})"
              f"（含瞬态则 {d_all[0]:+.2f},{d_all[1]:+.2f}）"
              f"｜实测表面位移=({d_true[0]:+.2f},{d_true[1]:+.2f})（请求 "
              f"+({dx_req},{dy_req})）⇒ 残差 ({resid[0]:+.2f},{resid[1]:+.2f}) px "
              + ("⇒ 契约 v7 第 7 条成立（读数差 == 自测表面位移）"
                 if max(abs(resid[0]), abs(resid[1])) <= 3.0 * PX
                 else "✗ 不等 ⇒ 该条在产品 ROI 上不成立"))
        print(f"VERDICT-LOCK-B  : 位移相稳态误差 ({sb.get('dx_mean', float('nan')):+.2f},"
              f"{sb.get('dy_mean', float('nan')):+.2f}) px｜最大 "
              f"|{sb.get('absmax', float('nan')):.2f}| ⇒ "
              + ("位移后回到真位" if sb.get("n") and sb["absmax"] <= 3.0 * PX
                 else "✗ 位移后仍有偏（瞬态或跳峰），逐读见 D 表"))
        if sc.get("n"):
            print(f"VERDICT-RETURN  : 移回原相稳态误差 ({sc['dx_mean']:+.2f},{sc['dy_mean']:+.2f}) px"
                  f"｜最大 |{sc['absmax']:.2f}|（瞬态窗宽 {MOVE_SETTLE} 读已剔）⇒ "
                  + ("来回一趟**不需要重注册**就回得来" if sc["absmax"] <= 3.0 * PX
                     else "✗ 回相仍有偏，见 E 表"))
    else:
        print("VERDICT-FOLLOW  : 不可判（位移相存在性闸门或抓屏未过）")
    if tA:
        tl = (tA[0]["tx"] - pick["size"] / 2.0 - pick["x0"],
              tA[0]["ty"] - pick["size"] / 2.0 - pick["y0"])
        # 活体容差是**推出来的**：H7 离线 RIGID 的窗内位移上界 3 px ＋ 独立尺子整像素量化 1 px。
        tol = (3.0 + 1.0) * PX if args.live else 3.0 * PX
        gap = max(abs(tl[0] - px), abs(tl[1] - py))
        tail_h = (("该差 = 姿态造成的 ROI **窗内**位移（H7 离线 RIGID 同项）＋尺子整像素量化，"
                   "不是换算式的偏；本探针在**活体**下从未反推出过请求位 ⇒ 活体里不能用"
                   "「读数 − 常量」当窗口绝对位置，只能当**位移**用（冻结相才闭合到 0.00）"
                   if gap <= tol else
                   f"✗ 差 {gap:.2f} px 已超姿态＋量化上界 {tol:.0f} px ⇒ 不只是姿态项，查换算")
                  if args.live else
                  ("换算式在真像素上闭合（窗口位置可由 ROI 读数唯一确定）" if gap <= tol else
                   "✗ 不闭合：锚点常量或口径有问题"))
        print(f"VERDICT-ANCHOR  : 用「ROI 真中心 − 冻结常量」反推窗口左上角 "
              f"= ({tl[0]:.2f},{tl[1]:.2f})｜门面请求 = ({px},{py}) ⇒ 差 "
              f"({tl[0] - px:+.2f},{tl[1] - py:+.2f}) px｜容差 {tol:.0f} px ⇒ {tail_h}")
    alive_reads = [e for e in empty if e is not None]
    fired = [e for e in alive_reads if e[2] > 0.0]
    print(f"VERDICT-EMPTY   : 撤场后 {len(empty)} 读里抛异常 {len(empty) - len(alive_reads)} 次"
          f"｜conf>0 的读 {len(fired)} 次"
          + (f"，残留读数 {[(round(e[0], 1), round(e[1], 1)) for e in alive_reads[:3]]}…（conf 全 0，"
             "即「仍报坐标但不声称有把握」⇒ 产品侧**必须**按 conf==0 / 陈旧度丢弃，不能按异常丢弃）"
             if alive_reads and not fired else
             (f"，其中 {len(fired)} 读 conf>0 ⇒ ✗ 空桌面上引擎仍在**肯定地**锁东西"
              if fired else "　⇒ 目标消失即报错，未瞎报")))
    if bg_peak:
        new_pk, base_pk = bg_peak["新 ROI"][0], bg_peak["现状 patch160"][0]
        print(f"VERDICT-FALSELOCK: 空桌面上最强替身峰（宿主 NCC，同一张背景）新 ROI={new_pk:+.4f}"
              f"｜现状={base_pk:+.4f}｜fidus 自述可用线 {LOCK_ASK} ⇒ "
              + (f"两把都在线下（余量 {LOCK_ASK - new_pk:.3f} vs {LOCK_ASK - base_pk:.3f}）"
                 "⇒ 新 ROI 因更自相似而把替身峰**抬高**了，但**未越过**可锁线"
                 if max(new_pk, base_pk) < LOCK_ASK else
                 "✗ 至少一把过线 ⇒ 该模板在真桌面上存在可用替身，误锁风险为实"))
        fact("这一相的边界", "只否证/证实**当前这一张桌面**上的替身；换壁纸或换窗口组合"
             "须重跑 F 相（成本 = 一次抓屏）")
    else:
        print("VERDICT-FALSELOCK: 不可判（grim 未出图或撤场证明未过）")
    live_tag = "（活体）" if args.live else ""
    print(f"VERDICT-NOTTESTED{live_tag}: "
          + ("别的壁纸/窗口组合下的真误锁（本轮只否证了当前这一张）、多输出与分数缩放"
             if args.live else
             "活体动画下的窗内位移（H7 的 RIGID ≤3 px 是**离线**同批帧上的数，`--live` 才在屏上"
             "量它）、别的壁纸/窗口组合下的真误锁（本轮只否证了当前这一张）、多输出与分数缩放")
          + " ⇒ 本件不背")
    return 0


if __name__ == "__main__":
    sys.exit(main())
