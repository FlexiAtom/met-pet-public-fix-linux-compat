#!/usr/bin/env python3
"""H23 · 预挂载（交互态）的两臂对表：喂 Qt 信念先验 vs 走 fidus 开出的第二格（不喂）。

出处：fidus 对我方入站件 `projects/fidus/pool/meapet-premount-locate-unreliable.md` 的裁定
（广播 pid-385355）第四、五条。要点是两句把责任移给我方的话：
* 「预挂载测不准**不在我方码里修**——预挂载没有合格先验，那一发该直接走三级阶梯的第二格
  （`register_target` 不带 `initial_center` ⇒ 整帧首搜），**「真值在窗外」这一族失败结构性消失**。」
* 「(甲) 预挂载测量**在射程内**……(乙) 位移闭环……Wayland 下对任何引擎都不可用，
  贵方三场里的闭环失败是**预期**，不该计入测量失败。」

人工裁的是「退回 pool，先修 fidus 侧测量能力」。本件先把这句的**证伪尝试**做掉：
同一台机、同一素材、同一生产候选路径（`FP.select_template` + `FP._settle`，不复刻），
只把先验从「Qt 信念」换成「不喂」——
* 若第二格格地锁对 ⇒ fidus 的归因成立（病灶在先验，不在引擎），我方欠的是接线；
* 若第二格照样锁错或三场三果 ⇒ 「能力没问题」不成立，「不修」要重裁。

两臂都**不做位移闭环**——那是 fidus 已裁「结构性不可用」的那一半（裁定乙），本件不重复主张，
只问「单发冷获取能不能给出可信读数」。对表一律是 grim + 宿主 NCC（`probe_h12.probe_box`
那条独立尺子路线），不经 fidus。

场景是**真 app 的交互态**（`MeaPet` + `_set_layer_mode(False)`，桌宠还是普通 Qt 窗口、
没有 layer surface）——预挂载要的就是这一格。
**相位已冻结**，冻法见下面 `main()` 里那段（走 paintGL 与离屏共用的 `_draw_model` 那一口）。
不是一开始就冻的：第一跑不冻，结果 B 臂的独立尺子最优峰只有 0.8913 < 0.90 在位闸 ⇒
96² 的小贴片抵不住动画，量具断在**要判的那一臂**上。既然本件要判的是「先验有无」这一件事，
就把相位从两侧同时消去，而不是让尺子在被测物之前先瞎。

三场 = 本脚本被独立调用三次（`--order ab` / `ba` / `ab`）。轮换顺序是防"后一臂沾前一臂的
残留信念"：`register_target` 每次注册都丢 fix（轮子自带 docstring 自陈，H21 的 CHANNEL 臂
已在真机复核），所以两臂各自都是冷获取。

用法
----
    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h23_premount_prior_vs_rung2.py --order ab
副作用（真机）：起一次真桌宠（照常启动）、一次 `calibrate_once`（约 2 s + 屏幕闪）、
若干次整屏抓取。**不挂 layer surface、不摆位、不改配置、不落盘。**
退出码：0 = 跑到出口（结论再否定也是 0）；1 = 量具自身不可用（帧不可得、在位闸不过、选不出盒）。
"""
from __future__ import annotations

import argparse
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

from PyQt5.QtWidgets import QApplication  # noqa: E402

import probe_a10_geometry as A10                  # noqa: E402
import probe_h4_ceiling_infer as H4               # noqa: E402
import probe_h6_dense_gate as H6                  # noqa: E402
import probe_h8_roi_on_screen as H8               # noqa: E402
import probe_h12_product_path as H12              # noqa: E402
import probe_h15_product_wiring as H15            # noqa: E402
from meapet.desktop import fidus_position as FP   # noqa: E402

PRESENT_PEAK = H12.PRESENT_PEAK   # 0.90 在位闸：过不去是量具断，不产出读数
TOL_PX = 2.0                      # 与 H12 同口径（首锁量到 0.00 px）
TRUTH_SAMPLES = 4                 # 尺子取最优峰的采样数（相位漂移的对策，非精度）
BELIEFLESS_HALF_LIMIT = 1999.5    # 轮子 docstring 自陈的整帧首搜半幅上限（物理 px）
DECOR_RGB = (0x7F, 0xC8, 0xFF)    # niri 焦点环，docstring 点名的污染源
BOOT_TIMEOUT = 45.0
DUMP_DIR = Path(os.environ.get("H23_DUMP_DIR", "/tmp/h23_dump"))


def fact(label, value) -> None:
    H6.fact(label, value)


