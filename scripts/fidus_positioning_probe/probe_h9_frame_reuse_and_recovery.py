#!/usr/bin/env python3
"""H9 · 帧跨 surface 重建还可用吗（我方提案的成本前提）+ 失控之后两条恢复问

出处：fidus 入站回执 `pool/fidus-coast-doc-rotation-and-rulings.md` §3 第 3/4 条，
以及它在 `meapet-frame-reuse-and-invalidation-ask` (a)/(b) 里**明写未验证**的那半句。

四个出口（A/B/C 各自独立、不互相顶替，也不合并成一个总评；D 是 B 的对照组；
另有一条附带出口 CADENCE 挂在 B 相上，见下）：

* **A FRAMEREUSE** —— fidus 给的书面契约是「一次开机内只做一次 `calibrate_once`、之后无限复用」，
  但它同时标了一条不对称：**从未在「复用跨 surface 存活」这个形态下验证过**，其「帧描述的是输出、
  不是调用方的 surface」只是**读码结论**。而我方产品形态恰恰**每次切换都重建 surface**
  （`render_host.py:750 _set_layer_mode` → `hide()` + `enable()`），所以这条推理是
  `pool/fidus-switch-positioning.md` 的**成本前提**：成立 ⇒ 一次会话只付一次 ~2 s；
  不成立 ⇒ 每次切换都付，那个产品形态直接垮掉。
  做法：上屏 → 注册 → 校准 → 读数（基准）→ **原位**拆挂（A2，单独分离「surface 被重建」这一个变量）
  → **换位**再拆挂（A3，两点检验：单点全绿不足以判「那份 request↔actual 映射仍对」）；
  全程不再 `calibrate_once`、不再 `register_target`。

* **B REACQUIRE** —— fidus §4 的四条件归因只覆盖「屏幕在位、引擎自困」这一形；
  **恢复判据的设计前提**「目标重新可见后找不找得回来」两边都没有数据。
  做法：把整只 surface 推到屏外若干读（造出 `conf==0` 的失控），再推回**原请求位**，
  连取 16 读（窗宽刻意给到 8 读的两倍——「8 读内没回来」会被读成「再等等就回来了」，
  那条尾巴本身必须是结论的一部分），报「落没落回真位」与「conf 有没有离开 0」。
  在位判据用 `PRESENT_PEAK`，不用 `truth_of` 那道 0.5：96² 模板在贴图上随手能撞到
  0.52–0.62 的**假峰**，拿 0.5 说「在位」会把壁纸读成桌宠。
  **边界（不拿能做的顶替不能做的）**：本相只做「离开画面」那一半；「被遮住」做不了——
  门面不提供更高 layer 的第二只 surface，普通窗口在 overlay 之下 ⇒ 屏上没有能盖住它的东西。
  那一半如实记 `未做`，回给 fidus 时不算闭合。

* **C FLUSH-REFUSED** —— fidus 新写进 `register_target.__doc__` 的一条**只有宿主会踩**的后果：
  「拿一份会被拒的素材去冲刷，冲刷等于没做」（被拒注册是活状态的 no-op）。
  做法：B 已把 surface 推回屏内（滤波器可能已重获取），所以这里**先把失控造回来**，
  再在失控态里用新判据下**必拒**的素材（`halves`：右半复制左半、周期 64 px）尝试重注册，验两件事：
  ① 抛 `FidusUntrackable`；② `confidence_ceiling` **逐位不变**，且读数**接续**冲刷前的外推**速度**
  （量的是「没有另起炉灶」，而不是「位置对不对」，因为失控态本来就没有对的位置）。
  **判据只认速度、不认位移**：失控态里 `p += v·dt`，位移天然正比于「距上一读过了多久」，
  而那次被拒的注册自己要做捕获与分析 —— 拿位移的绝对倍数去卡，等于把「冲刷花了多少秒」
  读成「冲刷改没改滤波器」。旧位移判据（`跳变 ≤ 3×中位步长`）本机已两向出错：
  run 5 边界位移 6.15 倍 ⇒ 误判「否证」（假阴），run 4 边界方向反转却因位移小而放过（假阳）。
  新判据把逐读**时间戳**记进每条记录，直接算速度：基线取**紧贴冲刷的连续纯外推段**
  （`conf==0` 的那些读，段内不足 4 条就不判），再验「方向余弦 ≥0.999 且速度比 ∈[0.5,2]」。
  已跑过的四次里边界位移/中位步长 = 1.39 / 1.41 / 6.15 / （run 6 不判）。前两次的倍数
  与「一次抓屏 + 一次注册」占用的时长同量级；**只有 6.15 那一次**明显更大，而那轮没记时间戳，
  被拒的注册内部到底做几次捕获我方又不可见 ⇒ 事后**无法**区分
  「桌面被第三方全屏占满 ⇒ 边界那段确实约 6 s」与「速度真变了」；该次既不算背书也不算否证，
  留待带时间戳的复跑清算。
  两点都成立 ⇒ 我方给该句背书；不成立 ⇒ 是缺陷，回投。

* **D RECOVER（对照组，本探针自加）** —— C 只说清了「**被拒**的冲刷等于没做」，没说「没被拒的冲刷值多少」。
  而 B 若给出「不自行找回」，产品侧唯一还能做的动作就是拿**同一素材**重注册一次
  （fidus 明写注册是滤波器状态的唯一写入者，且 `calibrate_once` 被它显式排除）。
  做法：内容推回屏内（先验在位）→ 3 读确认仍在路上 → `register_target(同一 ROI)` → 8 读。
  这一臂把 B 的负结果变成可执行结论：救得回 ⇒ 恢复动作有解、成本 = 一次注册；
  救不回 ⇒ 定位消费点只能整段退回合成数（不修一半）。
  **B 的答案不是每台机器一个**：同一条臂在 run 5 判「16 读不落回」、在 run 6 判「首条即落回」。
  run 6 那轮没打印失控相的读数位置 ⇒ 分不清「窗扫到目标」与「预测本就停在真位附近」这两种成因，
  所以自本轮起把**回屏末条读数离真位的距离**记进出口（`d_off ≤ 60 px` 的落回只算位置巧合）。
  两轮之间已见的另一处差别是失控相里引擎撞到的假峰高度（run 5 独立尺子峰 0.55–0.65、当时桌面
  被第三方全屏占满；run 6 稳定 0.5195）。**n=1:1，因果未确立**，
  所以 B 的结论形态只能是「可达且可撤销，但**不保证在有限条数内自行恢复**」；
  产品侧不能把「等它自己回来」当恢复动作，D 那条重注册才是。B 相未跑时该出口记「未跑」。

* **附带出口 CADENCE（fidus §3-6 的归因欠账）** —— 入档那条「读数节拍从 ≈1.0 s 减半到 ≈0.4 s」
  是**读环周期**，含 grim 抓屏 + 我方 FFT + Qt pump，按记录形态无法归因给引擎。本探针把
  「引擎单发耗时」与「读环周期」分开量，并**按引擎当时的内部状态分档**（真命中／假峰命中／
  纯外推，见 `tier_of`）——跨相、跨 run 直接比两档会被桌面负载漂移污染（同一"真命中"档
  跨 run 中位从 130 ms 到 308 ms）。B 相未跑时该出口记「未跑」。

真值口径与 H8 同：每读一次 `estimate()` 就 `grim` 抓一次整屏，用宿主侧 NCC 找同一块 ROI 的
真实中心（不经 fidus）。屏上找不到时该读**不进入统计**，失败路径的墙钟一律不当耗时上报。

量具前置（本机实测过一次误判，故写死）
--------------------------------------
`truth_of` 那道 0.5 只能判「有没有可比的数」。有一轮整条链跑在一张**首帧被丢弃**的浮层上
（`pool/layer-first-frame-commit`：`enable()` 后第一发 `update_pixels` 被静默丢），屏上根本没有
我方内容，尺子却照样在壁纸上给出 0.5–0.64 的峰，且引擎读数与这些假峰**逐条对得上**
——那种轮次会产出「同位=不过、换位=不过」这种看着像否证、其实是量具断了的结论。故本探针：
① 一切「在位」判据统一走 `present`（≥`PRESENT_PEAK`），闸门因此会照设计再投一帧；
② 校准之后再过 `liveness`（挪 50 px 尺子必须跟着挪，并排掉它自己引入的位移瞬态），
不过就退码 1、**不产出任何数字**。该 ROI 的 alpha 全为 255 ⇒ 峰值掉到 1.0 以下与
「背景换成了什么」无关，只有「我方内容不在抓屏里」这一种解释。
`liveness` 刻意**不放在校准前**：放前面那一版实测有一次 `calibrate_once` 以
`no changed pixels found` 响亮失败，挪到后面同素材同桌面立刻成功（各 n=1，因果未确立）。

用法
----
    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h9_frame_reuse_and_recovery.py
副作用：真实桌面上挂/拆 layer 浮层（产品帧，461×614）、多次整屏抓取、一次 `calibrate_once`
（屏幕会闪现标定标记）。退出码：0 = 四相都跑到出口（数字再难看、结论再否定也是 0）；1 = 量具/环境不可用。
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
import probe_h5_ceiling_surface as H5  # noqa: E402
import probe_h6_dense_gate as H6  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402
import probe_h8_roi_on_screen as H8  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

OFF_PX = 60          # 屏外相：越过右边缘再多推这么多，保证整块内容都不在屏上
LOST_READS = 8       # 失控态连取几条（fidus §4：`lost_streak` 只增不减、无重获取超时）
BACK_READS = 16      # 回到屏内之后连取几条（8 读不够下"找不回来"的结论——窗宽本身会被当成结论）
SETTLE = 5           # 瞬态窗宽（H8 同口径：位移后约 4 读才收住，留一格）
PRESENT_PEAK = 0.9   # 独立尺子的"内容确实在位"线：本批帧在位时逐读恒为 1.0000，
                     # 而 96² 模板在贴图上随手就能撞到 0.52–0.62 的**假峰** ⇒ 0.5 那道
                     # （`truth_of` 的可用线）只能判"有没有可比的数"，不能判"在不在位"
                     # 该模板 alpha 全 255 ⇒ 峰值掉到 1.0 以下与"背景换了什么"无关，
                     # 只有"我方内容不在抓屏里"这一种解释（见 `liveness`）
LIVE_DX = 50         # 量具活性自检的位移量：尺子必须跟着挪，否则抓屏是冻结/被独占的
APP: QApplication | None = None
ALL_ROWS: list[dict] = []   # 跨相累积的逐读记录（label + 状态档 + 两档耗时），CADENCE 用


def fact(label, value) -> None:
    H6.fact(label, value)


def _f(v) -> str:
    return H6._f(v)


def truth_of(roi: np.ndarray, tag: str) -> tuple[float, float, float] | None:
    """grim 抓整屏 + 宿主 NCC ⇒ ROI 在屏幕上的真实中心与峰值（不经 fidus）。"""
    return H8.truth_of(roi, tag)


def settle_present(backend, frame: np.ndarray, roi: np.ndarray, tag: str, tries: int = 4):
    """反复**补帧**直到独立尺子见到内容（已有 surface 的前提下）。

    这一圈不是保险，是 `pool/layer-first-frame-commit` 那条已知缺陷的既定处置：
    `enable()` 之后第一发 `update_pixels` 会被静默丢弃（与门面自己的 pump 线程赛跑），
    再提交一帧即成。缺了它，探针会在一张**根本没上屏**的浮层上量出「不可判」，
    把量具断掉误读成被测对象不行——本轮 A3/B/D 三格就是这么连带作废的。
    判据用 `present`（≥`PRESENT_PEAK`）而不是 `truth_of` 的 0.5：0.5 只说"有个可比的数"。"""
    qf = H1.qimage_from(frame)
    t = None
    for i in range(tries):
        t = truth_of(roi, f"h9_{tag}{i}")
        if present(t):
            fact(f"{tag}·上屏", f"第 {i + 1} 次抓到 ⇒ 真中心=({t[0]:.2f},{t[1]:.2f}) 峰值={t[2]:.4f}")
            return t
        fact(f"{tag}·上屏", f"第 {i + 1}/{tries} 次未在位"
             f"（峰 {'None' if t is None else f'{t[2]:.4f}'} < {PRESENT_PEAK}）⇒ 再提交一帧")
        backend.update_pixels(qf)
        H4.pump(APP, 1200)
    return t          # 失败时把**末次测量**交回去（可能仍 <在位线），供调用方报峰；None=尺子没数


def mount(backend, host, fw: int, fh: int, frame: np.ndarray, roi: np.ndarray,
          x: int, y: int, tag: str, rebuild: bool, tries: int = 4):
    """挂（或拆后重挂）一只 surface，并交给 `settle_present` 直到内容确实在屏上。"""
    if rebuild:
        backend.destroy_context()
        H4.pump(APP, 1200)
    backend.enable(host, fw, fh, x, y)
    backend.update_pixels(H1.qimage_from(frame))
    H4.pump(APP, 1400)
    return settle_present(backend, frame, roi, tag, tries)


def liveness(roi: np.ndarray, backend, t0: tuple, x: int, y: int) -> tuple[bool, str]:
    """量具活性自检：把浮层挪 `LIVE_DX` px，独立尺子必须**跟着**挪这么多。

    为什么必须有这道闸（`present` 顶替不了它）：`present` 能挡住「屏上压根没我方内容」
    （run 2 那种，闸门会重投或退码 1），但挡不住「抓屏冻结而我方内容恰好在里面」——
    那种轮次每条读数都自洽却全是陈旧的。只有"跟着请求动"能分开二者。
    刻意只抓**一次**（挪走之后）：原位由 `gate` 刚证明过，多一发全屏抓屏只是给校准添堵。
    返回 (是否活, 一句话证据)。"""
    backend.set_position(x + LIVE_DX, y)
    H4.pump(APP, 1200)
    tb = truth_of(roi, "h9_live_moved")
    backend.set_position(x, y)
    H4.pump(APP, 1200)
    if not present(tb):
        return False, (f"挪到 (+{LIVE_DX},+0) 后尺子未在位"
                       f"（峰 {'None' if tb is None else f'{tb[2]:.3f}'}）")
    dx, dy = tb[0] - t0[0], tb[1] - t0[1]
    ok = abs(dx - LIVE_DX) <= 3.0 and abs(dy) <= 3.0
    return ok, (f"请求位移 (+{LIVE_DX},+0) ⇒ 尺子实测 ({dx:+.2f},{dy:+.2f}) px"
                f"（原位真中心 ({t0[0]:.2f},{t0[1]:.2f})）")


def read_phase(label: str, eng, roi: np.ndarray, n: int, settle: int) -> list[dict]:
    """连取 n 读。**每读单独计两件事**：引擎单发耗时 `ms`（只裹 `estimate()`）与
    读环周期 `period_ms`（上一条起点到本条起点，含 grim + 宿主 FFT + pump）。
    这个分开是 fidus §3-6 那条「节拍减半」欠账的直接后果：入档的 1.0 s→0.4 s 量的是
    `period_ms` 那一档，它把宿主开销算在引擎头上，按记录形态**根本没法归因**。"""
    rows: list[dict] = []
    prev_start: float | None = None
    for k in range(n):
        start = time.perf_counter()
        period = None if prev_start is None else (start - prev_start) * 1e3
        prev_start = start
        try:
            x, y, c = eng.estimate()
            ms = (time.perf_counter() - start) * 1e3
        except BaseException as exc:  # noqa: BLE001
            ms = (time.perf_counter() - start) * 1e3
            fact(f"  {label} #{k}", f"estimate 抛 {type(exc).__name__}（{ms:.1f} ms）")
            rows.append({"k": k, "err": type(exc).__name__, "ms": ms, "period_ms": period,
                         "t": start})
            H4.pump(APP, 240)
            continue
        t = truth_of(roi, f"h9_{label}{k}")
        if not present(t):
            fact(f"  {label} #{k}", f"读数=({x:.2f},{y:.2f}) 独立尺子未在位"
                 f"（峰 {'None' if t is None else f'{t[2]:.4f}'} < {PRESENT_PEAK}）"
                 f" conf={_f(float(c))}｜引擎 {ms:.1f} ms 环 "
                 f"{'-' if period is None else f'{period:.0f}'} ms"
                 "⇒ 该读数不进入位置统计")
            rows.append({"k": k, "x": float(x), "y": float(y), "conf": float(c), "err": "no-truth",
                         "ms": ms, "period_ms": period, "t": start})
            H4.pump(APP, 240)
            continue
        dx, dy = float(x) - t[0], float(y) - t[1]
        rows.append({"k": k, "x": float(x), "y": float(y), "conf": float(c),
                     "tx": t[0], "ty": t[1], "dx": dx, "dy": dy, "peak": t[2],
                     "ms": ms, "period_ms": period, "t": start, "settled": k >= settle})
        fact(f"  {label} #{k}", f"读数=({x:.2f},{y:.2f}) 真=({t[0]:.2f},{t[1]:.2f}) "
                                 f"⇒ 误差 ({dx:+.2f},{dy:+.2f}) px conf={_f(float(c))} "
                                 f"尺峰={t[2]:.4f}｜引擎 {ms:.1f} ms 环 "
                                 f"{'-' if period is None else f'{period:.0f}'} ms")
        H4.pump(APP, 240)
    ALL_ROWS.extend({"label": label, **r} for r in rows if label != "drain")
    return rows


def tier_of(r: dict) -> str | None:
    """把一条读数按**引擎当时的内部状态**分档 —— CADENCE 出口要用它，因为「命中」不是一档：
    `conf>0` 里既有对上尺子的真命中，也有锁在壁纸假峰上的命中，而 `conf==0` 是纯外推。"""
    if "conf" not in r:
        return None
    if float(r["conf"]) == 0.0:
        return "纯外推 conf=0"
    if "dx" not in r:
        return "假峰命中（尺子未在位）"
    if abs(r["dx"]) <= 3.0 and abs(r["dy"]) <= 3.0:
        return "真命中"
    return "假峰命中"


def cadence_tiers() -> list[str]:
    """各档的（引擎单发中位，读环中位，条数）。分档是同会话同时序的对照，不受跨 run 负载漂移影响。"""
    out: list[str] = []
    for tier in ("真命中", "假峰命中", "假峰命中（尺子未在位）", "纯外推 conf=0"):
        rows = [r for r in ALL_ROWS if tier_of(r) == tier and "ms" in r]
        if len(rows) < 3:
            continue
        eng = float(np.median([r["ms"] for r in rows]))
        pers = [r["period_ms"] for r in rows if r.get("period_ms") is not None]
        per = float(np.median(pers)) if pers else float("nan")
        out.append(f"{tier} 引擎 {eng:.0f} ms／环 {per:.0f} ms（n={len(rows)}）")
    return out


def cadence(rows: list[dict]) -> tuple[float, float]:
    """该相的（引擎单发中位 ms，读环中位 ms）。样本不足回 nan，不猜。"""
    ms = [r["ms"] for r in rows if "ms" in r]
    per = [r["period_ms"] for r in rows if r.get("period_ms") is not None]
    if not ms or not per:
        return float("nan"), float("nan")
    return float(np.median(ms)), float(np.median(per))


def with_truth(rows: list[dict]) -> list[dict]:
    return [r for r in rows if "dx" in r]


def present(t) -> bool:
    """独立尺子的**在位**判据（不是「有可比数」判据）。见 `PRESENT_PEAK` 那条注释：
    拿 0.5 那道线去说「内容回来了/没出去」会把壁纸上的假峰读成在位。"""
    return t is not None and t[2] >= PRESENT_PEAK


def main() -> int:
    global APP
    ap = argparse.ArgumentParser(description="H9: 帧跨 surface 重建 + 失控后的两条恢复问")
    ap.add_argument("--batch", default="/tmp/h7_frames", help="与 H7/H8 同批的落盘帧")
    ap.add_argument("--est", type=int, default=6, help="基准相 estimate 次数")
    ap.add_argument("--no-c", action="store_true", help="跳过 C 相（冲刷被拒素材）")
    ap.add_argument("--no-liveness", action="store_true",
                    help="跳过量具活性自检（只为回答「是不是这道闸招来了失败」，一次运行一个变量）")
    args = ap.parse_args()

    idy = H7.arm_identity()
    frames = H7.load_frames(Path(args.batch))
    if not frames:
        print(f"✗ 读不到 {args.batch}（先跑 H7 `--dump`；本探针与 H8 刻意同批）")
        return 1
    if not idy["anchor_ok"] or not idy["so_ok"]:
        fact("⚠ 锚点与登记不符", "下面的数字仍会打印，但只能按**本轮实际锚点**读")

    APP = QApplication(sys.argv)
    eng = fidus.Fidus.build_wayland()

    print("\n=== A0 · CPU 侧选框（与 H7/H8 同一规则、同一批帧）===")
    cands = H7.candidates(frames[0])
    review = H7.score_exact(eng, cands)
    pick = H7.pick_by_rule(review)
    if pick is None:
        print("✗ 帧 0 选不出可行 ROI ⇒ 无从上屏")
        return 1
    roi = pick["box"]
    cap0 = pick["engine_cap"]
    sha = hashlib.sha256(roi.tobytes()).hexdigest()[:16]
    fact("选中", f"{pick['size']}²@({pick['x0']},{pick['y0']}) ceiling={_f(cap0)} "
                 f"模板指纹={sha}")

    fh, fw = frames[0].shape[:2]
    px, py = 260, 90
    px2, py2 = px + 40, py + 30      # A3/B/C 共用的第二落点（一次定义，免得各相各算各的）
    off_x = 1366 + OFF_PX            # 屏外落点：整块 461 px 宽的内容全部越过右边缘
    host = H1.HostWindow()
    host.show()
    H4.pump(APP, 500)
    backend = H1.FACADE.get_backend()

    print("\n=== A1 · 第一只 surface：上屏 → 注册 → 校准（本会话唯一一次校准）===")
    t0 = mount(backend, host, fw, fh, frames[0], roi, px, py, "A1", rebuild=False)
    if not present(t0):
        print("\n[VERDICT]\nVERDICT-VISIBLE : ✗ 补帧四投仍见不到该 ROI ⇒ 量具断，本探针不产出任何数字")
        backend.destroy_context()
        return 1
    cap, err, ms = H7.engine_read(eng, roi)
    if cap is None:
        print(f"\n[VERDICT]\nVERDICT-REGISTER: ✗ 引擎拒注册（{err}）⇒ 无从判断")
        backend.destroy_context()
        return 1
    try:
        cal = eng.calibrate_once()
    except fidus.FidusCalibrationError as exc:
        # 响亮失败是 fidus 的正确行为（不是段错误、不是挂起）。对本探针而言它是**环境/前置**
        # 断了：本相的补帧已证明内容在屏上（`mount` 过了），fidus 的标记检测却读到零变化。
        # 此时 liveness 还没跑（它在校准之后），故不下"抓屏冻结"的结论，只登记这条事实。
        print(f"\n[VERDICT]\nVERDICT-CALIB    : ✗ {type(exc).__name__}: {exc}\n"
              "  本会话 `mount` 已证明内容在屏上 ⇒ 校准看不到自己的标记，本轮不产出任何数字")
        backend.destroy_context()
        return 1
    fact("calibrate_once", f"scale={getattr(cal, 'scale', '?')}（{ms:.1f} ms 为注册耗时）")
    # 活性闸**放在校准之后**：带它的前置版实测有一次 `calibrate_once` 以
    # `no changed pixels found` 响亮失败、去掉它同素材同桌面立刻成功（各 n=1，因果**未确立**，
    # 但"宿主的全屏抓屏突发与校准失败同现"这条线索本身就是产品集成要防的事，
    # 故不拿被测对象的敏感步骤去赌——见 §12q 与本探针头注）。
    if args.no_liveness:
        live, why = True, "跳过（--no-liveness）"
    else:
        live, why = liveness(roi, backend, t0, px, py)
    if not live:
        print(f"\n[VERDICT]\nVERDICT-INSTRUMENT : ✗ 量具活性不过 ⇒ {why}\n"
              "  抓屏里没有「跟着请求动」的我方内容（第三方全屏窗口独占输出、或输出冻结），"
              "本轮不产出任何数字")
        backend.destroy_context()
        return 1
    fact("量具活性", why)
    # 活性闸自己那次来回位移会引入 H8 量过的阻尼收敛瞬态 ⇒ 先排掉 SETTLE 条不计入统计，
    # 否则"基准相"量的就不是稳态了。
    drain = read_phase("drain", eng, roi, SETTLE, SETTLE)
    fact("瞬态排空", f"{len(drain)} 条仅用于等收敛，不进入任何统计"
         f"（对上尺子 {len(with_truth(drain))} 条）")
    rows_base = read_phase("base", eng, roi, args.est, 1)
    base = with_truth(rows_base)
    if not base:
        print("\n[VERDICT]\nVERDICT-BASE    : ✗ 基准相没有一条对上尺子 ⇒ 后面各相全部作废")
        backend.destroy_context()
        return 1
    fact("基准", f"{len(base)} 条对上尺子，误差均值 ({np.mean([r['dx'] for r in base]):+.2f},"
                f"{np.mean([r['dy'] for r in base]):+.2f}) px，conf={_f(base[-1]['conf'])}")

    print("\n=== A2 · 拆 surface、**原位**重挂一只新的：不校准、不重注册，立刻读数 ===")
    # 刻意先做**同位**重挂：把「surface 被重建」这一个变量单独分离出来。
    # 若一上来就换位置，失败时会分不清是帧断了还是位移瞬态（H8 实测位移后约 4 读才收住）。
    def remount(x: int, y: int, tag: str, n: int, settle: int):
        t = mount(backend, host, fw, fh, frames[0], roi, x, y, tag, rebuild=True)
        if not present(t):
            return [], [], None        # 交给下游的 None：本相不产出数字（原因已由补帧日志给出）
        rows = read_phase(tag, eng, roi, n, settle)
        return rows, with_truth(rows), t

    def report(rows_ok: list[dict]) -> str:
        if not rows_ok:
            return "✗ 没有一条读数对上尺子"
        steady = [r for r in rows_ok if r["settled"]] or rows_ok
        return (f"{len(rows_ok)} 条对上尺子（稳态 {len(steady)} 条），"
                f"稳态误差均值 ({np.mean([r['dx'] for r in steady]):+.2f},"
                f"{np.mean([r['dy'] for r in steady]):+.2f}) px，"
                f"最大单条 {max(abs(r['dx']) + abs(r['dy']) for r in steady):.2f} px，"
                f"conf={_f(rows_ok[-1]['conf'])}")

    rows_a2, ok_a2, t_a2 = remount(px, py, "reuse0", args.est, 1)
    if t_a2 is None:
        verdict_a = "不可判（原位重挂后屏上没有内容 ⇒ 是浮层没上屏，不是帧的问题）"
        shift = (float("nan"), float("nan"))
    else:
        fact("A2 出口（同位）", report(ok_a2) + f"｜尺子中心=({t_a2[0]:.2f},{t_a2[1]:.2f})"
             f"｜ceiling 仍是 {_f(eng.confidence_ceiling)}（注册时 {_f(cap)}）")
        print("\n=== A3 · 再拆再挂，**换位置**：两点检验那份映射有没有偷偷错掉 ===")
        # 单点全绿不足以判"帧仍可用"：若 request↔actual 的线性映射被换成了别的（比如退化成了
        # 恒等），在原位上照样能量出一个自洽的数——只有第二点能暴露它。H8 已量到位移是
        # 阻尼收敛瞬态，故本臂取 8 读、稳态窗宽同 B。
        rows_a3, ok_a3, t_a3 = remount(px2, py2, "reuse1", 8, SETTLE)
        if t_a3 is None:
            verdict_a = "不可判（A2 在位、A3 重挂后未在位 ⇒ 量具断了，不能反过来说帧断了）"
            shift = (float("nan"), float("nan"))
        else:
            steady3 = [r for r in ok_a3 if r["settled"]]
            line = report(ok_a3)
            # 位移的**请求量**与**独立尺子实测的位移量**对表：这是"帧给的变换仍然对"的第二条独立证据，
            # 不依赖 fidus 自己的读数。
            shift = (t_a3[0] - t_a2[0], t_a3[1] - t_a2[1]) if t_a2 else (float("nan"),) * 2
            fact("A3 出口（换位）", line + f"｜请求位移 (+40,+30) 尺子实测位移 "
                 f"({shift[0]:+.2f},{shift[1]:+.2f}) px"
                 f"｜ceiling 仍是 {_f(eng.confidence_ceiling)}")
            if not (ok_a2 and steady3):
                # 在位闸门过了但可用读数不足 ⇒ 是统计量不够，不是"误差不合格"。分开写，
                # 免得一次环境抖动被读成对成本前提的否证（本轮之前就是这么误判过一次）。
                verdict_a = (f"不可判（同位可用 {len(ok_a2)} 条 / 换位稳态 {len(steady3)} 条"
                             "⇒ 样本不足，不拿空统计下结论）")
            else:
                a2_ok = max(abs(r["dx"]) + abs(r["dy"]) for r in (
                    [r for r in ok_a2 if r["settled"]] or ok_a2)) <= 3.0
                a3_ok = max(abs(r["dx"]) + abs(r["dy"]) for r in steady3) <= 3.0
                verdict_a = ("帧跨 surface 重建**仍可用**：原位重挂与换位重挂两点的稳态误差都 ≤3 px，"
                             "且全程未再 `calibrate_once`、未重注册 ⇒ fidus (b) 末句那条"
                             "「读码结论、非实测」现由我方实测背书"
                             if a2_ok and a3_ok else
                             f"✗ 至少一臂稳态误差超 3 px（同位={'过' if a2_ok else '不过'}、"
                             f"换位={'过' if a3_ok else '不过'}）⇒ 那条推理未获背书")
    fact("A 出口", verdict_a)

    print("\n=== B · 推到屏外造失控，再推回原请求位（fidus 缺的那格恢复证据）===")
    if t_a2 is None:
        # 后续各相都要一个"内容确实在屏上"的落点：A2 没过就没资格往下摆位（否则会量到一张空桌）。
        print("✗ A2 未上屏 ⇒ B/C 不产出数字（量具前提缺失，不猜）")
        verdict_b = "未跑（A2 未上屏）"
        verdict_c = "未跑（A2 未上屏）"
        verdict_6 = "未跑（A2 未上屏）"
    else:
        backend.set_position(off_x, py2)
        H4.pump(APP, 1200)
        rows_lost = read_phase("offscreen", eng, roi, LOST_READS, 1)
        confs_lost = [r["conf"] for r in rows_lost if "conf" in r]
        verdict_6 = None      # 由文末 `cadence_tiers()` 结算（要等各相的行都齐了才分得出档）
        fact("失控态", f"{len(confs_lost)} 条 conf 取值 = {sorted(set(_f(c) for c in confs_lost))}"
                      f"；尺子判为在位 {len(with_truth(rows_lost))}/{len(rows_lost)} 条"
                      "（屏上应当无内容 ⇒ 位置统计不参与）")
        # fidus §3-6：入档那条「读数节拍从 ≈1.0 s 减半到 ≈0.4 s」量的是读环周期，把宿主开销
        # （grim 抓屏 + 我方 FFT + Qt pump）算在了引擎头上。这里把两档分开量、并按**内部状态**
        # 分档（见 `tier_of`），出口在全相跑完后由 `cadence_tiers()` 结算 —— 两相直接比会被
        # 跨相的桌面负载漂移污染（同一档"真命中"跨 run 的中位从 130 ms 跳到 308 ms）。
        eng_hit, per_hit = cadence(rows_base)
        eng_coast, per_coast = cadence(rows_lost)
        base_per = [r["period_ms"] for r in rows_base if r.get("period_ms") is not None]
        coast_per = [r["period_ms"] for r in rows_lost if r.get("period_ms") is not None]
        fact("节拍·两相预览", f"命中相 引擎 {eng_hit:.0f} ms／环 {per_hit:.0f} ms，"
             f"失控相 引擎 {eng_coast:.0f} ms／环 {per_coast:.0f} ms"
             "（分档结算见文末 VERDICT-CADENCE）")
        backend.set_position(px2, py2)
        H4.pump(APP, 1200)
        t2 = settle_present(backend, frames[0], roi, "B回屏", 3)
        if not present(t2):
            verdict_b = ("不可判（推回后补帧三投仍未在位，末次峰 "
                         + ("None" if t2 is None else f"{t2[2]:.3f}")
                         + f" < 在位线 {PRESENT_PEAK} ⇒ 内容没回来，不是引擎的问题）")
        else:
            rows_back = read_phase("back", eng, roi, BACK_READS, SETTLE)
            first = rows_back[0] if rows_back else None
            if first is None or "dx" not in first:
                verdict_b = "✗ 回到屏内后首条对不上尺子"
            else:
                ok_b = with_truth(rows_back)
                settled = [r for r in ok_b if r["settled"]] or ok_b
                moved = [r for r in rows_back if "conf" in r and r["conf"] > 0.0]
                near = [r for r in settled if abs(r["dx"]) + abs(r["dy"]) <= 3.0]
                worst_b = min(abs(r["dx"]) + abs(r["dy"]) for r in settled)
                # 「落回真位」有两种截然不同的成因，不拆开就会把巧合记成能力：
                # 引擎在失控相末尾**停在哪里**决定了回屏那一读的搜索窗盖不盖得住真位。
                # 末尾读数本来就离真位几十 px ⇒ 窗必然盖住 ⇒ 第一条就对上纯属位置巧合；
                # 末尾读数在几百 px 之外却仍收敛回来 ⇒ 才有"窗随 `lost_streak` 变宽后扫到目标"这一格。
                off_reads = [r for r in rows_lost if "x" in r]
                d_off = (float(np.hypot(off_reads[-1]["x"] - t2[0], off_reads[-1]["y"] - t2[1]))
                         if off_reads else float("nan"))
                verdict_b = (f"内容确已回屏（尺子峰={t2[2]:.4f}@({t2[0]:.0f},{t2[1]:.0f})）；"
                             f"{len(rows_back)} 读里首条误差 "
                             f"({first['dx']:+.2f},{first['dy']:+.2f}) px conf={_f(first['conf'])}，"
                             f"稳态 {len(settled)} 条**最小**误差 {worst_b:.0f} px、落回 ≤3 px 的有 "
                             f"{len(near)} 条；conf 离开 0 的条数 {len(moved)}/{len(rows_back)}；"
                             f"失控相末条读数离真位 {d_off:.0f} px ⇒ "
                             + ("落回了，但回屏前预测就停在真位 "
                                f"{d_off:.0f} px 内 ⇒ 属**位置巧合**，不构成「能自行找回」的证据"
                                if (near and worst_b <= 3.0 and d_off <= 60.0) else
                                "目标重新可见后**自行**找得回来（末条读数尚在 "
                                f"{d_off:.0f} px 外仍收敛）"
                                if (near and worst_b <= 3.0) else
                                "✗ 目标重新可见后**不会**自行找回：读数停在假峰上，"
                                "且 conf 仍非零 ⇒ conf 不是恢复判据（见下 D 臂）"))
        fact("B 出口", verdict_b)
        fact("B 边界", "只测了「离开画面」；「被遮住」本机做不了（门面不给更高 layer 的第二只 surface，"
                       "普通窗口在 overlay 之下）⇒ 该半未闭合，回 fidus 时不得当已答")

    print("\n=== C · 失控态拿「必被拒」的素材冲刷：fidus 那句「冲刷等于没做」的验证位 ===")
    if args.no_c:
        verdict_c = "本轮跳过（--no-c）"
    elif t_a2 is None:
        pass                       # verdict_c 已在上面置为「未跑」，这里不覆盖
    else:
        # B 相把 surface 推回了屏内 ⇒ 滤波器可能已经重获取。fidus 那句话的主语是**失控态**，
        # 所以这里必须先把失控**造回来**，不能拿 B 之前的旧失控态读数当基线（那中间隔了一次成功重获取）。
        backend.set_position(off_x, py2)
        H4.pump(APP, 1200)
        rows_off2 = read_phase("off2", eng, roi, LOST_READS, 1)
        pos_off2 = [r for r in rows_off2 if "x" in r]
        conf_off2 = sorted({_f(r["conf"]) for r in rows_off2 if "conf" in r})
        on_screen = len(with_truth(rows_off2))     # 在位（峰 ≥ PRESENT_PEAK）才带 dx
        fact("C·再造失控", f"{len(rows_off2)} 条，conf 取值 {conf_off2}；独立尺子判为在位的 "
                           f"{on_screen}/{len(rows_off2)} 条（在位线 {PRESENT_PEAK}）⇒ "
                           + ("无一在位 ⇒ 内容确在屏外，本相的「失控」是真的"
                              if on_screen == 0 else
                              "✗ 有读数到在位 ⇒ 出屏不彻底，本相前提不牢"))
        # 基线只取**紧贴冲刷那一刻**的连续纯外推段（`conf==0` 意味着那一读没有量测、位置只由
        # `p += v·dt` 推进，步长才等于速度）。整相里混进 `conf>0` 的读不能进基线：那几读是引擎
        # 在壁纸上撞到假峰后**重写过速度**，拿它们当中位节拍等于用一段混合运动当尺子。
        tail: list[dict] = []
        for r in reversed(pos_off2):
            if float(r["conf"]) != 0.0:
                break
            tail.append(r)
        tail.reverse()
        steps = [(tail[i]["x"] - tail[i - 1]["x"], tail[i]["y"] - tail[i - 1]["y"])
                 for i in range(1, len(tail))]
        gaps = [(tail[i]["t"] - tail[i - 1]["t"]) * 1e3 for i in range(1, len(tail))]
        med = (float(np.median([s[0] for s in steps])), float(np.median([s[1] for s in steps]))) \
            if steps else (float("nan"), float("nan"))
        med_gap = float(np.median(gaps)) if gaps else float("nan")
        fact("C·外推节拍", f"紧贴冲刷的连续纯外推段 {len(tail)} 条（整相 {len(pos_off2)} 条），"
             f"段内 {len(steps)} 个逐读步长，中位步长 ({med[0]:+.2f},{med[1]:+.2f}) px"
             f"／中位读间隔 {med_gap:.0f} ms"
             "（失控态里读数仍在动 = 纯 `predict` 外推，与 fidus §4 第 1 条一致）")
        halves = H5.pat_halves()
        cap_before = eng.confidence_ceiling
        reg_t = time.perf_counter()
        try:
            eng.register_target(halves, False)
            verdict_c = ("✗ 冲刷**没有被拒** ⇒ 本相的前提不成立（该素材在新判据下不再必拒，"
                         "或注册面与预期不符）；fidus 那句既没被证实也没被否证")
        except BaseException as exc:  # noqa: BLE001
            raised = type(exc).__name__
            reg_ms = (time.perf_counter() - reg_t) * 1e3
            cap_after = eng.confidence_ceiling
            same_cap = (cap_after == cap_before)
            rows_c = read_phase("flush", eng, roi, 4, 1)
            pos_c = [r for r in rows_c if "x" in r]
            # 判据**只认速度**，不认位移。失控态里 `p += v·dt`，位移天然与「距上一读过了多久」
            # 成正比；拿一个固定倍数去卡位移（本探针曾如此）等于把「这次冲刷花了多少秒」
            # 误读成「冲刷改没改滤波器」——冲刷自己要做捕获与分析，那段时长就是纯噪声。
            # 速度不变 ⟺ 边界位移与中位步长**共线**（两分量之比相等），且位移比≈时间比。
            if not pos_c or not tail:
                verdict_c = (f"① 抛 {raised}（{reg_ms:.0f} ms）；② ✗ 位置链断裂"
                             f"（冲刷前连续纯外推 {len(tail)} / 后 {len(pos_c)} 条）⇒ ② 不判")
            elif len(tail) < 4:
                verdict_c = (f"① 抛 {raised}（{reg_ms:.0f} ms）；"
                             f"② ceiling {'逐位不变' if same_cap else '✗ 变了'}；"
                             f"✗ 紧贴冲刷的连续纯外推段只有 {len(tail)} 条（不足 4 条 ⇒ 段内仅 "
                             f"{max(len(tail) - 1, 0)} 个步长，中位节拍与方向都立不住）⇒ ② 不判")
            else:
                jump = (pos_c[0]["x"] - pos_off2[-1]["x"], pos_c[0]["y"] - pos_off2[-1]["y"])
                dt_b = (pos_c[0]["t"] - pos_off2[-1]["t"]) * 1e3
                step_mag, jump_mag = float(np.hypot(*med)), float(np.hypot(*jump))
                if min(step_mag, jump_mag, med_gap, dt_b) <= 0.0:
                    verdict_c = (f"① 抛 {raised}（{reg_ms:.0f} ms）；② ceiling "
                                 f"{'逐位不变' if same_cap else '✗ 变了'}；"
                                 f"退化样本（步长 {step_mag:.2f}／跳变 {jump_mag:.2f} px，"
                                 f"间隔 {med_gap:.0f}／{dt_b:.0f} ms）⇒ ② 不判")
                else:
                    cosang = (med[0] * jump[0] + med[1] * jump[1]) / (step_mag * jump_mag)
                    vel_pre = [float(np.hypot(*s)) / g for s, g in zip(steps, gaps) if g > 0]
                    v_pre = float(np.median(vel_pre))
                    vr = (jump_mag / dt_b) / v_pre
                    continuous = cosang >= 0.999 and 0.5 <= vr <= 2.0
                    verdict_c = (f"① 抛 {raised}（被拒注册自身耗时 {reg_ms:.0f} ms）；"
                                 f"② ceiling {'逐位不变' if same_cap else '✗ 变了'}"
                                 f"（{_f(cap_before)} → {_f(cap_after)}）；"
                                 f"边界位移 ({jump[0]:+.2f},{jump[1]:+.2f}) px = 中位步长的 "
                                 f"{jump_mag / step_mag:.2f} 倍，而边界时间 {dt_b:.0f} ms = "
                                 f"中位读间隔的 {dt_b / med_gap:.2f} 倍；"
                                 f"方向余弦 {cosang:.6f}，速度比 {vr:.3f}"
                                 f"（冲刷前逐读速度中位 {v_pre * 1e3:.2f} px/s → 边界 "
                                 f"{jump_mag / dt_b * 1e3:.2f} px/s）⇒ "
                                 + ("位移比≡时间比且方向共线 ⇒ 速度未变，读数**接续**外推、没有另起炉灶"
                                    if continuous else "✗ 速度变了 ⇒ 那句「冲刷等于没做」被否证")
                                 + ("　⇒ 两点同时成立，fidus 那句由我方实测背书"
                                    if raised == "FidusUntrackable" and same_cap and continuous
                                    else f"　✗ 与那句不符（① {raised} ② same_cap={same_cap}）⇒ 回投"))
                fact("C·冲刷后", f"{len(rows_c)} 条 conf 取值 "
                     f"{sorted({_f(r['conf']) for r in rows_c if 'conf' in r})}；"
                     f"位置续算 {[(round(r['x'], 1), round(r['y'], 1)) for r in pos_c[:3]]}")
    fact("C 出口", verdict_c)

    print("\n=== D · 对照臂：内容回屏后，拿**同一素材**重注册救不救得回来 ===")
    # C 说的是"被拒的冲刷 = 没冲刷"。这一臂是它的对照组：**没被拒**的冲刷值多少——
    # 也是产品侧唯一可执行的恢复动作（fidus 明写注册是滤波器状态的唯一写入者）。
    # 刻意不做"不重注册、再等若干读"的对照：B 已经等了 16 读，答案在那儿。
    backend.set_position(px2, py2)
    H4.pump(APP, 1200)
    t3 = settle_present(backend, frames[0], roi, "D回屏", 3) if t_a2 is not None else None
    if not present(t3):
        verdict_d = ("未跑（A2 未上屏）" if t_a2 is None
                     else "未跑（推回后独立尺子未在位 ⇒ 浮层没回来，不拿空桌判引擎）")
    else:
        pre = [r for r in read_phase("d-pre", eng, roi, 3, 1) if "dx" in r]
        cap2, err2, ms2 = H7.engine_read(eng, roi)
        fact("D·同素材重注册", ("✗ 被拒 ⇒ " + err2) if cap2 is None else
             f"过，ceiling={_f(cap2)}（{ms2:.1f} ms）｜与注册时 "
             + ("逐位相同" if cap2 == cap else f"✗ 不同（{_f(cap)}）"))
        post_rows = read_phase("d-post", eng, roi, 8, SETTLE)
        post = with_truth(post_rows)
        steady_d = [r for r in post if r["settled"]]
        if not post or cap2 is None:
            verdict_d = "✗ 重注册后仍没有一条对上尺子 ⇒ 恢复动作无效（产品只能整段退回合成数）"
        else:
            worst_d = (max(abs(r["dx"]) + abs(r["dy"]) for r in (steady_d or post))
                       if steady_d else float("nan"))
            pre_worst = max((abs(r["dx"]) + abs(r["dy"]) for r in pre), default=float("nan"))
            verdict_d = (f"重注册前 {len(pre)} 读全在路上（最大 {pre_worst:.0f} px）"
                         f"；重注册后首条误差 "
                         f"({post[0]['dx']:+.2f},{post[0]['dy']:+.2f}) px，稳态 {len(steady_d)} 条"
                         f"最大 {worst_d:.2f} px ⇒ "
                         + ("**仅重注册**即可撤销失控（产品恢复动作有解）" if steady_d and worst_d <= 3.0
                            else "✗ 仅重注册不足以撤销 ⇒ 须连 surface 一起重建"))
    fact("D 出口", verdict_d)

    backend.destroy_context()
    H4.pump(APP, 600)

    if verdict_6 is None:
        # 结算 fidus §3-6。分两问，因为它们各自判、答案互不借用（当年入档那条把两问混成了一句）：
        #  问一「读环减半还在不在」—— 读本相与失控相的 `period_ms`，那是当年记录的同一种量；
        #     当年实测 ≈1.0 s → ≈0.4 s ⇒ 比值 ≈0.4，所以「复现」的判据是比值 ≤0.6（近半），
        #     而不是"只要变快了"。
        #  问二「引擎单发耗时与内部状态有关吗」—— 只认**同一相内部**两档都有 ≥3 条的对照：
        #     同相把桌面负载漂移框在了一相之内。跨相、跨 run 比都不算证据（同一档「真命中」
        #     跨 run 中位 130 → 308 ms）。我方只报时间随状态变，不指认是哪条语句。
        tiers = cadence_tiers()
        if len(base_per) >= 3 and len(coast_per) >= 3:
            rl = float(np.median(coast_per)) / float(np.median(base_per))
            q1 = (f"读环中位 命中 {np.median(base_per):.0f} vs 失控 {np.median(coast_per):.0f} ms"
                  f"（比值 {rl:.2f}）⇒ 「减半」"
                  + ("复现" if rl <= 0.6 else "未复现"))
        else:
            q1 = f"读环缺样本（命中 {len(base_per)}／失控 {len(coast_per)} 条）⇒ 问一不判"
        contrasts: list[str] = []
        for lab in dict.fromkeys(str(r["label"]) for r in ALL_ROWS):
            rows_l = [r for r in ALL_ROWS if r.get("label") == lab and "ms" in r]
            hit = [r["ms"] for r in rows_l if tier_of(r) not in (None, "纯外推 conf=0")]
            co = [r["ms"] for r in rows_l if tier_of(r) == "纯外推 conf=0"]
            if len(hit) >= 3 and len(co) >= 3:
                mh, mc = float(np.median(hit)), float(np.median(co))
                contrasts.append(f"{lab} 相 conf≠0 {mh:.0f} ms（n={len(hit)}）vs conf=0 "
                                 f"{mc:.0f} ms（n={len(co)}）⇒ {mh / mc:.1f} 倍")
        q2 = ("；".join(contrasts) + " ⇒ 引擎单发**随内部状态变**（同相对照，我方不指认语句）"
              if contrasts else "各相均无两档各 ≥3 条的同期对照 ⇒ 问二不判")
        verdict_6 = (f"{'；'.join(tiers) if tiers else '各档样本均不足 3 条'}"
                     f"｜问一「读环减半」：{q1}｜问二「时间随状态」：{q2}")

    print("\n[VERDICT]")
    print(f"VERDICT-FRAMEREUSE : {verdict_a}")
    print(f"VERDICT-REACQUIRE  : {verdict_b}")
    print(f"VERDICT-FLUSH-REFUSED : {verdict_c}")
    print(f"VERDICT-RECOVER    : {verdict_d}")
    print(f"VERDICT-CADENCE    : {verdict_6}")
    print("VERDICT-OCCLUDE    : 未做（本机无遮挡物可摆；fidus §3-3 只算半闭合）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
