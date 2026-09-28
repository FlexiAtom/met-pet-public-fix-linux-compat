#!/usr/bin/env python3
"""H21 · 同一贴片跑两遍：喂 `initial_center` 与不喂，**首条读数逐位比对**（真机）

出处：fidus 入站广播 2026-09-26T17:21:41（`notices.md`，投递件
`projects/fidus/pool/fidus-fused-prior-wired.md` §6）。贵方要的不是"两臂都锁定"，
而是把 **「喂先验与不喂的首条读数逐位相同」** 升格成我方真机上的**二值断言**：
同一贴片、同一 surface 几何，一遍带 `initial_center`、一遍省略，两条**首条** `estimate`
读数必须**逐位**（不只是四舍五入后）相等。

我方此前只回投过**跨轮**的逐位回归（H10 一行不改在 `-58` 复跑，四臂位置数与两条 conf
逐位同 `-53`，见 `working/fidus-switch-positioning.md` §5.7.1 与回执第二轮）。
那一条**推不出**贵方这一条：跨轮比的是"不喂那一支没被改坏"，本条比的是
"喂与不喂在同一支轮子上给出同一个数"。二者是不同的判据 ⇒ 本件补的就是这一格。

四件事，各自独立：
* **CHANNEL（前置，不成立则本件不判）** —— 640² 那一翼：**不喂先验必抛** `FidusTargetLost`、
  **喂先验必锁定**。它同时是"不喂那一支真的走整帧、没沾上一次锁定的残留信念"的机核证据
  ⇒ 跑在比对**之前**，并在一轮结束时**复跑一次**：若末次仍抛，`register_target(frame, False)`
  确实每次重置 ⇒ 中间那几臂的"不喂"是干净的整帧路径。
* **BITWISE（承重断言）** —— 对每个 `--edges` 档，同一 mount 上跑两遍顺序
  （不喂→喂、喂→不喂），每遍比较两条**首条**读数：位置用 `==`，conf 用
  `struct.pack("<d", x)` 的**字节**比。顺带报"是否等于 ceiling"，但**不作门槛**。
* **COMPARABLE（诚实闸）** —— 每条 pass 前抓整屏并 sha256。两遍之间整屏字节变了（别的窗口
  动了、指针进画了）⇒ 该对判"不可比"，**不算通过也不算否证**，如实写出来。
* **ORDER** —— 两个顺序各给一条 VERDICT；若只有其中一个顺序逐位相同，那是**顺序敏感**，
  本身就是对贵方那条断言的否证，必须点名。

用法
----
    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h21_prior_vs_noprior.py
可选 `--edges 600,633`（默认 600）、`--out-long 1366`。
副作用：真实桌面上挂/拆 layer 浮层（640×640 不透明方块）、每臂多次整屏抓取、一次 `calibrate_once`。
退出码：0 = 跑到出口（结论再否定也是 0）；1 = 量具/环境不可用（内容没上屏、注册被拒、校准抛）。
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import time

import numpy as np
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fidus  # noqa: E402
import probe_a10_geometry as A10  # noqa: E402
import probe_h10_cold_start_cap as H10  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h6_dense_gate as H6  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

DISCRIMINATOR_EDGE = 640       # -53 上必抛、-58+ 上喂先验必锁的那一翼
READ_EDGES = (600, 633, 400)   # 不喂也能锁的档：逐位比对才有对象
ORDERS = (("noprior", "prior"), ("prior", "noprior"))
MODE_LABEL = {"noprior": "不喂", "prior": "喂先验"}

APP: QApplication | None = None


def fact(label, value) -> None:
    H6.fact(label, value)


def bits(value: float) -> str:
    """float64 的字节，逐位比的单位。"""
    return struct.pack("<d", float(value)).hex()


def patch_rect(edge: int, truth) -> tuple[int, int, int, int]:
    """贴片在抓屏上的矩形（由独立尺子量到的中心反推，不用请求位——请求位可能被夹移）。"""
    cx, cy = float(truth[0]), float(truth[1])
    return (int(round(cx - edge / 2.0)), int(round(cy - edge / 2.0)),
            int(round(cx + edge / 2.0)), int(round(cy + edge / 2.0)))


def boxes_overlap(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def diff_bbox(shot_a, shot_b) -> tuple[tuple[int, int, int, int] | None, str]:
    """两遍抓屏之间的差异包围盒；返回 (盒或 None, 说明)。"""
    if shot_a.shape != shot_b.shape:
        return None, f"两遍抓屏形状不同 {shot_a.shape} vs {shot_b.shape} ⇒ 无从比"
    changed = np.any(shot_a != shot_b, axis=-1)
    n = int(changed.sum())
    if n == 0:
        return None, "无差异"
    ys, xs = np.nonzero(changed)
    return ((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), f"{n} 像素")


def read_one(eng, mode: str, frame, truth) -> dict:
    """一 pass：按 mode 注册同一贴片，取**首条** estimate，逐位记下。

    抓屏**在注册之前**做，并把整屏数组留在 dict 里：`compare_pair` 用它算两遍之间的
    差异包围盒。整屏字节相同这个要求在现场是**跑不通的**（实测：niri 栏上有时钟在动，
    两遍之间 sha 必不同，而四遍读数逐位相同），所以判"可比"要判在正确的位置上——
    看变动是否落在贴片矩形内。不落在贴片内 ⇒ 背景动画，仍可比（且对"不喂"那一支
    反而是加强：整帧窗口把那些会变的像素也扫进去了，读数照旧不动）。
    """
    out: dict = {"mode": mode, "tpl_sha": hashlib.sha256(frame.tobytes()).hexdigest()[:16]}
    shot = A10.capture(f"h21_screen_{mode}")
    out["shot"] = shot
    out["screen_sha"] = None if shot is None else hashlib.sha256(shot.tobytes()).hexdigest()[:16]
    kwargs = {} if mode == "noprior" else {
        "initial_center": (float(truth[0]), float(truth[1]))
    }
    out["fed"] = None if mode == "noprior" else kwargs["initial_center"]
    start = time.perf_counter()
    try:
        eng.register_target(frame, False, **kwargs)
    except BaseException as exc:  # noqa: BLE001
        out["register"] = f"refused:{type(exc).__name__}"
        return out
    out["register"] = "ok"
    out["ceiling"] = float(eng.confidence_ceiling)
    try:
        ex_ey = eng.estimate()
    except BaseException as exc:  # noqa: BLE001
        if type(exc).__name__ not in H10.ENGINE_EXC:
            raise RuntimeError(
                f"量具错（非引擎异常 {type(exc).__name__}）：{exc}"
                " ⇒ 本 pass 不判，不当作对上游的否证") from exc
        out["err"] = type(exc).__name__
        out["ms"] = (time.perf_counter() - start) * 1e3
        return out
    out["err"] = "lock"
    out["x"], out["y"], out["conf"] = (
        float(ex_ey[0]), float(ex_ey[1]), float(ex_ey[2]))
    out["x_bits"], out["y_bits"], out["conf_bits"] = bits(out["x"]), bits(out["y"]), bits(out["conf"])
    out["ms"] = (time.perf_counter() - start) * 1e3
    return out


def report_pass(edge: int, order_i: int, r: dict) -> None:
    if r["register"] != "ok":
        fact(f"{edge}²#{order_i} {MODE_LABEL[r['mode']]}", f"注册被拒（{r['register']}）")
        return
    if r["err"] != "lock":
        fact(f"{edge}²#{order_i} {MODE_LABEL[r['mode']]}",
             f"抛 {r['err']}｜{r['ms']:.1f} ms｜喂={r['fed']}")
        return
    ceil = r.get("ceiling")
    tag = "" if ceil is None else ("＝ceiling" if abs(r["conf"] - float(ceil)) < 1e-12 else "≠ceiling")
    fact(f"{edge}²#{order_i} {MODE_LABEL[r['mode']]}",
         f"读数=({r['x']:.2f},{r['y']:.2f}) conf={H6._f(r['conf'])}{tag}"
         f"｜{r['ms']:.1f} ms｜位=({r['x_bits']},{r['y_bits']},{r['conf_bits']})"
         f"｜屏={r['screen_sha']}｜模板={r['tpl_sha']}")


def compare_pair(edge: int, order_i: int, a: dict, b: dict, truth) -> str:
    """返回 PASS / FAIL / VOID，并把判据打进事实行。

    "可比"的判据不是整屏字节相同（现场做不到），而是**变动不落在贴片矩形内**。
    """
    if a["register"] != "ok" or b["register"] != "ok":
        verdict, why = "VOID", "有 pass 注册被拒 ⇒ 本对不判"
    elif a["err"] != "lock" or b["err"] != "lock":
        verdict, why = "VOID", f"有 pass 未锁定（{a['err']} / {b['err']}）⇒ 逐位无从比"
    elif a["shot"] is None or b["shot"] is None:
        verdict, why = "VOID", "有 pass 抓屏失败 ⇒ 无从证两遍输入同幅"
    else:
        box, note = diff_bbox(a["shot"], b["shot"])
        if box is None and note != "无差异":
            verdict, why = "VOID", f"两遍抓屏不可比（{note}）"
        elif box is not None and boxes_overlap(box, patch_rect(edge, truth)):
            verdict = "VOID"
            why = (f"两遍之间**贴片本身**变了（差异包围盒 {box}，{note}）"
                   "⇒ 不是同一幅输入，本对不判")
        else:
            same = (a["x"] == b["x"] and a["y"] == b["y"]
                    and a["conf_bits"] == b["conf_bits"])
            verdict = "PASS" if same else "FAIL"
            extra = ("｜两遍抓屏逐字节相同" if box is None
                     else f"｜差异包围盒 {box}（{note}）在贴片外 ⇒ 背景动画，"
                          "不喂那一支的整帧窗把这些像素也扫了，读数照旧逐位不动")
            why = ("位置 == 且 conf 字节相等" + extra if same else
                   f"不同：位=({a['x_bits']},{a['y_bits']},{a['conf_bits']}) vs "
                   f"({b['x_bits']},{b['y_bits']},{b['conf_bits']})")
    fact(f"VERDICT-BITWISE {edge}²#{order_i}", f"{verdict}｜{why}")
    return verdict


def channel_arm(eng, backend, host, x: int, y: int, phase: str) -> bool:
    """640² 那一翼：不喂必抛、喂必锁。返回是否成立（不成立则承重断言不判）。"""
    frame = H10.make_frame(DISCRIMINATOR_EDGE)
    truth = H10.mount(backend, host, frame, x, y, f"chan{phase}")
    if truth is None or truth[2] < H10.PRESENT_PEAK:
        fact(f"CHANNEL({phase})", f"补帧后尺峰 {'None' if truth is None else f'{truth[2]:.4f}'}"
                                  f" < {H10.PRESENT_PEAK} ⇒ 内容没上屏，本件不判")
        return False
    print(f"\n=== CHANNEL({phase})：{DISCRIMINATOR_EDGE}² 通道判别（真位中心 {truth[0]:.2f},{truth[1]:.2f}）===")
    noprior = read_one(eng, "noprior", frame, truth)
    report_pass(DISCRIMINATOR_EDGE, 0, noprior)
    H4.pump(APP, 240)
    prior = read_one(eng, "prior", frame, truth)
    report_pass(DISCRIMINATOR_EDGE, 0, prior)
    ok = noprior["register"] == "ok" and prior["register"] == "ok" \
        and noprior["err"] == "FidusTargetLost" and prior["err"] == "lock"
    fact(f"VERDICT-CHANNEL({phase})",
         f"{'PASS' if ok else 'FAIL'}｜不喂={noprior['err']}（预测 FidusTargetLost）、"
         f"喂={prior['err']}（预测 lock）"
         + ("；⇒ 不喂那一支确实走整帧、没沾残留信念" if ok else
            "；⇒ 通道不干净，BITWISE 那一条读作**不可判**，不是通过"))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-long", type=int, default=1366,
                    help="输出长边（物理像素）；本机 1366，换机器请如实改这个数")
    ap.add_argument("--edges", default="600",
                    help=f"逐位比对的档位（逗号分隔，取自 {'/'.join(str(s) for s in READ_EDGES)}）")
    args = ap.parse_args()
    edges = tuple(int(s) for s in args.edges.replace(" ", "").split(","))
    if not edges or any(s not in READ_EDGES for s in edges):
        print(f"✗ --edges 只接受 {READ_EDGES} 的子集，收到 {args.edges!r}")
        return 1

    idy = H7.arm_identity()
    if not idy["anchor_ok"] or not idy["so_ok"]:
        fact("⚠ 锚点与登记不符", "下面的数字仍会打印，但只能按**本轮实际锚点**读")

    global APP  # noqa: PLW0603
    APP = QApplication(sys.argv[:1])  # noqa: PLW0603
    H10.APP = APP          # H10.mount/H10.read_arm 里的 pump 取的是 **H10 的模块全局**，不设则 None
    eng = fidus.Fidus.build_wayland()
    host = H1.HostWindow()
    host.show()
    H4.pump(APP, 500)
    backend = H1.FACADE.get_backend()
    fact("锚点", fidus.__git_commit__)
    fact("register_target 签名", fidus.Fidus.register_target.__text_signature__)
    fact("gate_status", eng.gate_status())
    fact("输出长边取数", f"按命令行 {args.out_long}（本探针不自动探测输出几何）")

    # surface 摆在整帧路径下能锁的几何里：x 按**全档最大边**居中（与 H10 同口径），y 固定。
    x = (args.out_long - max(edges + (DISCRIMINATOR_EDGE,))) // 2
    y = 64

    # 校准：本会话唯一一次（A 臂实测每会话一次）。落在通道判别臂上。
    frame0 = H10.make_frame(DISCRIMINATOR_EDGE)
    t0 = H10.mount(backend, host, frame0, x, y, "cal")
    if t0 is None or t0[2] < H10.PRESENT_PEAK:
        print("VERDICT-BITWISE : ✗ 首臂内容没上屏 ⇒ 量具断，不产出任何数字")
        backend.destroy_context()
        return 1
    try:
        eng.register_target(frame0, False)
    except BaseException as exc:  # noqa: BLE001
        print(f"VERDICT-BITWISE : ✗ 首臂注册被拒（{type(exc).__name__}）⇒ 无从校准")
        backend.destroy_context()
        return 1
    cal_start = time.perf_counter()
    try:
        cal = eng.calibrate_once()
    except BaseException as exc:  # noqa: BLE001
        print(f"VERDICT-BITWISE : ✗ calibrate_once 抛 {type(exc).__name__}: {exc} ⇒ 不产出任何数字")
        backend.destroy_context()
        return 1
    fact("calibrate_once", f"{(time.perf_counter() - cal_start) * 1e3:.0f} ms "
                           f"scale={getattr(cal, 'scale', '?')}")

    channel_ok = channel_arm(eng, backend, host, x, y, "start")

    verdicts: list[tuple[int, int, str]] = []
    for edge in edges:
        frame = H10.make_frame(edge)
        truth = H10.mount(backend, host, frame, x, y, f"e{edge}")
        if truth is None or truth[2] < H10.PRESENT_PEAK:
            fact(f"臂 {edge}", f"尺峰 {'None' if truth is None else f'{truth[2]:.4f}'}"
                               f" < {H10.PRESENT_PEAK} ⇒ 没上屏，本档不判")
            continue
        print(f"\n=== 臂 {edge}²（真位中心 {truth[0]:.2f},{truth[1]:.2f}，尺峰 {truth[2]:.4f}）===")
        for order_i, (first, second) in enumerate(ORDERS):
            a = read_one(eng, first, frame, truth)
            report_pass(edge, order_i + 1, a)
            H4.pump(APP, 240)
            b = read_one(eng, second, frame, truth)
            report_pass(edge, order_i + 1, b)
            verdicts.append((edge, order_i + 1, compare_pair(edge, order_i + 1, a, b, truth)))
            H4.pump(APP, 240)

    channel_end = channel_arm(eng, backend, host, x, y, "end")
    backend.destroy_context()

    print("\n[VERDICT]")
    if not channel_ok or not channel_end:
        print("VERDICT-BITWISE : 不可判｜通道判别"
              f"{'起' if not channel_ok else '末'}那一次不成立 ⇒ "
              "「不喂」那一支是否真走整帧没有机核，逐位相同即使成立也不能算对贵方那条的验收")
    elif not verdicts:
        print("VERDICT-BITWISE : 不可判｜没有任何档跑到可比对的一对 pass（见上面对比行）")
    else:
        n_pass = sum(1 for _e, _o, v in verdicts if v == "PASS")
        n_fail = sum(1 for _e, _o, v in verdicts if v == "FAIL")
        n_void = sum(1 for _e, _o, v in verdicts if v == "VOID")
        summary = f"{len(verdicts)} 对：PASS {n_pass}／FAIL {n_fail}／VOID {n_void}"
        if n_fail:
            print(f"VERDICT-BITWISE : ✗ 否证成立｜{summary} ⇒ 喂与不喂的首条读数并非逐位相同，"
                  "须点名是哪一对、两个顺序都 FAIL 还是只有一个顺序 FAIL")
        elif n_pass and not n_void:
            print(f"VERDICT-BITWISE : ✓ 成立｜{summary}｜两个顺序各档均逐位相同")
        else:
            print(f"VERDICT-BITWISE : 未定｜{summary}（有 VOID 但无 FAIL ⇒ 既有 PASS 只算部分兑现）")
    print(f"VERDICT-CHANNEL : start={'PASS' if channel_ok else 'FAIL'} "
          f"end={'PASS' if channel_end else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
