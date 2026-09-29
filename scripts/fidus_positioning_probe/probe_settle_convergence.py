#!/usr/bin/env python3
"""位移后的两把尺子：过渡在屏幕侧，还是在 fidus 侧？（真机 L3）

背景：§12i **否证**了"位移后抖动 = coasting"的归因（三条样本 `conf` 全 >0，按 §12h 即锁定读数，
抖动仍在）。其后的收敛试验（本件前作）看到位移后读数按 ≈46→17→13→2→0.5 px 几何衰减、4/4 臂
带过冲 ⇒ 形态像时域滤波的**阻尼收敛**。但那件只有一把尺子（fidus 自己的读数），于是"过渡"
有两种完全不同的落点：fidus 读到的是**还没搬完的屏幕内容**（合成器/抓屏侧滞后），或读到的是
**新内容但内部有平滑**。两者的消费规则不一样，必须分开。

本件同时挂两把尺子：
* 尺子 A（独立）：`grim` 抓整幅 + 自带多尺度 NCC 峰定位（复用 `probe_a10_geometry`）⇒ 屏幕当下在哪。
* 尺子 B（被测）：`estimate` 串行读数，**每条之后立刻抓一屏** ⇒ fidus 在什么时刻读到什么、
  同一时刻屏幕实际在哪。

判据（各自独立，不合并）：
* `VERDICT-ANCHOR`：装机轮的 `fidus.__git_commit__` 是否等于 `ANCHOR`。不等 ⇒ 后面三行只说明
  "这台机器上这只轮子如此"，不可归因于登记的那一轮件（同族 h4/h5/h6/hygiene 早有此行，本件 2026-09-30 补）。
* `VERDICT-SCREEN`：真位移臂整段（含紧随 `set_position` 的第一枪）屏幕实测离期望中心的**最大**偏差。
  ≤3 px ⇒ 屏幕自始至终在目标位，屏幕侧没有过渡可言。
* `VERDICT-LOCUS`：屏幕侧无过渡、而 fidus 首条仍偏 >10 px ⇒ 过渡在 **fidus 内部**（它的时域过程），
  不是"读到了还没搬完的屏幕"。
* `VERDICT-CONSUME`：末条离期望 ≤2 px 且与倒数第二条互差 ≤2 px ⇒ "位移后多读几条、取末条"这一
  **时间基**消费规则成立；否则消费方必须按读数自身判稳（等多少条都不保证）。
* `VERDICT-ADJ`：把"要几条"变成可打印的数。**两栏**：只按「相邻互差 ≤2 px」（fidus 文档的原样判据）
  与再并上「离期望 ≤2 px」。二者不等即"假稳"——大位移后读数停在错处不动时互差恒为 0，只看互差会
  在第 2 条就报"稳"（2026-09-30 amp4 实测，故本件原先的单栏输出是一个会自己发绿的假阳性）。
* `VERDICT-RECOVER`（`--recover`）：失控判据是**一条**——末条 conf 塌到 0 而末条离期望 >2 px。
  健康臂 conf 恒等于逐注册常数，进不了 conf 门，故不必再用距离倍数分"发散/衰减"。读数在做什么
  （`冻结` / `慢速外推` / `逼近中`，可叠 `+发散`）只作形态标签打印，不参与判定。
  这一条是两轮补出来的：先只有「≥2× 幅值」这个端点（看不见 amp4 实测的冻结态，其距离比 0.9994），
  补上「互差 ≤2 px」这个端点后仍漏**端点之间**的慢速外推（互差 17 px、距离比 ~1.0 那种，
  `fidus-self-window-positioning` §12e-4 形态② 实测过）⇒ 判据收到收敛性一条上（全程见常量处 ①②③）。

分辨率限制（不掩饰）：`estimate` 实测 ≈0.6–1.1 s/条、每条返回后再花 ≈0.2–0.4 s 才抓到那一屏（两者
本件都打出来）⇒ 短于一个 estimate 周期的过渡**看不见**；fidus 那次抓屏在 estimate 调用内部的哪一刻
发生**不可知**，所以本件分不开"fidus 内部时域滤波"与"fidus 取到的帧滞后 ≤1 s"——但两者都在 fidus
进程内，均已排除合成器/屏幕。

预检闸门（settle5/6/7 那几轮的教训，见 §12j）：**先**用尺子 A 确认图案在请求位，**再**让 fidus 校准。
反例是真实存在的：layer 没上屏时 `calibrate_once` 仍返回 `rms_residual_px=0.000` 的"成功"，随后每条
`estimate` 都 `FidusTargetLost`（本机 7 轮里 2 轮命中，重建 layer 即恢复）⇒ 校准阶段不校验可见性，
先校后查会把整轮变成失败路径噪声。

用法（真机，会移动一个不透明 layer、每轮抓屏数秒）：
```
WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \
  .venv/bin/python scripts/fidus_positioning_probe/probe_settle_convergence.py \
      --moves 3 --col 4
```
退出码：0=跑完（结论看三行 VERDICT）；1=探针/环境自身不可用（预检闸门未过即属此类）。

失效边界：本机 niri + 桥接层 layer（**不是** Qt 窗）⇒ 只主张"layer 位移后 fidus 读数形态"；
单输出 scale=1.0；fidus 侧参照系仍是 self-consistency（niri 不回报 layer 几何，共存件 §6.4-①），
但尺子 A 是**绝对**基准（grim 像素 + NCC），所以本件**能**判落点误差落在谁头上。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

from PyQt5.QtCore import QEventLoop  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

import fidus  # noqa: E402
import probe_a10_geometry as A10  # noqa: E402  尺子 A：grim + 自带 NCC
import probe_h1_coexistence as H1  # noqa: E402  复用同一拓扑：图案/门面/HostWindow

# 本件此前**不钉锚点**——同族四把尺子（h4/h5/h6/hygiene）都钉。2026-09-30 做跨轮 A/B 时才暴露：
# 同一份日志无法自证是哪只轮子跑的，只能靠文件名外部约定 ⇒ 补这一格。换轮未记账会在此响。
ANCHOR = "v0.1.0-beta.2"

DX, DY = 180, -120
INPLACE_PX = 1.5    # 屏幕「到位」判据（NCC 峰值定位是整数像素，留 1.5 px 量化余量）
SCREEN_OK_PX = 3.0  # 尺子 A 认定「屏幕侧无过渡」的上限
FIDUS_FIRST_PX = 10.0  # 尺子 B 首条算「明显偏」的下限
# 失控（--recover 要判的东西）得先有可打印的定义，否则"挽回"无从谈起。
# 2026-09-30 两轮补判据后的最终形态：**主判据只有一条——进了 conf 门而末条又不在期望位**。
# 距离倍数（GAIN）从主判据**降级成形态标签**，原因见下两段。
RUNAWAY_CONF = 0.001  # conf 上限：高于它就不算失控（健康臂 conf 恒等于逐注册常数 ceiling，进不了这道门）
# ① 只看 GAIN 会漏**冻结**态：amp4 实测 773 px 单跳后四条读数全同（互差 0.00、conf 0.0000），
#    末条离期望 772.53 而幅值 773.00 ⇒ 比值 0.99939，GAIN 只要 ≥1.0 就必然漏。
# ② 补了冻结之后仍漏**中间态**（|v| 小而非零的慢速外推）：本件 `runaway_mode` 原先的两个型都是
#    **端点检查**——一个查距离比、一个查步长——而端点之间那段两头不靠：步长 >ADJ_TOL 不算冻结，
#    距离比 <GAIN 不算外推 ⇒ 打印「未失控」。这不是假想情形：`fidus-self-window-positioning`
#    §12e-4 明记 conf==0 有两形，其中「逐次外推漂移 `1223→1240`、`1679→1693→1710`，每步 ~17 px」
#    就是中间态的实测；取 amp4 几何按 17 px/步走，4 条窗口内比值落在 [0.93, 1.07] ⇒ 两型都不命中。
# ③ 端点检查之所以多余：conf==0 的坐标按 fidus 语义是**标注为信念、非测量**（coasting），
#    它与"离得多远""跑得多快"无关。故判据收到一条上，形态只作标签供读日志，不再参与判定。
RUNAWAY_GAIN = 2.0    # 形态标签「发散」的下界：末条离期望 ≥ 该倍数 × 位移幅值（不参与是否失控的判定）
ADJ_TOL_PX = 2.0      # 三个用途共用这一个数：互差判稳 / 形态「冻结」的不动界 / 形态「慢速外推」的逼近容差
SETTLED_PX = 2.0      # 「离期望 ≤ 它」= 读数到位。与 ADJ_TOL_PX 同值而**含义不同**，故分两个名字
RECOVER_OK_PX = 5.0   # 重注册后首条离期望的上限（此时屏幕已稳，比 2 px 略宽留量化余量）
NAN = float("nan")


def fact(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


def pump(app, ms: int) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents(QEventLoop.AllEvents, 20)


def dev(pt, center) -> float:
    """平面距离（px）。pt/center 均为 (x, y)。"""
    return float(np.hypot(pt[0] - center[0], pt[1] - center[1]))


def fnum(v, spec="{:.2f}", dash="-") -> str:
    """None → 短横：抓屏失败不是 0，不能静默当成功读数（I3 的口径）。"""
    return dash if v is None else spec.format(v)


def xy(pt) -> str:
    """(x, y) → 两个 9 位定宽数；None 时两格都是短横。"""
    return (fnum(None if pt is None else pt[0], "{:9.2f}") + " "
            + fnum(None if pt is None else pt[1], "{:9.2f}"))


def med(vals):
    got = [v for v in vals if v is not None and v == v]
    return float(np.median(got)) if got else NAN


def screen_shot(tag: str, pattern, center, gscale) -> dict:
    """抓一屏并定位，返回 {d, peak, pt}；抓屏/匹配失败时 d=None。"""
    shot = A10.capture(tag)
    m = A10.measure(shot, pattern, [gscale]) if shot is not None else None
    if not m or m.get("cx") is None:
        return {"d": None, "peak": None, "pt": None}
    pt = (float(m["cx"]), float(m["cy"]))
    return {"d": dev(pt, center), "peak": float(m["peak"]), "pt": pt}


def read_column(eng, pattern, center, n, t_origin, gscale, tag) -> list:
    """串行取 n 条 estimate，**每条返回后立刻抓一屏** ⇒ 两把尺子在同一时刻并排。

    时刻一律相对 t_origin（= set_position 前一刻）。`t` 是读数**返回**的时刻；抓屏在 `t` 之后再花
    `lag` 秒才完成 ⇒ 该条抓屏实测的是 `t_scr = t + lag` 的屏幕。fidus 内部真正取样在哪一刻**不可知**
    （在它自己那次抓屏里），这是本件的分辨率下限，不是屏幕侧的问题。
    """
    rows = []
    for k in range(n):
        try:
            r = eng.estimate()
            read = (float(r[0]), float(r[1]), float(r[2]))
            err_name = ""
        except BaseException as exc:  # 中途 TargetLost：记成不可用臂，不让它掀掉整轮
            read, err_name = None, f"RAISED {type(exc).__name__}"
        t_fid = time.perf_counter() - t_origin
        scr = screen_shot(f"{tag}_k{k}", pattern, center, gscale)
        t_scr = time.perf_counter() - t_origin
        rows.append({"t": t_fid, "t_scr": t_scr, "lag": t_scr - t_fid, "err": err_name,
                     "pt": None if read is None else (read[0], read[1]),
                     "conf": -1.0 if read is None else read[2],
                     "d_f": NAN if read is None else dev((read[0], read[1]), center),
                     "scr": scr})
    return rows


def settle_index(rows, tol=SETTLED_PX):
    """首条「离期望 ≤tol 且此后再不回退」的下标；没有则 None。"""
    for i, row in enumerate(rows):
        if row["d_f"] <= tol and all(v["d_f"] <= tol for v in rows[i:]):
            return i
    return None


def along_proj(rows, move_vec, center):
    """沿运动方向的一维投影：<0 = 尚未到位，>0 = 已越过目标位。"""
    n = float(np.hypot(move_vec[0], move_vec[1]))
    if n < 1.0:
        return None
    ux, uy = move_vec[0] / n, move_vec[1] / n
    return [NAN if row["pt"] is None
            else float((row["pt"][0] - center[0]) * ux + (row["pt"][1] - center[1]) * uy)
            for row in rows]


def runaway_mode(arm) -> str:
    """末条 conf 塌到 `RUNAWAY_CONF` 以下**且**末条不在期望位 ⇒ 失控。返回值只是**形态标签**，不参与判定。

    主判据一条就够：conf==0 的坐标按 fidus 语义是"标注为信念、非测量"，与它离得多远、跑得多快无关；
    而健康臂的 conf 恒等于逐注册常数 `confidence_ceiling`（amp1/amp2 实测 `[0.1067]*4`），
    **根本进不了这道门** ⇒ 不需要再用距离倍数去区分"发散"与"衰减中"。判据的两轮演变与
    为什么端点检查不够，见常量处 ①②③。

    形态标签（读数自身在做什么，供读日志与归因用）：
    * `冻结`：末两条互差 ≤ `ADJ_TOL_PX`——停在错处不动（amp4 实测形，|v|≈0 的端点）。
    * `慢速外推`：互差 > `ADJ_TOL_PX` 而离期望没缩小（或无从比较）——端点之间的**一般情形**。
      这一格就是本函数原先漏掉的：互差 >2 不算冻结、距离比 <2 不算外推，两型两头不靠 ⇒ 打印「未失控」。
    * `逼近中`：离期望在逐条缩小——coasting 恰好朝正确方向挪。**仍判失控**，因为它到没到位都不是测量。
    * `形态不可判`：只有一条读数、或前一条抛异常 ⇒ 趋势无从比较。判定照走（主判据只看末条），
      但**不凭空给它一个趋势标签**。
    * `+发散`：末条离期望 ≥ `RUNAWAY_GAIN` × 幅值——可叠加在后三者之上，只说明"已经跑到两倍开外"。

    取**末条**而不是全列 max：位移后首条是锁定读数（conf == ceiling），conf 塌到 0 从第 2 条起
    ——按全列 max 判会把每一个真位移臂都判成"未失控"（run2 实测踩到，判据自身被否证一次）。

    返回 `''` 的三种情形：末条 conf 未塌（健康）、末条正好在期望位（信念碰对了，无需挽回）、
    末条抛异常（无距离可判；该臂已在逐臂行与 `dropped` 里明写"不参与判定"，不在这里充数）。
    """
    last = arm["rows"][-1]
    if not last["conf"] <= RUNAWAY_CONF:
        return ""
    if last["d_f"] != last["d_f"]:                    # 末条抛异常 ⇒ 无距离可判（处置见上一段）
        return ""
    if last["d_f"] <= SETTLED_PX:
        return ""                                     # 信念恰好落在期望位：仍不可信，但不需要挽回
    adj = arm["adj"] or []                     # 同一把尺子：相邻两读数的平面距离，不是距离之差
    devs = [row["d_f"] for row in arm["rows"]]
    step = adj[-1] if adj else NAN
    prev_dev = devs[-2] if len(devs) >= 2 else NAN
    if step == step and step <= ADJ_TOL_PX:
        mode = "冻结"
    elif step != step and prev_dev != prev_dev:
        mode = "形态不可判"                # 只有一条读数、或前一条抛异常 ⇒ 无趋势可比（判定不受影响）
    elif prev_dev != prev_dev or last["d_f"] >= prev_dev - ADJ_TOL_PX:
        mode = "慢速外推"
    else:
        mode = "逼近中"
    if last["d_f"] >= RUNAWAY_GAIN * arm["move_px"]:
        mode += "+发散"
    return mode


def adj_settle(adj, tol=ADJ_TOL_PX):
    """最早的第 j 对满足 `|Δ_j| ≤ tol` **且其后各对皆 ≤ tol** ⇒ 敢说"稳"至少要读到第几 *条*。

    fidus 文档的判据是"相邻两条互差不超过容差"，而该判据**是否够用取决于手里有几条**：
    本函数就是把"要几条"这件事变成一个可打印的数，而不是停在"读 6 条"这个未条件化的常数上。
    None = 这一列里从未稳过（列数不够或素材本身在抖）。
    """
    good = [a == a for a in adj]
    for j, a in enumerate(adj):
        if good[j] and a <= tol and all(good[k] and adj[k] <= tol for k in range(j + 1, len(adj))):
            return j + 2
    return None


def adj_settle_both(adj, devs, adj_tol=ADJ_TOL_PX, settle_px=SETTLED_PX):
    """`adj_settle` 加上"读数得真的在期望位"那一半：第 j 对及其后**各对互差 ≤ adj_tol 且各条离期望 ≤ settle_px**。

    与 `adj_settle` 的差就是这条判据漏掉的那一半：2026-09-30 amp4 冻结态互差 0.00，旧函数
    第 2 条就报"稳"，而那四条离期望 772.53 px ⇒ 单靠互差判稳会把"停在错处"读成"稳定在目标"。
    两者都满足才敢说稳——这条与 `--recover` 的 `冻结` 型是同一个事实的两面。
    None = 这一列里从未"又稳又到位"。
    """
    ok = [a == a and a <= adj_tol for a in adj]
    near = [v == v and v <= settle_px for v in devs]
    for j in range(len(adj)):
        if all(ok[k] and near[k] and near[k + 1] for k in range(j, len(adj))):
            return j + 2
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--moves", type=int, default=3, help="真位移臂次数")
    ap.add_argument("--col", type=int, default=4, help="每臂 estimate 条数")
    ap.add_argument("--gscale", type=float, default=1.0, help="尺子 A 的显示 scale（本机 1.0）")
    ap.add_argument("--no-control", action="store_true", help="跳过「原地 set_position」对照臂")
    ap.add_argument("--tries", type=int, default=4, help="fidus 闸门重试次数")
    ap.add_argument("--commits", type=int, default=3, help="首帧不可见时最多再提交几帧")
    # 下面两条是为回填 fidus 件 `meapet-settle-count-alignment` 的反证行而加：
    # 该件要我方证明"读 6 条"是不是**普适常数**，还是要随**位移幅值**与**首枪间隔**条件化。
    # 不加这两个旋钮就只能重复同一组 (幅值, dt)，那等于把"常数"当成未经检验的前提。
    ap.add_argument("--amp", type=float, default=1.0,
                    help=f"位移幅值倍率（基准 hypot({DX},{DY})="
                         f"{np.hypot(DX, DY):.0f} px）")
    ap.add_argument("--gap", type=float, default=0.0,
                    help="set_position 后**先空等**几秒再取第一枪（首枪间隔）")
    ap.add_argument("--recover", action="store_true",
                    help="末尾加一臂：不移动，只**重注册**同一模板再读若干条 ⇒ 看"
                         "大位移失控（conf 恒 0、读数外推或冻结）能否靠重注册撤销")
    args = ap.parse_args()

    print("=== 位移后的两把尺子（grim+NCC 独立实测 vs fidus estimate）===")
    app = QApplication(sys.argv)
    geo = QApplication.primaryScreen().availableGeometry()
    px = max(40, min(geo.width() - H1.PATTERN_W - 40, 48))
    py = max(40, min(geo.height() - H1.PATTERN_H - 40, 300))
    cx0, cy0 = px + H1.PATTERN_W / 2, py + H1.PATTERN_H / 2
    fact("图案请求位", f"({px},{py}) 尺寸 {H1.PATTERN_W}x{H1.PATTERN_H}"
         f" ⇒ 期望中心 ({cx0:.0f},{cy0:.0f})")
    anchor = getattr(fidus, "__git_commit__", None)
    anchor_ok = anchor == ANCHOR
    fact("fidus.__git_commit__", f"{anchor!r} 期望 {ANCHOR!r} ⇒ "
         + ("命中" if anchor_ok else "✗ 换轮未记账：本日志的读数不可归因于登记的那一轮件"))

    host = H1.HostWindow()
    host.move(px, py)
    host.show()
    pump(app, 600)

    backend = H1.FACADE.get_backend()
    pattern = np.ascontiguousarray(H1.make_pattern())
    qimg = H1.qimage_from(pattern)
    shim = None  # _shim 由 enable() 惰性创建 ⇒ 只能在 enable 之后取，不能提前（settle8 踩过）

    def bring_up() -> None:
        """立起 layer（门面 enable 已幂等，二次调用不泄漏孤儿 ctx：meapet 711102c）。"""
        nonlocal shim
        backend.enable(host, H1.PATTERN_W, H1.PATTERN_H, px, py)
        shim = backend._shim
        shim.layer_last_error.restype = ctypes.c_char_p
        backend.update_pixels(qimg)
        pump(app, 1200)

    def ensure_visible(tries: int):
        """用尺子 A 确认图案真在请求位；未见就**再提交一帧**。

        本机实测（diag_race，6/6 同进程复现 + 跨进程间歇 ≈1/3）：`enable` 后第一次
        `update_pixels` 可能停在「surface 已映射但屏幕上没有内容」的状态——niri 的 layers
        里能看到 `meapet/Overlay` 存在，抓屏却全黑（NCC 峰 0.14）；**再提交一帧**立刻 peak=1.0。
        shim 不导出任何 fd/dispatch 入口（只有 init/create/set_*/update/destroy）⇒ 宿主无法
        主动驱动它 flush。生产里宠物逐帧重绘所以自愈，静态目标（本探针）不放大的话就永久看不见。
        """
        for i in range(tries):
            scr = screen_shot(f"vis{i}", pattern, (cx0, cy0), args.gscale)
            if scr["d"] is not None and scr["d"] <= SCREEN_OK_PX:
                return scr, i
            fact("可见性", f"第 {i + 1}/{tries} 次未见（屏幕 {fnum(scr['d'])}px "
                 f"峰={fnum(scr['peak'], '{:.4f}')}）⇒ 再提交一帧")
            backend.update_pixels(qimg)
            pump(app, 1200)
        return None, tries

    bring_up()
    scr, extra = ensure_visible(args.commits)
    if scr is None:
        fact("判定", f"提交 {args.commits + 1} 帧仍不可见 ⇒ 本轮不可判，**不产出 VERDICT 行**")
        backend.destroy_context()
        return 1
    fact("可见性", f"OK（额外提交 {extra} 帧，屏幕 {scr['d']:.2f}px 峰={scr['peak']:.4f}）")

    eng = fidus.Fidus.build_wayland()
    fact("gate_status", eng.gate_status())
    eng.register_target(pattern, False)

    # ── fidus 侧闸门（§12g-4 那个坑的固化）：**先确认屏幕上有图案，再让 fidus 校准**。
    # 反例（settle5/settle7 实测）：图案不在屏上时 `calibrate_once` 仍返回 rms_residual_px=0.000 的
    # "成功"，随后每条 estimate 都 FidusTargetLost ⇒ 校准不校验可见性，先校后查会把整轮变成失败路径噪声。
    passed = False
    for attempt in range(args.tries):
        t_c = time.perf_counter()
        info = eng.calibrate_once()
        fact("calibrate_once", f"{time.perf_counter() - t_c:.2f}s {str(info)[:70]}")
        try:
            r = eng.estimate()
            conf = float(r[2])
            d = dev((float(r[0]), float(r[1])), (cx0, cy0))
            note = f"fidus {d:.1f}px conf={conf:.4f}"
        except BaseException as exc:  # TargetLost / PanicException 都不该掀掉整轮
            note = f"estimate 抛 {type(exc).__name__}"
            conf, d = -1.0, 999.0
        ok = conf > 0.0 and d < 25.0
        fact("fidus 闸门", f"{'ok' if ok else '未过'}（尝试 {attempt + 1}/{args.tries}：{note}；"
             f"gate={eng.gate_status()}）")
        if ok:
            passed = True
            break
        host.raise_()
        _, extra = ensure_visible(2)
        pump(app, 600)
    if not passed:
        fact("判定", f"fidus 闸门 {args.tries} 次未过 ⇒ 本轮不可判，**不产出 VERDICT 行**")
        backend.destroy_context()
        return 1

    # ── 各臂：对照臂（原地 set_position）+ 真位移臂
    arms: list = []

    def take(tag, tx, ty, frm, center):
        t_move = time.perf_counter()
        backend.set_position(tx, ty)
        if args.gap > 0:                       # 首枪间隔（fidus 件要求的第二个变量）
            pump(app, int(args.gap * 1000))
        shot0 = screen_shot(f"{tag}_t0", pattern, center, args.gscale)
        lag0 = time.perf_counter() - t_move
        rows = read_column(eng, pattern, center, args.col, t_move, args.gscale, tag)
        err = shim.layer_last_error()
        mv = (tx - frm[0], ty - frm[1])
        cand = [shot0["d"]] + [row["scr"]["d"] for row in rows]
        got = [v for v in cand if v is not None]
        last, prev = rows[-1], rows[-2]
        usable = all(row["pt"] is not None for row in rows)
        step = NAN if (last["pt"] is None or prev["pt"] is None) else dev(last["pt"], prev["pt"])
        pts = [row["pt"] for row in rows]
        adj = [NAN if (pts[k] is None or pts[k - 1] is None)
               else dev(pts[k], pts[k - 1]) for k in range(1, len(pts))]
        m = {
            "tag": tag, "req": (tx, ty), "frm": frm, "center": center, "shot0": shot0,
            "rows": rows, "err": err, "move_px": float(np.hypot(*mv)),
            "jump": rows[0]["d_f"], "real": float(np.hypot(*mv)) > 5.0, "usable": usable,
            "scr_max": max(got) if got else NAN,
            "settle": settle_index(rows),
            "last_dev": last["d_f"], "last_step": step,
            "along": along_proj(rows, mv, center),
            "adj": adj, "gap_med": med([rows[k]["t"] - rows[k - 1]["t"]
                                        for k in range(1, len(rows))]),
            "need_col": adj_settle(adj),
            "need_col2": adj_settle_both(adj, [row["d_f"] for row in rows]),
        }
        arms.append(m)
        print(f"\n[{tag}] 请求 {frm} → ({tx},{ty})（移动 {m['move_px']:.0f} px）"
              f" 期望中心 ({center[0]:.0f},{center[1]:.0f})  layer_err={err!r}")
        print("  fidus读数时刻t  fidus 读数     离期望     conf"
              "   屏采样时刻  屏幕实测      离期望      峰   采样耗时")
        s0 = xy(shot0["pt"])
        star0 = "*" if shot0["d"] is not None and shot0["d"] <= INPLACE_PX else " "
        print(f"      ≈0.00  (set_position 后第一枪)        -        -"
              f"     {lag0:7.2f} {s0} {fnum(shot0['d'], '{:9.2f}')}{star0} "
              f"{fnum(shot0['peak'], '{:7.4f}')}   {lag0:.2f}s")
        for row in rows:
            note = row["err"] or f"{row['pt'][0]:9.2f},{row['pt'][1]:9.2f}"
            sd = row["scr"]["d"]
            star = "*" if sd is not None and sd <= INPLACE_PX else " "
            print(f"  {row['t']:11.2f}  {note:>18} "
                  f"{fnum(row['d_f'], '{:9.2f}')} {row['conf']:8.4f}"
                  f"   {row['t_scr']:9.2f} {xy(row['scr']['pt'])} {fnum(sd, '{:9.2f}')}{star} "
                  f"{fnum(row['scr']['peak'], '{:7.4f}')}   {row['lag']:.2f}s")
        sidx = "未收敛" if m["settle"] is None else f"第 {m['settle'] + 1} 条"
        fact("首条离期望 / 屏幕整段最大 / 收敛于",
             f"{fnum(m['jump'])} / {fnum(m['scr_max'])} px / {sidx}"
             f"；末条离期望 {fnum(m['last_dev'])}、与倒数第二条互差 {fnum(m['last_step'])} px"
             + ("" if usable else " ⇒ **本臂有读数抛异常，不参与判定**"))
        if m["along"] is not None and usable:
            fact("  沿运动方向投影", " ".join(f"{v:+.1f}" for v in m["along"])
                 + f"（越过稳态位最多 {max(m['along']):+.1f} px）")
        fact("  相邻互差 Δ(px)", " ".join("—" if a != a else f"{a:.2f}" for a in m["adj"])
             + f"｜dt 中位 {m['gap_med']:.2f}s｜互差判据要读到 "
             + ("本列内从未稳" if m["need_col"] is None else f"{m['need_col']} 条")
             + "｜互差且离期望≤" + f"{SETTLED_PX:g}" + "px 要读到 "
             + ("本列内从未又稳又到位" if m["need_col2"] is None else f"{m['need_col2']} 条"))

    if not args.no_control:
        take("对照·原地set_position", px, py, (px, py), (cx0, cy0))
    cur = (px, py)
    for i in range(args.moves):
        # 交替落点，但**首臂必须是真位移**（早期版本首臂退化成 0 px ⇒ 白跑一臂还错标）
        if i % 2 == 0:
            tx = int(round(np.clip(px + DX * args.amp, 20, geo.width() - H1.PATTERN_W - 20)))
            ty = int(round(np.clip(py + DY * args.amp, 20, geo.height() - H1.PATTERN_H - 20)))
        else:
            tx, ty = px, py
        take(f"位移#{i}", tx, ty, cur, (tx + H1.PATTERN_W / 2, ty + H1.PATTERN_H / 2))
        cur = (tx, ty)

    # ── 恢复臂（--recover）：大位移失控后**不移动**、只重注册，看读数能否回到真位。
    # 为什么要单独一臂：失控若只能靠"把窗搬回原位"自愈，消费方就得等用户动作；
    # 若重注册即可撤销，契约才写得出"失控 ⇒ 在 tick 外重注册"这条**可执行**出口。
    # ── 恢复臂（--recover）：大位移失控后**不移动**、只重注册，看读数能否回到真位。
    # 为什么要单独一臂：失控若只能靠"把窗搬回原位"自愈，消费方就得等用户动作；
    # 若重注册即可撤销，契约才写得出"失控 ⇒ 在 tick 外重注册"这条**可执行**出口。
    # 拆两小臂而不是只跑一种：`register_target` 是否连带清掉 `calibrate_once` 建立的参照系映射
    # 我方**不知道**——只跑"仅重注册"，失败时分不清"滤波状态没清"还是"校准没了"；
    # 只跑"重注册+校准"，则可能把本来不必付的校准成本写进契约。两种都测，出口才写得出代价。
    rec = None
    if args.recover and not [m for m in arms if m["real"]]:
        fact("恢复臂", "跳过：--moves 0 时没有「失控前」的参照臂，本臂无意义")
    elif args.recover:
        pre = [m for m in arms if m["real"]][-1]
        ctr = pre["center"]
        pre_mode = runaway_mode(pre)
        pdev = [r["d_f"] for r in pre["rows"]]
        ptrend = ("—" if len(pdev) < 2 or pdev[-2] != pdev[-2] else f"{pdev[-2]:.2f}→") + \
                 ("—" if pdev[-1] != pdev[-1] else f"{pdev[-1]:.2f}")
        print(f"\n[恢复臂] 停在期望中心 ({ctr[0]:.0f},{ctr[1]:.0f})、不移动"
              f"｜上一臂 {pre['tag']} 末条离期望 {fnum(pre['last_dev'])} px"
              f"（其 conf 序列 {[round(r['conf'], 4) for r in pre['rows']]}）"
              f"｜末两条互差 {fnum(pre['adj'][-1] if pre['adj'] else None)} px"
              f"｜离期望 {ptrend} px"
              f"⇒ 按判据 "
              + (f"**算**失控（{pre_mode} 型）" if pre_mode else "**不算**失控"))
        recs = []
        for cal in (False, True):
            label = "重注册+校准" if cal else "仅重注册"
            t_r = time.perf_counter()
            eng.register_target(pattern, False)
            if cal:
                eng.calibrate_once()
            reg_ms = (time.perf_counter() - t_r) * 1000.0
            fact(label, f"{reg_ms:.1f} ms（模板 {H1.PATTERN_W}×{H1.PATTERN_H}，"
                        f"ceiling={eng.confidence_ceiling!r}）")
            rows = read_column(eng, pattern, ctr, args.col, t_r, args.gscale, f"rec{int(cal)}")
            for row in rows:
                note = row["err"] or f"{row['pt'][0]:9.2f},{row['pt'][1]:9.2f}"
                print(f"  {row['t']:11.2f}  {note:>18} {fnum(row['d_f'], '{:9.2f}')}"
                      f" {row['conf']:8.4f}   屏幕 {fnum(row['scr']['d'])}px"
                      f" 峰={fnum(row['scr']['peak'], '{:.4f}')}")
            recs.append({"label": label, "ms": reg_ms, "rows": rows})
        rec = {"pre": pre, "mode": pre_mode, "arms": recs}

    real = [m for m in arms if m["real"] and m["usable"]]
    ctrl = [m for m in arms if not m["real"]]
    dropped = [m["tag"] for m in arms if m["real"] and not m["usable"]]

    # ── 判定
    print("\n[VERDICT]")
    print(f"VERDICT-ANCHOR : {anchor!r} vs 登记 {ANCHOR!r} ⇒ "
          + ("同一轮件，读数可归因" if anchor_ok
             else "✗ 换轮未记账：下面的『过渡在哪一侧』不可归因于登记的那一轮件"))
    if dropped:
        print(f"EXCLUDED         : {dropped} ⇒ 该臂有 estimate 抛异常，不参与判定")
    if not real:
        fact("判定", "无可用真位移臂 ⇒ 不产出 VERDICT")
    else:
        worst = max(m["scr_max"] for m in real)
        scr_med = med([m["scr_max"] for m in real])
        f_first = med([m["jump"] for m in real])
        print(f"VERDICT-SCREEN   : 真位移臂整段屏幕实测离期望最大 {worst:.2f} px"
              f"（逐臂 {[round(m['scr_max'], 2) for m in real]}，中位 {scr_med:.2f}；带 * 即 ≤"
              f"{INPLACE_PX} px 视为到位）⇒ "
              + ("屏幕**自始至终在目标位**：屏幕侧没有过渡。" if worst <= SCREEN_OK_PX else
                 "屏幕自身即存在过渡 ⇒ 不能把 fidus 的偏差全记到 fidus 头上。"))
        locus_fidus = worst <= SCREEN_OK_PX and f_first > FIDUS_FIRST_PX
        print(f"VERDICT-LOCUS    : fidus 首条偏离中位 {f_first:.2f} px vs 屏幕最大 {worst:.2f} px ⇒ "
              + ("过渡在 **fidus 进程内**（其内部时域滤波，或其那次抓屏取到旧帧——本件分不开这两者，"
                 "但都排除合成器/屏幕）。" if locus_fidus else
                 "不满足「屏稳而 fidus 偏」⇒ 本件不支持 fidus 内部过渡这一归因。"))
        ok_t = [m for m in real if m["last_dev"] <= SETTLED_PX and m["last_step"] <= ADJ_TOL_PX]
        print(f"VERDICT-CONSUME  : 末条离期望 ≤{SETTLED_PX:g} px 且与倒数第二条互差 ≤{ADJ_TOL_PX:g} px"
              f" 的真位移臂 "
              f"{len(ok_t)}/{len(real)}"
              f"（逐臂末条 {[round(m['last_dev'], 2) for m in real]}、步长 "
              f"{[round(m['last_step'], 2) for m in real]}）⇒ "
              + (f"时间基规则够用：位移后再读 {args.col} 条、取末条即可。"
                 if len(ok_t) == len(real) else
                 "时间基不保证 ⇒ 消费方须按读数自身判稳（如相邻两条互差收敛）。"))
        overs = [m for m in real if m["along"] is not None]
        if overs:
            hits = sum(1 for m in overs
                       if m["along"][0] < -2.0 and any(v > 2.0 for v in m["along"][1:]))
            mx = max(max(m["along"]) for m in overs)
            print(f"OBS-OVERSHOOT    : {hits}/{len(overs)} 条真位移臂在逼近中越过稳态位"
                  f"（沿运动方向最大越界 {mx:+.1f} px）⇒ 形态是**阻尼收敛**，不是单向逼近。")
        # ── VERDICT-ADJ：fidus 件 `meapet-settle-count-alignment` 的反证行——"6 条"是不是普适常数
        need = [m["need_col"] for m in real]      # fidus 文档那条判据的原样：只看相邻互差
        need2 = [m["need_col2"] for m in real]    # 加上「末条得真的在期望位」那一半
        fake = [f"{m['tag']}（末条 {m['last_dev']:.2f} px）" for m in real
                if m["need_col"] is not None and m["last_dev"] > SETTLED_PX]
        print("VERDICT-ADJ      : 幅值倍率 amp={:.2f} 首枪间隔 {:.1f}s 列数上限 {} ⇒ 各臂按"
              "「相邻互差 ≤{:g} px」判稳**所需条数** {}；再并上「离期望 ≤{:g} px」后 "
              "{}".format(args.amp, args.gap, args.col, ADJ_TOL_PX, need, SETTLED_PX, need2))
        if fake:
            print(f"                   ✗ 假稳 {len(fake)}/{len(real)} 臂：{fake} 按互差已判到「稳」"
                  f"而末条离期望仍 >{SETTLED_PX:g} px ⇒「相邻互差」单用会把「停在错处不动」读成"
                  "「稳定在目标」，判稳必须并上离期望条件")
        print("                   逐臂（幅值 px / dt 中位 s / 仅互差条数 / 互差且到位条数）: " + "；".join(
            f"{m['move_px']:.0f}/{m['gap_med']:.2f}/"
            + (">" + str(args.col) if m["need_col"] is None else str(m["need_col"])) + "/"
            + (">" + str(args.col) if m["need_col2"] is None else str(m["need_col2"]))
            for m in real))
        uniq = {n for n in need2}
        if uniq == {None}:
            steps = [round(m["last_step"], 2) for m in real]
            devs = [round(m["last_dev"], 2) for m in real]
            stuck = [i for i, m in enumerate(real) if m["last_step"] <= ADJ_TOL_PX
                     and m["last_dev"] > SETTLED_PX]
            why = ("读数**停在错处**（对上失控/假稳行看）" if len(stuck) == len(real) else
                   "读数**仍在移动**（互差 >{:g} px）".format(ADJ_TOL_PX) if not stuck else
                   f"{len(real) - len(stuck)} 臂仍在移动、{len(stuck)} 臂停在错处")
            print(f"                   ⇒ 本组各臂在 {args.col} 条内**从未**「又稳又到位」⇒ 瓶颈不是条数："
                  f"{why}（逐臂末两条互差 {steps}、末条离期望 {devs}）"
                  + ("；加 --col 才能定位拐点" if len(stuck) < len(real) else ""))
        else:
            print("                   ⇒ " + ("本组内各臂所需条数**相同** ⇒ 在该幅值该间隔下"
                                             "条数看不出对幅值的依赖，须换 amp 重跑"
                                             if len(uniq) == 1 else
                                             "所需条数在组内就不齐 ⇒ 「读 6 条」是**有条件**的经验值，"
                                             "契约里必须写成「幅值 × 首枪间隔」的函数或留余量")
                  + ("；本组的差异由**停在错处**的臂驱动（见上假稳行），不是同一收敛过程的快慢"
                     if fake else ""))
        if ctrl:
            c_jump = med([m["jump"] for m in ctrl])
            print(f"OBS-CONTROL      : 原地 set_position 臂首条离期望 {c_jump:.2f} px、"
                  f"屏幕最大 {max(m['scr_max'] for m in ctrl):.2f} px ⇒ "
                  + ("调用本身不引入过渡（前作复现）。" if c_jump <= 2.0 else
                     "连「原地不动」的 set_position 都引入偏差 ⇒ 归因要重新看。"))
    if rec is not None:
        pre = rec["pre"]
        zc = sum(1 for r in pre["rows"] if r["conf"] <= RUNAWAY_CONF)
        padj = pre["adj"]
        pdv = [r["d_f"] for r in pre["rows"]]
        ptr = ("—" if len(pdv) < 2 or pdv[-2] != pdv[-2] else f"{pdv[-2]:.2f}→") + \
              ("—" if pdv[-1] != pdv[-1] else f"{pdv[-1]:.2f}")
        print(f"VERDICT-RECOVER  : 上一臂 {pre['tag']}（幅值 {pre['move_px']:.0f} px，"
              f"conf ≤ {RUNAWAY_CONF:g} 的条数 {zc}/{len(pre['rows'])}，末条 "
              f"{fnum(pre['rows'][-1]['d_f'])} px、末条 conf {pre['rows'][-1]['conf']:.4f}、"
              f"末两条互差 {fnum(padj[-1] if padj else None)} px、离期望 {ptr} px）"
              f"⇒ 按「末条 conf ≤ {RUNAWAY_CONF:g} 且末条离期望 > {SETTLED_PX:g} px」判为 "
              + (f"**失控（{rec['mode']} 型）**" if rec["mode"] else "**未失控**"))
        print(f"                   形态标签只描述读数在做什么，不参与判定："
              f"互差 ≤{ADJ_TOL_PX:g} px = 冻结；否则离期望没缩小 ≥{ADJ_TOL_PX:g} px = 慢速外推、"
              f"在缩小 = 逼近中、无趋势可比 = 形态不可判；离期望 ≥{RUNAWAY_GAIN:g}× 幅值再并记 +发散"
              f"（旧判据取其中两个**端点**做判定，端点之间的慢速外推会打印「未失控」，2026-09-30 收口）")
        for sub in rec["arms"]:
            dp = [r["d_f"] for r in sub["rows"]]
            sp = [r["scr"]["d"] for r in sub["rows"] if r["scr"]["d"] is not None]
            ok_at = [i for i, v in enumerate(dp) if v == v and v <= RECOVER_OK_PX]
            print(f"  · {sub['label']}（{sub['ms']:.1f} ms）⇒ 离期望 "
                  + " ".join("—" if v != v else f"{v:.2f}" for v in dp)
                  + " px｜conf " + " ".join(f"{r['conf']:.4f}" for r in sub["rows"])
                  + f"｜同刻屏幕侧最大 {fnum(max(sp) if sp else None)} px")
            if sp and max(sp) > SCREEN_OK_PX:
                tail = (f"屏幕本身不在期望位（>{SCREEN_OK_PX:g} px）⇒ 归因不成立，"
                        "先修可见性/位姿再看 fidus")
            elif not rec["mode"]:
                tail = "上一臂不算失控 ⇒ 本行只说明该动作**是否打断正常跟踪**，不支撑出口结论"
            elif not ok_at:
                tail = (f"{len(dp)} 条**无一**回到 ≤ {RECOVER_OK_PX:g} px ⇒ 该动作**不能**撤销失控")
            else:
                tail = (f"第 {ok_at[0] + 1} 条即回到 ≤ {RECOVER_OK_PX:g} px ⇒ 该动作**能**撤销失控"
                        f"（代价 {sub['ms']:.1f} ms，须落在 tick 外）")
            print("      ⇒ " + tail)
        can = [s["label"] for s in rec["arms"]
               if rec["mode"] and any(r["d_f"] == r["d_f"] and r["d_f"] <= RECOVER_OK_PX
                                      for r in s["rows"])
               and not any(r["scr"]["d"] is not None and r["scr"]["d"] > SCREEN_OK_PX
                           for r in s["rows"])]
        if rec["mode"]:
            best = min((s for s in rec["arms"] if s["label"] in can), key=lambda s: s["ms"])
            print("      ⇒ 出口判定："
                  + (f"{'、'.join(can)} 均可撤销失控 ⇒ 取**最便宜**的那个：{best['label']}"
                     f" {best['ms']:.1f} ms（贵的一档 {max(s['ms'] for s in rec['arms']):.0f} ms "
                     f"作为「必须附带的动作」的成本下限写进契约）；两者都远超 2 ms tick ⇒ "
                     f"必须落在 GUI tick 外"
                     if can else
                     "两种动作都不能撤销 ⇒ 契约的出口只能是「放弃该素材路径」或「把窗搬回原位」，"
                     "**不得**承诺 tick 外重注册可恢复"))
        else:
            print("      ⇒ 出口判定：本组未复现失控 ⇒ 不产出出口结论，须加大 --amp 重跑")
    gaps = [b["t"] - a["t"] for m in arms for a, b in zip(m["rows"], m["rows"][1:])]
    lags = [row["lag"] for m in arms for row in m["rows"]]
    print(f"  分辨率限制重申: estimate 相邻间隔中位 {med(gaps):.2f} s、单条抓屏耗时最大 "
          f"{(max(lags) if lags else NAN):.2f} s ⇒ 短于一个 estimate 间隔的过渡**看不见**；"
          f"fidus 那次抓屏在它自己调用内部的哪一刻发生**不可知** ⇒ 本件分不开「内部时域滤波」与"
          f"「取到旧帧」，但两者都在 fidus 进程内。layer 位移 ≠ Qt 窗位移；单输出 "
          f"scale={args.gscale}；fidus 参照系 self-consistency（I3），尺子 A 为绝对基准。")

    backend.destroy_context()
    pump(app, 200)
    return 0


if __name__ == "__main__":
    sys.exit(main())
