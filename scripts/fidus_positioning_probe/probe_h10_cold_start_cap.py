#!/usr/bin/env python3
"""H10 · 冷启动整帧搜索窗**在夹紧之前**计价 ⇒ 大模板在中等输出上"注册成功、永远 TargetLost"

出处：fidus 入站件 `pool/fidus-timing-retraction-and-cold-start-cap.md` §3-B。
它的机理主张：`register_target` 显式置 `has_fix = false` ⇒ 首条 `estimate` 请求**整块输出**当搜索窗
（`half = max(输出边) + max(模板边)`），而 `MAX_SEARCH_POSITIONS = 16_000_000` 在**夹紧之前**计价
⇒ 位置数 `= (2·half + 1)²` 一旦越线，首帧必失败，且 `has_fix` 永不置真 ⇒ **每次调用同样失败**。

它给了一条**二值可砸**的预测（本机输出 1366×768 ⇒ 临界模板最长边 634）：
    640×640 目标 ⇒ 注册成功、首条 `estimate` **永远抛 `FidusTargetLost`**（哪怕目标完整在屏上）
    600×600 同素材 ⇒ 正常锁定
两臂同判即它的模型错。我方把临界挪到**刀刃上**再加两臂：633 允许（15,992,001 ≤ 上限）、
634 拒绝（16,008,001 > 上限）——只跑 640/600 只能证明"有大小的差别"，证不了那条不等式。

**为什么必须真上屏**：不挂内容时两臂都会抛 `FidusTargetLost`（没有 prior fix 本来就是这个异常），
那是一棵**假绿**树。所以每臂先过独立尺子的在位闸（grim + 宿主 NCC，峰值 ≥ `PRESENT_PEAK`），
再注册、再逐条读；"注册成功"与"内容确实在屏上"两件事各自有证据，剩下的失败才只可能来自几何。

三个出口，各自独立、不互相顶替：
* **CAPS**（主）—— 四臂（640／634／633／600）对贵方不等式的符合度，含"永抛"（连读 3 条全抛）。
* **CONTROL**（同屏换模板）—— 640² surface 上改用 96² 中心裁剪 ⇒ 预测**正常锁定**。
  它把"surface 太大／位置太差／素材不可匹配"与"模板最长边进不等式"分开；没有它，CAPS 的
  失败臂可以赖在别的变量上。（贵方 §6 反证表第一行是同一思路的 Rust 侧版本。）
* **HOST-IMPACT**（产品后果，算术而非推测）—— 由**已坐实**的不等式反解各常见输出下的临界模板边，
  给出"哪些输出上任何可用桌宠模板都会失败"。1920 那一格我方**本机测不到**（无高分屏），
  只能作为算术外推上报，不外推成实测。

用法
----
    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h10_cold_start_cap.py
    # 只复跑失败臂 + 对照（省桌面时间；surface 几何与全量跑逐位一致）：
    #   ... probe_h10_cold_start_cap.py --arms 640
副作用：真实桌面上挂/拆 layer 浮层（最大 640×640 不透明方块）、每臂多次整屏抓取、一次 `calibrate_once`。
退出码：0 = 各臂都跑到出口（结论再否定也是 0）；1 = 量具/环境不可用。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
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
import probe_h8_roi_on_screen as H8  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

SIZES = (640, 634, 633, 600)   # 贵方不等式的两翼：640/634 预测失败，633/600 预测锁定
# 400 不在刀刃上，是 fidus 出站请求（pid-1295942 (a)）要的一档二值预测：
# 贵方 work 公式（∝ 位置数 × 模板面积）宣称成本峰在 s≈400 ⇒ 400² 应是**允许臂里最慢的那一个**。
EXTRA_SIZES = (400,)
ALLOWED = SIZES + EXTRA_SIZES
CONTROL_EDGE = 96              # 同一只 640² surface 上的小模板对照
READS = 3                      # 失败臂连读几条：验"每次调用同样失败"，不是只验首条
PRESENT_PEAK = 0.9             # 在位线（与 H9 同口径；0.5 只能判"有没有可比的数"）
SEED = 1024                    # 素材固定：模板指纹随臂打印，跨轮可比
MAX_POSITIONS = 16_000_000     # 贵方给的常量，用于把实测臂与算术临界对表
# 宽 except 只准接引擎自己抛的异常。首版我方一行格式化代码抛 TypeError，被同一个 except
# 吞成"抛 TypeError"，三条允许臂当场判成"✗ 不一致"——量具故障与结论在输出里长得一模一样。
ENGINE_EXC = {n for n in dir(fidus) if n.startswith("Fidus") and n != "Fidus"} | {"PanicException"}
APP: QApplication | None = None


def fact(label, value) -> None:
    H6.fact(label, value)


def make_frame(edge: int) -> np.ndarray:
    """逐像素随机噪声 + 全不透明 alpha：非自相似（密铺门过得去）、且 alpha 恒 255
    ⇒ 独立尺子峰值掉到 1.0 以下只有"我方内容不在抓屏里"一种解释（H9 同论证）。"""
    rng = np.random.default_rng(SEED)
    arr = rng.integers(0, 256, size=(edge, edge, 3), dtype=np.uint16).astype(np.uint8)
    alpha = np.full((edge, edge, 1), 255, dtype=np.uint8)
    return np.concatenate([arr, alpha], axis=2)


def truth_of(roi: np.ndarray, tag: str) -> tuple[float, float, float] | None:
    """grim 抓整屏 + 宿主 NCC ⇒ ROI 在屏幕上的真实中心与峰值（不经 fidus）。"""
    shot = A10.capture(tag)
    if shot is None:
        return None
    peak, x, y = A10.Correlator(H8.luma_of(shot)).peak(H8.luma_of(roi))
    h, w = roi.shape[:2]
    return x + w / 2.0, y + h / 2.0, float(peak)


def mount(backend, host, frame: np.ndarray, x: int, y: int, tag: str, tries: int = 4):
    """挂一只新 surface（先拆旧的），补帧直到独立尺子见到内容。

    补帧不是保险，是 `pool/layer-first-frame-commit` 那条已知缺陷的既定处置
    （`enable()` 后第一发 `update_pixels` 会被静默丢弃）。"""
    backend.destroy_context()
    H4.pump(APP, 1200)
    fh, fw = frame.shape[:2]
    backend.enable(host, fw, fh, x, y)
    H4.pump(APP, 300)
    qf = H1.qimage_from(frame)
    for i in range(tries):
        backend.update_pixels(qf)
        H4.pump(APP, 1200)
        t = truth_of(frame, f"h10_{tag}_try{i}")
        if t is not None and t[2] >= PRESENT_PEAK:
            return t
    return t


def predict(edge: int, out_long: int) -> tuple[int, bool]:
    """贵方模型的算术版：返回 (位置数, 预测失败吗)。half = 输出长边 + 模板长边。"""
    half = out_long + edge
    n = (2 * half + 1) ** 2
    return n, n > MAX_POSITIONS


def read_arm(eng, backend, host, edge: int, out_long: int, x: int, y: int) -> dict:
    """一臂：挂 edge² → 注册 edge² 全幅 → 逐条 estimate，报每条的出口与耗时。"""
    frame = make_frame(edge)
    tag = f"{edge}"
    t = mount(backend, host, frame, x, y, tag)
    arm = {"edge": edge, "tpl_sha": hashlib.sha256(frame.tobytes()).hexdigest()[:16],
           "present": None if t is None else round(float(t[2]), 4)}
    if t is None or t[2] < PRESENT_PEAK:
        arm["register"] = "量具断"
        fact(f"臂 {edge}", f"补帧 {4} 投后尺峰 = {'None' if t is None else f'{t[2]:.4f}'}"
                           f" < {PRESENT_PEAK} ⇒ 内容没上屏，本臂不产出引擎结论")
        return arm
    arm["truth"] = (round(float(t[0]), 2), round(float(t[1]), 2))
    try:
        eng.register_target(frame, False)
        arm["register"] = "ok"
        arm["ceiling"] = float(eng.confidence_ceiling)
    except BaseException as exc:  # noqa: BLE001
        arm["register"] = f"refused:{type(exc).__name__}"
        fact(f"臂 {edge}", f"注册被拒（{type(exc).__name__}）⇒ 不等式那一问本臂不判")
        return arm
    n_pos, pred_lost = predict(edge, out_long)
    arm["pred_lost"] = pred_lost
    arm["positions"] = n_pos
    reads: list[dict] = []
    ceil = arm.get("ceiling")
    for k in range(READS):
        start = time.perf_counter()
        try:
            ex_ey = eng.estimate()
            cfn = float(ex_ey[2])
            cf = H6._f(cfn)
            # 顺带打印 ceiling 并标出 conf==ceiling：fidus 出站请求（pid-1295942 (b)）要这一格
            # ——它是"conf 满值不能单独当失控签名"的直接数据，正确锁定上同样会出现。
            tag = "" if ceil is None else f"｜ceiling={H6._f(ceil)}"
            if ceil is not None:
                tag += "＝ceiling" if abs(cfn - float(ceil)) < 1e-12 else "≠ceiling"
            out = f"读数=({float(ex_ey[0]):.2f},{float(ex_ey[1]):.2f}) conf={cf}{tag}"
            err = "lock"
        except BaseException as exc:  # noqa: BLE001
            if type(exc).__name__ not in ENGINE_EXC:
                raise RuntimeError(f"量具错（非引擎异常 {type(exc).__name__}）：{exc}"
                                   " ⇒ 本臂不判，不当作对上游的否证") from exc
            out = f"抛 {type(exc).__name__}"
            err = type(exc).__name__
        ms = (time.perf_counter() - start) * 1e3
        reads.append({"err": err, "ms": ms})
        fact(f"  {edge} estimate#{k + 1}", f"{out}｜{ms:.1f} ms")
        H4.pump(APP, 240)
    arm["reads"] = reads
    all_lost = all(r["err"] == "FidusTargetLost" for r in reads)
    ok = all_lost if pred_lost else all(r["err"] == "lock" for r in reads)
    arm["match"] = ok
    seen = "全抛 TargetLost" if all_lost else "有读数"
    fact(f"臂 {edge} 对表", f"位置数 {n_pos:,} vs 上限 {MAX_POSITIONS:,} ⇒ 预测"
                            f"{'失败' if pred_lost else '锁定'}；实测{seen}"
                            f" ⇒ {'一致' if ok else '✗ 不一致'}")
    return arm


def control_arm(eng, backend, host, edge: int, out_long: int, x: int, y: int) -> dict:
    """对照：同一只 edge² surface，模板换成中心 96² 裁剪 ⇒ 预测锁定。

    没有它，CAPS 的失败臂可以赖在"surface 太大／位置太差／素材不可匹配"上。"""
    frame = make_frame(edge)
    c0 = (edge - CONTROL_EDGE) // 2
    # `.copy()` 不是风格问题：numpy 交叉切片是**非连续视图**，直接递给 `register_target`
    # 会得到 `ValueError`（首跑实测，当时误记成"对照不成立"）。贵方文档面把这类拒绝归为
    # host-side input fault，与本件一致——是我方量具的锅，不是几何那一问的证据。
    roi = frame[c0:c0 + CONTROL_EDGE, c0:c0 + CONTROL_EDGE].copy()
    t = truth_of(roi, "ctl_present")
    arm = {"edge": edge, "tpl_edge": CONTROL_EDGE,
           "present": None if t is None else round(float(t[2]), 4),
           "pred_lost": predict(CONTROL_EDGE, out_long)[1]}
    if t is None or t[2] < PRESENT_PEAK:
        arm["result"] = "量具断"
        return arm
    try:
        eng.register_target(roi, False)
    except BaseException as exc:  # noqa: BLE001
        arm["result"] = f"refused:{type(exc).__name__}"
        fact("对照臂", f"96² 裁剪注册被拒（{type(exc).__name__}）⇒ 对照不成立")
        return arm
    arm["ceiling"] = float(eng.confidence_ceiling)
    start = time.perf_counter()
    try:
        ex_ey = eng.estimate()
        arm["result"] = f"lock ({float(ex_ey[0]):.2f},{float(ex_ey[1]):.2f})"
    except BaseException as exc:  # noqa: BLE001
        if type(exc).__name__ not in ENGINE_EXC:
            raise RuntimeError(f"量具错（对照臂，非引擎异常 {type(exc).__name__}）：{exc}"
                               " ⇒ 不读成『对照不成立』") from exc
        arm["result"] = f"抛 {type(exc).__name__}"
    arm["ms"] = (time.perf_counter() - start) * 1e3
    fact("对照臂", f"{edge}² surface + {CONTROL_EDGE}² 模板 ⇒ {arm['result']}"
                   f"（预测锁定，位置数 {predict(CONTROL_EDGE, out_long)[0]:,}）")
    return arm


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-long", type=int, default=1366,
                    help="输出长边（物理像素）；本机 1366，换机器请如实改这个数")
    ap.add_argument("--arms", default="all",
                    help=f"边长子集（逗号分隔，取自 {'/'.join(str(s) for s in ALLOWED)}），"
                         "默认全跑刀刃四臂。只重跑对照时给单个失败臂即可（如 `--arms 640`），"
                         "对照臂总在本子集最大那一臂之后；400 是 fidus 要的峰值档，需显式列出")
    args = ap.parse_args()
    sizes = SIZES if args.arms == "all" else tuple(
        int(s) for s in args.arms.replace(" ", "").split(","))
    if not sizes or any(s not in ALLOWED for s in sizes):
        print(f"✗ --arms 只接受 {ALLOWED} 的子集，收到 {args.arms!r}")
        return 1

    idy = H7.arm_identity()
    if not idy["anchor_ok"] or not idy["so_ok"]:
        fact("⚠ 锚点与登记不符", "下面的数字仍会打印，但只能按**本轮实际锚点**读")

    global APP  # noqa: PLW0603
    APP = QApplication(sys.argv[:1])
    eng = fidus.Fidus.build_wayland()
    host = H1.HostWindow()
    host.show()
    H4.pump(APP, 500)
    backend = H1.FACADE.get_backend()
    fact("gate_status", eng.gate_status())
    fact("输出长边取数", f"按命令行 {args.out_long}（本探针不自动探测输出几何；"
                        "改机器忘改这个数 ⇒ 临界算错，故明写在此）")

    # 第一臂兼做本会话唯一一次校准的落点（A 臂实测校准每会话一次，§12q.5）：
    # 注册 → 校准 → 之后各臂不再校准，帧跨 surface 重建复用。
    # 落点按**全四臂的最大边**算，不按本子集：`--arms 640` 复跑与全量跑的 surface 几何必须一致，
    # 否则两次运行的"临界"对不上表（600 单跑时仍摆在全量跑的 x 上）。
    x = (args.out_long - max(SIZES)) // 2
    y = 64
    fact("本轮臂子集", f"{list(sizes)}（全四臂 = {list(SIZES)}）")
    arms: list[dict] = []
    ctl: dict = {}
    for i, edge in enumerate(sizes):
        print(f"\n=== 臂 {edge}²（全幅模板）===")
        if i == 0:
            frame = make_frame(edge)
            t = mount(backend, host, frame, x, y, f"cal{edge}")
            if t is None or t[2] < PRESENT_PEAK:
                print("VERDICT-CAPS : ✗ 首臂内容没上屏 ⇒ 量具断，不产出任何数字")
                backend.destroy_context()
                return 1
            try:
                eng.register_target(frame, False)
            except BaseException as exc:  # noqa: BLE001
                print(f"VERDICT-CAPS : ✗ 首臂注册被拒（{type(exc).__name__}）⇒ 无从校准")
                backend.destroy_context()
                return 1
            t0 = time.perf_counter()
            try:
                cal = eng.calibrate_once()
            except BaseException as exc:  # noqa: BLE001
                print(f"VERDICT-CAPS : ✗ calibrate_once 抛 {type(exc).__name__}: {exc}"
                      " ⇒ 本探针不产出任何数字")
                backend.destroy_context()
                return 1
            fact("calibrate_once", f"{(time.perf_counter() - t0) * 1e3:.0f} ms "
                                   f"scale={getattr(cal, 'scale', '?')}")
        arms.append(read_arm(eng, backend, host, edge, args.out_long, x, y))
        if edge == max(sizes):
            print(f"\n=== 对照臂（{edge}² surface + {CONTROL_EDGE}² 模板）===")
            ctl = control_arm(eng, backend, host, edge, args.out_long, x, y)

    backend.destroy_context()
    print("\n[VERDICT]")
    got_lost = [a["edge"] for a in arms if a.get("pred_lost")]
    got_lock = [a["edge"] for a in arms if not a.get("pred_lost")]
    bad = [a["edge"] for a in arms if not a.get("match", False)]
    for a in arms:
        pos = a.get("positions")
        rd = a.get("reads") or []
        ms0 = "-" if not rd else f"{rd[0]['ms']:.1f} ms"
        fact("  出口", f"{a['edge']}²：注册={a.get('register')} 尺峰={a.get('present')} "
                       f"位置数={'-' if pos is None else f'{pos:,}'} 首条={ms0} 预测="
                       f"{'TargetLost' if a.get('pred_lost') else 'lock'} "
                       f"一致={a.get('match')}")
    verdict = ("✗ 量具断（无臂达在位线）" if not any("match" in a for a in arms)
               else ("PASS 本轮各臂全中" if not bad else f"✗ 不一致的臂 = {bad}"))
    # 出口行自带臂数与子集：`--arms 640` 的 PASS 不能被读成"四臂全中"（复刻首跑那一行）。
    print(f"VERDICT-CAPS     : 本轮 {len(arms)} 臂 {list(sizes)}｜预测失败的边长 {got_lost}"
          f"／预测锁定的边长 {got_lock}｜{verdict}")
    # fidus 出站请求 (a)：二值预测"400² 应是允许臂里最慢的那一个"（其 work 公式峰在 s≈400）。
    # 缺臂不判（见本轮坑账）：400 没跑过就绝不打 ✗，那是"没测"不是"否"。
    if 400 in sizes:
        locky = [(a["edge"], (a.get("reads") or [{}])[0].get("ms"))
                 for a in arms if not a.get("pred_lost") and a.get("reads")]
        peak = max((p for p in locky if p[1] is not None), key=lambda p: p[1], default=None)
        print(f"VERDICT-PEAK     : 允许臂首条耗时 {[(e, f'{m:.0f}') for e, m in locky]}"
              f"｜最慢 = {peak[0] if peak else '-'}² ⇒ 贵方"
              f"「峰在 s≈400」预测 {'成立' if peak and peak[0] == 400 else '✗ 不成立'}")
    else:
        print("VERDICT-PEAK     : 本轮未跑 400 臂 ⇒ 本行不判（缺臂不是否定）")
    ctl_ok = ctl.get("result", "").startswith("lock")
    print(f"VERDICT-CONTROL  : {ctl.get('result', '未跑')}"
          f" ⇒ {'失败可归因到模板最长边进不等式，而非 surface 大小' if ctl_ok else '✗ 对照不成立'}")
    print(f"VERDICT-HOST     : 临界模板边 = (sqrt({MAX_POSITIONS:,})−1)/2 − 输出长边 "
          f"= {(MAX_POSITIONS ** 0.5 - 1) / 2 - args.out_long:.1f} px（本机 {args.out_long} 输出）")
    for out in (1366, 1920, 2560, 3840):
        crit = (MAX_POSITIONS ** 0.5 - 1) / 2 - out
        fact("  支持列表算术", f"输出长边 {out} ⇒ 临界模板边 {crit:.0f} px"
                             f"{'（⇒ 任何可用桌宠模板都失败）' if crit < 200 else ''}"
                             + ("　← 本机实测" if out == args.out_long else "　← **算术外推，未实测**"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