def template_truth(box, tag: str, samples: int = TRUTH_SAMPLES):
    """独立尺子：连抓 N 张整屏，对 `box` 的亮度面求峰，取**最优峰**那一张的位置。

    取最优而不是取均值：桌宠在动，某一拍的姿势与注册时的模板差得远，那一发的峰低但不
    代表位置错；均值会把"量具在不在位"这件事和相位混成一团。最优峰过不了在位闸才是量具断。
    回 `(贴片中心 x, y, 峰, 那张抓屏)`；坐标是**屏上物理 px**（grim 全幅）。
    """
    best = None
    peaks = []
    for i in range(samples):
        shot = A10.capture(f"{tag}_{i}")
        if shot is None:
            continue
        peak, x, y = A10.Correlator(H8.luma_of(shot)).peak(H8.luma_of(box))
        peaks.append(peak)
        if best is None or peak > best[2]:
            edge = box.shape[0]
            best = (float(x + edge / 2.0), float(y + edge / 2.0), float(peak), shot)
        H4.pump(QApplication.instance(), 120)
    return best, peaks


def decor_inside(shot, cx: float, cy: float, edge: int) -> int:
    """数目标矩形里有多少只像素**正是** niri 焦点环的颜色（docstring 要求宿主自查的那条）。"""
    half = edge // 2
    x0, y0 = max(0, int(cx - half)), max(0, int(cy - half))
    h, w = shot.shape[:2]
    patch = shot[y0:min(h, y0 + edge), x0:min(w, x0 + edge)]
    if patch.size == 0:
        return 0
    b, g, r = (patch[..., 0].astype(int), patch[..., 1].astype(int), patch[..., 2].astype(int))
    return int(((r == DECOR_RGB[0]) & (g == DECOR_RGB[1]) & (b == DECOR_RGB[2])).sum())


def alpha_frac(box) -> float:
    """盒内 `alpha < 250` 的像素占比——屏上那部分是**与桌面合成**后的亮度，与帧不同源。

    这一条是给「真值为什么只认独立不透明盒」记账用的，不参与判定。
    """
    return float((box[..., 3] < 250).mean())


def dump_pair(name: str, frame, shot, box) -> None:
    """把「离屏帧 / 屏上抓屏 / 被相关的那只盒」三样落盘——在位闸失败后唯一还能看的东西。

    存在的原因：`0.6619` 与「四采样逐位相同」合起来只说明**两侧都是静态、但两侧不是同一物**，
    不说差在哪一维（相位？缩放？位移？根本没上屏？）。四族各自的修法不同，日志分不出来，
    并排看一次就分得出来。写 `/tmp`（可用 `H23_DUMP_DIR` 改），不进仓库、不落配置。
    """
    from PIL import Image

    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    for suffix, arr in (("frame", frame), ("shot", shot), ("box", box)):
        if arr is None:
            continue
        a = np.ascontiguousarray(arr)
        mode = "RGBA" if a.shape[2] == 4 else "RGB"
        Image.fromarray(a, mode).save(DUMP_DIR / f"{name}_{suffix}.png")
    fact("落盘", f"{DUMP_DIR}/{name}_{{frame,shot,box}}.png")


def cold_acquire(eng: FP.FidusEngine, box, belief, label: str):
    """按给定先验（`None` = 第二格）重注册并连读到定住。

    回 `(状态, 读数 | 错误串, ceiling, 耗时秒)`。「注册被拒」与「定不住」是两族失效
    （前者是算术闸门，后者是匹配本身），合并了就分不清账，所以分开返回。
    """
    t0 = time.perf_counter()
    try:
        ceiling = eng.register(box, belief)
    except FP.EngineError as exc:
        return "注册被拒", f"{type(exc).__name__}: {exc}", None, time.perf_counter() - t0
    secs_reg = time.perf_counter() - t0
    reading = FP._settle(eng, label, log=lambda k, v: fact(f"    {k}", v))
    return ("定住" if reading is not None else "定不住"), reading, ceiling, secs_reg


