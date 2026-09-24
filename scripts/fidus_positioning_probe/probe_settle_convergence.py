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

判据（三行，各自独立，不合并）：
* `VERDICT-SCREEN`：真位移臂整段（含紧随 `set_position` 的第一枪）屏幕实测离期望中心的**最大**偏差。
  ≤3 px ⇒ 屏幕自始至终在目标位，屏幕侧没有过渡可言。
* `VERDICT-LOCUS`：屏幕侧无过渡、而 fidus 首条仍偏 >10 px ⇒ 过渡在 **fidus 内部**（它的时域过程），
  不是"读到了还没搬完的屏幕"。
* `VERDICT-CONSUME`：末条离期望 ≤2 px 且与倒数第二条互差 ≤2 px ⇒ "位移后多读几条、取末条"这一
  **时间基**消费规则成立；否则消费方必须按读数自身判稳（等多少条都不保证）。

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

DX, DY = 180, -120
INPLACE_PX = 1.5    # 屏幕「到位」判据（NCC 峰值定位是整数像素，留 1.5 px 量化余量）
SCREEN_OK_PX = 3.0  # 尺子 A 认定「屏幕侧无过渡」的上限
FIDUS_FIRST_PX = 10.0  # 尺子 B 首条算「明显偏」的下限
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


def settle_index(rows, tol=2.0):
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--moves", type=int, default=3, help="真位移臂次数")
    ap.add_argument("--col", type=int, default=4, help="每臂 estimate 条数")
    ap.add_argument("--gscale", type=float, default=1.0, help="尺子 A 的显示 scale（本机 1.0）")
    ap.add_argument("--no-control", action="store_true", help="跳过「原地 set_position」对照臂")
    ap.add_argument("--tries", type=int, default=4, help="fidus 闸门重试次数")
    ap.add_argument("--commits", type=int, default=3, help="首帧不可见时最多再提交几帧")
    args = ap.parse_args()

    print("=== 位移后的两把尺子（grim+NCC 独立实测 vs fidus estimate）===")
    app = QApplication(sys.argv)
    geo = QApplication.primaryScreen().availableGeometry()
    px = max(40, min(geo.width() - H1.PATTERN_W - 40, 48))
    py = max(40, min(geo.height() - H1.PATTERN_H - 40, 300))
    cx0, cy0 = px + H1.PATTERN_W / 2, py + H1.PATTERN_H / 2
    fact("图案请求位", f"({px},{py}) 尺寸 {H1.PATTERN_W}x{H1.PATTERN_H}"
         f" ⇒ 期望中心 ({cx0:.0f},{cy0:.0f})")

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
        m = {
            "tag": tag, "req": (tx, ty), "frm": frm, "center": center, "shot0": shot0,
            "rows": rows, "err": err, "move_px": float(np.hypot(*mv)),
            "jump": rows[0]["d_f"], "real": float(np.hypot(*mv)) > 5.0, "usable": usable,
            "scr_max": max(got) if got else NAN,
            "settle": settle_index(rows),
            "last_dev": last["d_f"], "last_step": step,
            "along": along_proj(rows, mv, center),
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

    if not args.no_control:
        take("对照·原地set_position", px, py, (px, py), (cx0, cy0))
    cur = (px, py)
    for i in range(args.moves):
        # 交替落点，但**首臂必须是真位移**（早期版本首臂退化成 0 px ⇒ 白跑一臂还错标）
        tx, ty = (px + DX, py + DY) if i % 2 == 0 else (px, py)
        take(f"位移#{i}", tx, ty, cur, (tx + H1.PATTERN_W / 2, ty + H1.PATTERN_H / 2))
        cur = (tx, ty)

    real = [m for m in arms if m["real"] and m["usable"]]
    ctrl = [m for m in arms if not m["real"]]
    dropped = [m["tag"] for m in arms if m["real"] and not m["usable"]]

    # ── 判定
    print("\n[VERDICT]")
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
        ok_t = [m for m in real if m["last_dev"] <= 2.0 and m["last_step"] <= 2.0]
        print(f"VERDICT-CONSUME  : 末条离期望 ≤2 px 且与倒数第二条互差 ≤2 px 的真位移臂 "
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
        if ctrl:
            c_jump = med([m["jump"] for m in ctrl])
            print(f"OBS-CONTROL      : 原地 set_position 臂首条离期望 {c_jump:.2f} px、"
                  f"屏幕最大 {max(m['scr_max'] for m in ctrl):.2f} px ⇒ "
                  + ("调用本身不引入过渡（前作复现）。" if c_jump <= 2.0 else
                     "连「原地不动」的 set_position 都引入偏差 ⇒ 归因要重新看。"))
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