def boot_pet(app) -> object | None:
    """起真桌宠并泵到「离屏帧拿得到」；拿不到就是量具断。

    `fidus.enabled` 必须在 `show()` **之前**置回 False：产品的挂载路径在首帧就绪那一刻就投
    定位作业（用户 config 里是开的），而 fidus 只允许一条线程持有引擎——等本件接手时再关，
    产品那条 fidus 线程已经建过一只引擎了。本件自己只在主线程驱动一只。
    """
    from meapet.desktop.app import MeaPet

    pet = MeaPet()
    pet.config.setdefault("fidus", {})["enabled"] = False
    pet.show()
    deadline = time.perf_counter() + BOOT_TIMEOUT
    while time.perf_counter() < deadline:
        H4.pump(app, 500)
        if pet._fidus_current_frame() is not None:
            return pet
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", choices=("ab", "ba"), default="ab",
                    help="两臂顺序；ab = 先复现我方现状，ba = 先跑第二格")
    args = ap.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    fact("锚点", __import__("fidus").__git_commit__)
    fact("被测代码", f"{FP.__file__}（生产选尺与冷获取，未复刻）")
    fact("臂序", "A=喂 Qt 信念先验 → B=不喂（整帧首搜）" if args.order == "ab"
                 else "B=不喂（整帧首搜）→ A=喂 Qt 信念先验")

    sc, unit_line = H15.read_units(app)
    fact("合成器单位", unit_line)
    if sc != sc:
        print("VERDICT-H23 : ✗ 读不到 scale ⇒ 换算无从谈起，本件不产出主张")
        return 1
    if abs(sc - 1.0) > 1e-9:
        print(f"VERDICT-H23 : ✗ 本件的尺子按物理 px、引擎按逻辑 px，只在 scale=1.0 上做对表；"
              f"当前 {sc} ⇒ 不跑，不猜")
        return 1

    geo = app.primaryScreen().geometry()
    avail = app.primaryScreen().availableGeometry()
    fact("屏幕", f"geometry=({geo.x()},{geo.y()},{geo.width()}×{geo.height()}) "
                 f"available=({avail.x()},{avail.y()},{avail.width()}×{avail.height()})")

    pet = boot_pet(app)
    if pet is None:
        print(f"VERDICT-H23 : ✗ {BOOT_TIMEOUT} s 内拿不到离屏帧 ⇒ 量具自身失效，不猜")
        return 1
    fact("产品 fidus", f"用户 config 是 enabled=True，本件在 `show()` 之前置回 False"
                       f"（见 `boot_pet` 注）——产品的挂载路径也投定位作业，而 fidus 只允许"
                       f"**一条**线程持有引擎，两边同时驱动就是未定义行为。改内存，不落盘。")

    # 冻结相位：`_draw_model` 是 paintGL 与 render_offscreen **共用**的那一口
    # （live2d_widget.py:324 自陈「供 paintGL 与离屏渲染共用」），里面 `model.Update()` 推进、
    # `model.Draw()` 绘制。只留 Draw ⇒ 屏幕与离屏帧从此逐拍同相，两侧一起冻。
    # 停 `sprite_label._timer` 是错的量法：那一只冻住了显示提交，`render_offscreen` 还在推进
    # 模型，两侧反而**越来越不同相**（实测把峰从 0.9964 打到 0.69，且两个相位给出同一个数）。
    widget = pet.sprite_label
    widget._draw_model = widget.l2d.model.Draw      # type: ignore[method-assign]
    H4.pump(app, 400)
    fact("相位", "`_draw_model` 只留 `model.Draw()`（去掉 `Update()` 与光标追踪）⇒ "
                 "显示与离屏同源同相；本件测的是**先验**这一件事，相位作为变量已消去")

    # 预挂载场景：把桌宠留在交互态（普通 Qt 窗口），不挂 layer surface。
    pet._set_layer_mode(False)
    H4.pump(app, 1500)
    backend = getattr(pet, "_layer_backend", None)
    fact("态", f"`_set_layer_mode(False)` 已调用；`_layer_backend`={type(backend).__name__} "
               f"｜顶层 ({pet.x()},{pet.y()}) size ({pet.width()}×{pet.height()})")

    frame = pet._fidus_current_frame()
    if frame is None:
        print("VERDICT-H23 : ✗ 交互态下取不到帧 ⇒ 量具断")
        return 1
    fh, fw = frame.shape[:2]
    fact("被测帧", f"{fw}×{fh}（来自 `pet._fidus_current_frame()`，与产品模板同源）")

    lx, ly, lw, lh = pet._layer_geometry()
    belief_center = (lx + lw / 2.0, ly + lh / 2.0)
    fact("信念", f"`_layer_geometry()`=({lx},{ly},{lw}×{lh}) ⇒ 信念中心 ({belief_center[0]:.2f},"
                 f"{belief_center[1]:.2f})")

    eng = FP.FidusEngine()
    t_cal = time.perf_counter()
    eng.calibrate()
    fact("calibrate", f"{(time.perf_counter() - t_cal) * 1e3:.0f} ms（一次会话一发）")

    picked = FP.select_template(eng, frame, belief_center,
                                log=lambda k, v: fact(f"    {k}", v))
    if picked is None:
        print("VERDICT-H23 : ✗ 生产选尺全被拒 ⇒ 本件测不到第二格，如实记不可得")
        return 1
    cand, ceiling = picked
    edge = cand.edge
    ask_half = max(geo.width(), geo.height()) + edge
    rung2_gate = ("放行" if ask_half <= BELIEFLESS_HALF_LIMIT else
                  "**被拒**（docstring：被拒的形态是每发 FidusTargetLost，看起来像空屏）")
    fact("选定贴片", f"{edge}² 帧内锚点偏移={tuple(round(v, 1) for v in cand.anchor)} "
                    f"ceiling={ceiling:.6f}（挂载前闸门要的就是这个数）")
    fact("第二格算术", f"整帧首搜半幅 = max(capture){max(geo.width(), geo.height())} "
                       f"+ max(tpl){edge} = {ask_half} px vs 上限 {BELIEFLESS_HALF_LIMIT} ⇒ "
                       f"{rung2_gate}")

    # 量具先在位：用**整盒不透明**的独立盒确认内容确实在屏上，并让它充当全案唯一真值来源。
    # 真值一律取这只盒，不取 fidus 自己的贴片：独立尺子的资格是"与被测代码不共享选择逻辑"，
    # 而贴片那 96² 落在发丝一带，边缘含 alpha（本场实测 alpha<250 占 3.2%，独立盒 0.1%），
    # 屏上那里是**与桌面合成之后**的亮度 —— 量具的失败面应当比被测面窄，不是宽。
    # 记账：2026-10-02 有一场在位闸 0.9971 过、而贴片真值只有 0.8137（两数同场）。3.2% 的
    # 边缘透明**不足以解释**掉到 0.81，那一场的成因（遮挡？桌面换了底下那层？）未定，
    # 换掉真值来源之后没有再复现——所以这一改是**收窄量具失败面**，不是"根因已查明"。
    ruler_box, ruler_off = H12.probe_box(frame)     # ruler_off = 盒心相对帧心的偏移（帧内 px）
    if ruler_box is None:
        print("VERDICT-H23 : ✗ 独立尺子选不出盒 ⇒ 量具自身失效")
        return 1
    ruler, r_peaks = template_truth(ruler_box, "h23_ruler")
    fact("在位闸", f"独立盒 {ruler_box.shape[0]}² 峰值 {[f'{p:.4f}' for p in r_peaks]} "
                   f"⇒ 最优 {ruler[2]:.4f} {'✓' if ruler[2] >= PRESENT_PEAK else '✗'}")
    if ruler[2] < PRESENT_PEAK:
        dump_pair("h23_visible_fail", frame, ruler[3] if ruler else None, ruler_box)
        # 「合成器手里还是冻相前那一张」这一族失效的判据：主动催一拍 paintGL，
        # 让窗口自己把**冻相后**的内容提交上去，再量一次。两次之间只有「有没有被催」这一个变量。
        for _ in range(3):
            widget.update()
            pet.update()
            H4.pump(app, 300)
        ruler2, r_peaks2 = template_truth(ruler_box, "h23_ruler_retry")
        fact("在位闸·催拍后", f"峰值 {[f'{p:.4f}' for p in r_peaks2]} ⇒ 最优 {ruler2[2]:.4f} "
                            f"{'✓' if ruler2[2] >= PRESENT_PEAK else '✗'}"
                            f"｜与上一轮之差 {ruler2[2] - ruler[2]:+.4f}")
        if ruler2[2] < PRESENT_PEAK:
            dump_pair("h23_visible_fail_retry", frame, ruler2[3], ruler_box)
            print(f"VERDICT-VISIBLE : ✗ 最优峰 {ruler[2]:.4f} → 催拍后 {ruler2[2]:.4f} "
                  f"仍 < {PRESENT_PEAK} ⇒ 内容不在屏上（或与帧不同源），全案不产出读数")
            return 1
        print("VERDICT-VISIBLE : 首量不过、**催一拍 paintGL 后过** ⇒ 屏上是合成器留的旧缓冲，"
              "不是「没上屏」；后续一律按催拍后的真值")
        ruler = ruler2

    # 帧心在屏上的位置 = 盒心 − 盒心相对帧心的偏移。两臂全程不挪窗，所以这一发真值两臂共用。
    truth_center = (ruler[0] - ruler_off[0], ruler[1] - ruler_off[1])
    fact("真值口径", f"独立不透明盒 {ruler_box.shape[0]}² 屏上盒心=({ruler[0]:.2f},"
                     f"{ruler[1]:.2f}) 帧内偏移={tuple(round(v, 1) for v in ruler_off)} "
                     f"⇒ **帧心屏上=({truth_center[0]:.2f},{truth_center[1]:.2f})**｜峰={ruler[2]:.4f}")
    fact("不透明度", f"fidus 贴片 {edge}² alpha<250 占 {alpha_frac(cand.box):.1%}"
                     f"｜独立盒 alpha<250 占 {alpha_frac(ruler_box):.1%}"
                     f" ⇒ 真值只认独立盒")

    results: dict[str, tuple] = {}
    order = ("A", "B") if args.order == "ab" else ("B", "A")
    for arm in order:
        tag = "喂 Qt 信念先验" if arm == "A" else "不喂（第二格 · 整帧首搜）"
        print(f"\n=== 臂 {arm} · {tag} ===")
        belief = cand.belief_for(belief_center) if arm == "A" else None
        if belief is not None:
            fact("先验", f"贴片中心信念=({belief[0]:.2f},{belief[1]:.2f})")
        status, payload, ce, secs_reg = cold_acquire(eng, cand.box, belief, f"臂{arm}")
        if status == "注册被拒":
            fact("注册", f"被拒 {payload}（{secs_reg * 1e3:.0f} ms）⇒ 本臂不判")
            results[arm] = (status, None, None, None, secs_reg)
            continue
        fact("注册", f"{secs_reg * 1e3:.0f} ms｜ceiling={ce:.6f}")
        dx = dy = None
        if payload is None:
            fact("读数", f"{FP.ACQUIRE_READS} 发内没定住 ⇒ 退回")
        else:
            x, y, conf = payload
            # fidus 报的是**贴片中心**；按锚点换算回帧心，与 `Fix.center` 用的同一条公式，
            # 也和独立尺子那发真值（帧心）落在同一个量上。
            fx, fy = x - cand.anchor[0], y - cand.anchor[1]
            dx, dy = fx - truth_center[0], fy - truth_center[1]
            verdict = "✓ 锁对" if max(abs(dx), abs(dy)) <= TOL_PX else "✗ 锁错"
            fact("读数", f"贴片中心 ({x:.2f},{y:.2f}) → 帧心 ({fx:.2f},{fy:.2f}) "
                         f"conf={conf:.6f}｜真值帧心=({truth_center[0]:.2f},"
                         f"{truth_center[1]:.2f}) 峰={ruler[2]:.4f}｜差=({dx:+.2f},{dy:+.2f}) px "
                         f"{verdict}｜conf==ceiling: {abs(conf - ce) < 1e-12}")
            fact("装饰色自检", f"帧心矩形内 niri 焦点环 #7fc8ff 像素 "
                              f"{decor_inside(ruler[3], fx, fy, edge)} 只")
        results[arm] = (status, payload, (*truth_center, ruler[2]), (dx, dy), secs_reg)

    print("\n=== 出口 ===")
    for arm in ("A", "B"):
        if arm not in results:
            print(f"VERDICT-ARM{arm} : 未跑")
            continue
        status, reading, truth, dxy, _secs = results[arm]
        if dxy is not None and None not in dxy:
            dx, dy = dxy
            print(f"VERDICT-ARM{arm} : 差 ({dx:+.2f},{dy:+.2f}) px｜阈值 {TOL_PX} ⇒ "
                  f"{'锁对' if max(abs(dx), abs(dy)) <= TOL_PX else '锁错'}")
        else:
            print(f"VERDICT-ARM{arm} : {status}（无位置读数）")
    ref = next((results[a][2] for a in ("B", "A") if a in results and results[a][2]), None)
    if ref is not None:
        print(f"VERDICT-BELIEF : 信念中心 ({belief_center[0]:.2f},{belief_center[1]:.2f}) "
              f"vs 真值 ({ref[0]:.2f},{ref[1]:.2f}) ⇒ 先验误差 "
              f"({belief_center[0] - ref[0]:+.2f},{belief_center[1] - ref[1]:+.2f}) px")
    print("VERDICT-RUN : 跑到出口")
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except BaseException:                    # noqa: BLE001 - 出口必须带得出异常，不许假成功
        import traceback

        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        rc = 1
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        # 真 app 带着 worker 线程与 Live2D 的关窗崩溃（H15 teardown 记过那条），
        # 本件的出口与异常都已印出 ⇒ 直接退出，不给收尾引入新变量的机会。
        os._exit(rc)
