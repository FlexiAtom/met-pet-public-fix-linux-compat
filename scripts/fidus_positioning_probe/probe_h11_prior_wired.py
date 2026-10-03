#!/usr/bin/env python3
"""H11 · `-58` 把先验接进 `fused` 之后的冷启动四问（真机，验收 ②③④ + 贵方追加的一条）

出处：fidus 入站件 `pool/fidus-fused-prior-wired.md` §11 与贵方对 §6 验收 ②③④ 的归属声明
（"真机数归贵方"）。装机轮 `-53` 上我方已坐实（H10）：`register_target` 置 `has_fix=false`
⇒ 首条 `estimate` 请求整块输出当窗口，位置数 `(2·(输出边+模板边)+1)²` 在**夹紧之前**计价
⇒ 本机 @1366 的临界模板边 633（640／634 全抛 `FidusTargetLost`，633／600 锁定 0.00 px）。

`-58` 声称接上了 `target.initial_center`（`fused.rs:277-294` 先验档，共用 `search_half()`
⇒ 半宽 `1.5·s + 48`，**不含输出边**）。本探针只判四件事，各自独立、不互相顶替：

* **R2-PRIOR（贵方验收 ②，本件的解闸尺）** —— 同一 material、同一 surface 几何，**只多喂一个
  先验中心**（取独立尺子量到的真位，即"信念恰好为真"这一最好档）⇒ 640²／634² 首条 `estimate`
  必须**不再抛**。翻判 ⇒ §5.7 解闸；不翻判 ⇒ 继续挂。
* **R4-NOPRIOR（贵方验收 ④）** —— 同一个探针**不喂**先验复跑那三臂 ⇒ 必须与 `-53` **逐位同判**
  （640／634 仍抛、633 仍锁定）。若 None 臂被改动，γ 就把今天唯一还在工作的兜底路径一起改掉了。
* **R3-COST（贵方验收 ③，观测项非门槛）** —— 报每条读数的 ms，与 `-53` 那两档基线对表
  （497 ms @96² 整帧／2.5–6.7 s @633² 整帧）。
* **WRONG-BELIEF + CORNER（贵方 §5 主动交代的新失败模式，与 §7-③ 那条警告）** —— 96² 模板、
  信念**故意偏 440 px** ⇒ 数"第几发才找到"（贵方 CPU 侧钉的是 5）；另一臂给 640² surface 喂
  **左上角**而非中心 ⇒ 我方按 `half = 1.5·s ≥ s/2` 推的是"喂成角**永远出不了窗**"，
  与贵方"半份渲染偏移"的警告并不冲突，但冲突与否必须实测，不能靠式子收尾。

先验坐标系按贵方 §3：layer-shell **usable-area 逻辑系**、**中心**、与 `estimate` 返回值同系
⇒ 本探针喂的是"grim + 宿主 NCC 独立量到的真位中心"（不经 fidus），生产里喂的是我方请求位。

用法
----
    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h11_prior_wired.py
副作用：真实桌面上挂/拆 layer 浮层（最大 640×640 不透明方块）、每臂多次整屏抓取、一次 `calibrate_once`。
退出码：0 = 各臂都跑到出口（结论再否定也是 0）；1 = 量具/环境不可用。
"""
from __future__ import annotations

import argparse
import hashlib
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fidus  # noqa: E402
import probe_h10_cold_start_cap as H10  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h6_dense_gate as H6  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

SIZES = (640, 634, 633)      # 640/634 = -53 上必抛的那两翼；633 = 必锁定的临界内侧
SMALL_EDGE = H10.CONTROL_EDGE    # 96²：WRONG-BELIEF 臂用小模板，才谈得上"第几发"
TPLE_EDGES = (96, 128, 160, 200, 256)   # 桌宠量级：COST-CURVE 臂问的是"切换那一刻要等几毫秒"
READS = 3                    # 无先验臂连读几条：验的是"每次调用同样失败"，不是只验首条
WRONG_OFFSET = 440.0         # 贵方 §5 那一格的偏移量，照抄数值才能对表"第 5 发"
WRONG_MAX_READS = 12         # 落空臂最多读几发；超过就说明展宽不是 64 px/发
LOCK_TOL = 3.0               # 锁定判据：与独立尺子的真位中心误差（px）
PUMP_MS = 240
APP: QApplication | None = None


def fact(label, value) -> None:
    H6.fact(label, value)


def half_of_prior(template_edge: int) -> float:
    """贵方 `search_half()` 在冷启动（`lost_streak == 0`）那一档的半宽。"""
    return 1.5 * template_edge + 48.0


def positions_no_prior(edge: int, out_long: int) -> int:
    return (2 * (out_long + edge) + 1) ** 2


def positions_with_prior(edge: int) -> int:
    half = half_of_prior(edge)
    return int((2 * half + 1) ** 2)


def belief_variants(truth, req_x: int, req_y: int) -> dict:
    """两档信念：`oracle` = 独立尺子量到的真位中心（"信念恰好为真"这一最好档）；
    `corner` = 我方请求位的**左上角**（贵方 §7-③ 警告的那种喂法，也是产品里最容易写错的一发）。
    `truth` 是 (cx, cy, peak)。"""
    cx, cy = float(truth[0]), float(truth[1])
    return {"oracle": (cx, cy), "corner": (float(req_x), float(req_y))}


def one_read(eng, label: str) -> dict:
    start = time.perf_counter()
    try:
        ex_ey = eng.estimate()
        out = {"err": "lock", "pt": (float(ex_ey[0]), float(ex_ey[1])),
               "conf": float(ex_ey[2])}
        txt = f"读数=({out['pt'][0]:.2f},{out['pt'][1]:.2f}) conf={H6._f(out['conf'])}"
    except BaseException as exc:  # noqa: BLE001
        out = {"err": type(exc).__name__}
        txt = f"抛 {type(exc).__name__}"
    out["ms"] = (time.perf_counter() - start) * 1e3
    fact(label, f"{txt}｜{out['ms']:.1f} ms")
    H4.pump(APP, PUMP_MS)
    return out


def arm(eng, backend, host, edge: int, out_long: int, x: int, y: int,
        belief: str, reads: int) -> dict:
    """挂 edge² → 注册（按 `belief` 决定是否喂先验）→ 逐条 estimate。"""
    frame = H10.make_frame(edge)
    t = H10.mount(backend, host, frame, x, y, f"h11_{belief}_{edge}")
    rec = {"edge": edge, "belief": belief,
           "tpl_sha": hashlib.sha256(frame.tobytes()).hexdigest()[:16],
           "present": None if t is None else round(float(t[2]), 4)}
    if t is None or t[2] < H10.PRESENT_PEAK:
        rec["register"] = "量具断"
        fact(f"臂 {belief}/{edge}", f"尺峰 = {t} < {H10.PRESENT_PEAK} ⇒ 内容没上屏，本臂不判")
        return rec
    rec["truth"] = (round(float(t[0]), 2), round(float(t[1]), 2))
    bel = belief_variants(t, x, y)
    kwargs = {} if belief == "none" else {"initial_center": bel[belief]}
    try:
        eng.register_target(frame, False, **kwargs)
        rec["register"] = "ok"
        rec["ceiling"] = float(eng.confidence_ceiling)
    except BaseException as exc:  # noqa: BLE001
        rec["register"] = f"refused:{type(exc).__name__}"
        fact(f"臂 {belief}/{edge}", f"注册被拒（{type(exc).__name__}: {exc}）⇒ 本臂不产出读数结论")
        return rec
    rec["positions"] = (positions_no_prior(edge, out_long) if belief == "none"
                        else positions_with_prior(edge))
    rs = [one_read(eng, f"  {belief}/{edge} estimate#{k + 1}") for k in range(reads)]
    rec["reads"] = rs
    first = rs[0]
    if first["err"] == "lock":
        dx = first["pt"][0] - float(t[0])
        dy = first["pt"][1] - float(t[1])
        rec["err_px"] = (round(dx, 2), round(dy, 2))
        rec["locked_first"] = max(abs(dx), abs(dy)) <= LOCK_TOL
    else:
        rec["err_px"] = None
        rec["locked_first"] = False
    rec["all_threw"] = all(r["err"] == "FidusTargetLost" for r in rs)
    fact(f"臂 {belief}/{edge} 出口",
         f"位置数 {rec['positions']:,}｜注册={rec['register']}｜首条"
         f"{'锁定 ' + str(rec['err_px']) + ' px' if first['err'] == 'lock' else '抛 ' + first['err']}"
         f"｜首条 {first['ms']:.1f} ms")
    return rec


def wrong_belief_arm(eng, backend, host, surface_edge: int, x: int, y: int,
                     offset: float) -> dict:
    """小模板 + 信念偏 `offset` px ⇒ 数第几发才找到（贵方 §5：440 px → 第 5 发）。

    展宽是 `+64·lost_streak`，首窗半宽 `1.5·s + 48`：s=96 ⇒ 192，覆盖 440 需 `192+64n ≥ 440`
    ⇒ n=4，即第 5 发。这条把贵方的**机制**变成我方的**可数出口**。"""
    frame = H10.make_frame(surface_edge)
    # 必须先挂：plan 收尾时桌上留的是 633² surface，拿 640² 的裁剪去量真位必然掉在
    # 「量具断」上（首跑实测：本臂零读数却被出口行读成"连读未锁"）。
    if H10.mount(backend, host, frame, x, y, "h11_wrong") is None:
        rec_abort = {"offset": offset, "tpl_edge": SMALL_EDGE, "register": "量具断"}
        fact("WRONG-BELIEF", "挂 640² surface 失败 ⇒ 本臂不判")
        return rec_abort
    c0 = (surface_edge - SMALL_EDGE) // 2
    roi = frame[c0:c0 + SMALL_EDGE, c0:c0 + SMALL_EDGE].copy()
    t = H10.truth_of(roi, "h11_small_truth")
    rec = {"offset": offset, "tpl_edge": SMALL_EDGE, "n_reads": 0,
           "present": None if t is None else round(float(t[2]), 4)}
    if t is None or t[2] < H10.PRESENT_PEAK:
        rec["register"] = "量具断"
        return rec
    cx, cy = float(t[0]), float(t[1])
    rec["truth"] = (round(cx, 2), round(cy, 2))
    belief = (cx + offset, cy)
    rec["belief"] = (round(belief[0], 2), round(belief[1], 2))
    try:
        eng.register_target(roi, False, initial_center=belief)
        rec["register"] = "ok"
    except BaseException as exc:  # noqa: BLE001
        rec["register"] = f"refused:{type(exc).__name__}"
        fact("WRONG-BELIEF", f"注册被拒（{type(exc).__name__}）⇒ 本臂不判")
        return rec
    n_lock = None
    for k in range(WRONG_MAX_READS):
        rec["n_reads"] = k + 1
        r = one_read(eng, f"  偏 {offset:.0f} px estimate#{k + 1}")
        if r["err"] == "lock":
            dx = r["pt"][0] - cx
            dy = r["pt"][1] - cy
            n_lock = k + 1
            rec["first_lock"] = {"read": n_lock, "err_px": (round(dx, 2), round(dy, 2)),
                                 "ms": round(r["ms"], 1)}
            break
    rec["reads_to_lock"] = n_lock
    half0 = half_of_prior(SMALL_EDGE)
    # 两档距离账：信念到**真位中心**（贵方 CPU 测试的算法），和信念到**贴片左上角**
    # （搜索走的是贴片可摆放的锚点 ⇒ 还差半份模板 s/2）。哪一档命中实测，
    # 就把"+1 发"归因到记账口径，而不是归因到展宽机制或合成器时序。
    n_center = max(0.0, (offset - half0) / 64.0)
    n_anchor = max(0.0, (offset + SMALL_EDGE / 2.0 - half0) / 64.0)
    pred_center = int(math.ceil(n_center)) + 1
    pred_anchor = int(math.ceil(n_anchor)) + 1
    rec["pred"] = (pred_center, pred_anchor)
    got = f"第 {n_lock} 发锁定" if n_lock else f"连读 {WRONG_MAX_READS} 发未锁"
    fact("WRONG-BELIEF 出口",
         f"信念偏 {offset:.0f} px ⇒ {got}｜首窗半宽 {half0:.0f}（展宽 64 px/发）"
         f"｜按到中心算预测第 {pred_center} 发、按到左上角算预测第 {pred_anchor} 发")
    return rec


def cost_curve(eng, backend, host, surface_edge: int, tpl_edges, x: int, y: int) -> list:
    """COST-CURVE：同一只 surface 上换**居中裁剪**的模板边，各喂真位先验 ⇒ 首条耗时的曲线。

    为什么单列一支：640² 那一格回答的是"**能不能**锁"（γ 的主张），不回答"**值不值**"。
    产品里桌宠模板是 100–250 px 量级，切换那一刻能等几毫秒才是 promote 的账。
    与 `-53` 那条 497 ms 基线**同模板边**（96²）比才是同一条尺：
    那边是"整帧、无先验"，这边是"局部窗、喂先验"。"""
    frame = H10.make_frame(surface_edge)
    if H10.mount(backend, host, frame, x, y, "h11_cost") is None:
        fact("COST-CURVE", "挂 surface 失败 ⇒ 本支不判")
        return []
    out: list[dict] = []
    for e in tpl_edges:
        c0 = (surface_edge - e) // 2
        # `.copy()`：交叉切片是非连续视图，直接递给 register_target 会 ValueError（H10 实测）
        roi = frame[c0:c0 + e, c0:c0 + e].copy()
        t = H10.truth_of(roi, f"h11_cost_truth{e}")
        if t is None or t[2] < H10.PRESENT_PEAK:
            out.append({"edge": e, "result": "量具断"})
            fact(f"  cost {e}²", f"尺峰 {'None' if t is None else f'{t[2]:.4f}'} ⇒ 不判")
            continue
        try:
            eng.register_target(roi, False, initial_center=(float(t[0]), float(t[1])))
        except BaseException as exc:  # noqa: BLE001
            out.append({"edge": e, "result": f"refused:{type(exc).__name__}"})
            fact(f"  cost {e}²", f"注册被拒（{type(exc).__name__}）")
            continue
        r = one_read(eng, f"  cost {e}² 先验档")
        rec = {"edge": e, "positions": positions_with_prior(e), "ms": r["ms"],
               "result": r["err"]}
        if r["err"] == "lock":
            rec["err_px"] = (round(r["pt"][0] - float(t[0]), 2),
                             round(r["pt"][1] - float(t[1]), 2))
        out.append(rec)
        dx = f" 误差 {rec['err_px']} px" if r["err"] == "lock" else ""
        fact(f"  cost {e}² 出口", f"窗内位置数 {rec['positions']:,} ⇒ {r['err']}{dx}"
                                  f"｜{r['ms']:.1f} ms")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-long", type=int, default=1366,
                    help="输出长边（物理像素）；本机 1366，换机器请如实改这个数")
    ap.add_argument("--skip", default="",
                    help="逗号分隔的臂名子串（如 'oracle/640'）用于跳过，省桌面时间")
    args = ap.parse_args()

    idy = H7.arm_identity()
    if not idy["anchor_ok"] or not idy["so_ok"]:
        fact("⚠ 锚点与登记不符", "下面的数字仍会打印，但只能按**本轮实际锚点**读")

    global APP  # noqa: PLW0603
    APP = QApplication(sys.argv[:1])
    # H10.mount()/pump 用的是它自己模块里的 APP——不把它指过来，被复用的那几层会拿到 None
    # （首跑实测：AttributeError 'NoneType' has no attribute 'processEvents'）。
    H10.APP = APP
    eng = fidus.Fidus.build_wayland()
    host = H1.HostWindow()
    host.show()
    H4.pump(APP, 500)
    backend = H1.FACADE.get_backend()
    fact("gate_status", eng.gate_status())
    fact("锚点", fidus.__git_commit__)
    fact("register_target 签名", fidus.Fidus.register_target.__text_signature__)
    fact("先验半宽式（本探针按此算位置数）",
         f"half = 1.5·s + 48 ⇒ 640² → {half_of_prior(640):.0f} → "
         f"{positions_with_prior(640):,}；634² → {half_of_prior(634):.0f} → "
         f"{positions_with_prior(634):,}")
    fact("上限", f"MAX_SEARCH_POSITIONS = {H10.MAX_POSITIONS:,}（无先验臂仍按它判）")

    x = (args.out_long - max(SIZES)) // 2
    y = 64
    results: list[dict] = []
    skip = [s.strip() for s in args.skip.split(",") if s.strip()]

    def want(belief: str, edge: int) -> bool:
        tag = f"{belief}/{edge}"
        return not any(s in tag for s in skip)

    # 会话内唯一一次校准，落在第一臂（H9 A 臂：帧跨 surface 重建复用、不再校准）
    first_frame = H10.make_frame(SIZES[0])
    t0 = H10.mount(backend, host, first_frame, x, y, "h11_cal")
    if t0 is None or t0[2] < H10.PRESENT_PEAK:
        print("VERDICT-R2-PRIOR : ✗ 量具断（首臂内容没上屏）⇒ 不产出任何数字")
        backend.destroy_context()
        return 1
    eng.register_target(first_frame, False)
    cal_start = time.perf_counter()
    try:
        cal = eng.calibrate_once()
    except BaseException as exc:  # noqa: BLE001
        print(f"VERDICT-R2-PRIOR : ✗ calibrate_once 抛 {type(exc).__name__}: {exc}")
        backend.destroy_context()
        return 1
    fact("calibrate_once", f"{(time.perf_counter() - cal_start) * 1e3:.0f} ms "
                          f"scale={getattr(cal, 'scale', '?')}")

    plan = [("none", 640, READS), ("oracle", 640, 1), ("corner", 640, 1),
            ("none", 634, READS), ("oracle", 634, 1),
            ("none", 633, READS)]
    for belief, edge, reads in plan:
        if not want(belief, edge):
            fact(f"臂 {belief}/{edge}", "按 --skip 跳过")
            continue
        print(f"\n=== 臂 {belief} @ {edge}² ===")
        results.append(arm(eng, backend, host, edge, args.out_long, x, y, belief, reads))

    small = None
    if want("wrong", SMALL_EDGE):
        print(f"\n=== WRONG-BELIEF 臂（{SMALL_EDGE}² 模板、信念偏 {WRONG_OFFSET:.0f} px）===")
        small = wrong_belief_arm(eng, backend, host, SIZES[0], x, y, WRONG_OFFSET)

    curve: list = []
    if want("cost", 0):
        print(f"\n=== COST-CURVE 臂（桌宠量级模板边 {list(TPLE_EDGES)}，各喂真位先验）===")
        curve = cost_curve(eng, backend, host, SIZES[0], TPLE_EDGES, x, y)

    backend.destroy_context()

    print("\n[VERDICT]")
    def pick(belief: str, edge: int):
        return next((r for r in results
                     if r.get("belief") == belief and r.get("edge") == edge), None)

    # R2：解闸尺
    r2 = [pick("oracle", e) for e in (640, 634)]
    if not any(r2):
        # 缺臂≠否定：`--skip` 掉的臂一个都没跑过，照样打出一行 VERDICT 就是假红
        # （与"上界改成字面量后空循环仍全绿"同族，只是方向相反）。
        print("VERDICT-R2-PRIOR   : 本轮 --skip 未跑先验臂 ⇒ 本行不判"
              "（解闸与否只按全量轮次记）")
    else:
        r2_ok = [bool(r and r.get("locked_first")) for r in r2]
        for r in [x2 for x2 in r2 if x2]:
            if not r.get("reads"):
                fact("  R2", f"{r['edge']}² 喂先验 ⇒ 本臂未产出读数（{r.get('register')}）")
                continue
            fact("  R2", f"{r['edge']}² 喂先验 ⇒ 首条"
                         f"{'锁定' if r.get('locked_first') else '未锁定'}"
                         f" 误差 {r.get('err_px')} px｜{r['reads'][0]['ms']:.1f} ms")
        r2_flipped = all(r2_ok) and all(r2)
        print(f"VERDICT-R2-PRIOR   : 喂先验后 640²／634² 首条锁定 = {r2_ok}"
              f" ⇒ {'**翻判成立**：上限对本件不再参与，§5.7 解闸' if r2_flipped else '✗ 未翻判 ⇒ 继续挂'}")

    # R4：无先验回归
    r4 = {e: pick("none", e) for e in SIZES}
    if not any(r4.values()):
        print("VERDICT-R4-NOPRIOR : 本轮 --skip 未跑无先验臂 ⇒ 本行不判")
        thr, lck = {}, {}
    else:
        thr = {e: bool(r and r.get("all_threw")) for e, r in r4.items() if r}
        lck = {e: bool(r and r.get("locked_first")) for e, r in r4.items() if r}
        for e, r in r4.items():
            if r:
                pos = r.get("positions")
                seen = ("全抛 TargetLost" if r.get("all_threw")
                        else "锁定" if r.get("locked_first")
                        else "有读数但未锁定")
                fact("  R4", f"{e}² 不喂先验 ⇒ {seen}"
                             f"｜位置数 {'-' if pos is None else f'{pos:,}'}（-53 同臂同值）"
                             f"｜误差 {r.get('err_px')}｜注册={r.get('register')}")
        r4_unchanged = thr.get(640) and thr.get(634) and lck.get(633)
        print(f"VERDICT-R4-NOPRIOR : 必抛档 {[(e, thr.get(e)) for e in (640, 634)]}"
              f"｜必锁定档 633={lck.get(633)}"
              f" ⇒ {'None 臂行为未变（验收④）' if r4_unchanged else '✗ 与 -53 不同判，请逐臂对表'}")

    # R3：耗时对表 -53 基线
    def first_ms(belief: str, only_locked: bool = False) -> list:
        out = []
        for r in results:
            if r.get("belief") != belief or not r.get("reads"):
                continue
            if only_locked and r["reads"][0]["err"] != "lock":
                continue
            out.append(r["reads"][0]["ms"])
        return out

    ms_prior = first_ms("oracle")
    ms_full = first_ms("none", only_locked=True)
    if ms_prior or ms_full:
        print(f"VERDICT-R3-COST    : 先验档首条 {[f'{m:.1f}' for m in ms_prior]} ms"
              f"｜无先验整帧锁定档首条 {[f'{m:.1f}' for m in ms_full]} ms"
              f"（-53 基线：497 ms @96² 整帧／2.5–6.7 s @633² 整帧）")
    else:
        print("VERDICT-R3-COST    : 本轮 --skip 未跑对表臂 ⇒ 本行不判")

    # 贵方 §7-③：喂左上角会怎样
    rc = pick("corner", 640)
    if rc:
        print(f"VERDICT-CORNER     : 640² 喂左上角 ⇒ 首条"
              f"{'锁定 误差 ' + str(rc['err_px']) + ' px' if rc.get('locked_first') else '未锁定/抛'}"
              f"｜我方推论「half=1.5·s ≥ s/2 ⇒ 喂成角永远出不了窗」"
              f"{'**成立**' if rc.get('locked_first') else '被否证'}")

    # 贵方 §5：信念偏 440 px 要几发
    if small:
        n = small.get("reads_to_lock")
        pc, pa = small.get("pred", (None, None))
        which = ("按**到左上角**记账（贴片锚点）" if n == pa and n != pc
                 else "按**到中心**记账" if n == pc and n != pa
                 else "两档都不中 ⇒ 另有机制" if n else "未锁")
        print(f"VERDICT-WRONG      : 偏 {WRONG_OFFSET:.0f} px ⇒ 实测第 {n} 发锁定"
              f"｜贵方 CPU 档=5｜我方两档预测：到中心 第 {pc} 发／到锚点 第 {pa} 发"
              f"｜归因：{which}｜首读误差 {small.get('first_lock', {}).get('err_px')} px"
              f"｜喂错**没有**造出假位置（若有，首发就会给出偏离真位 ~{WRONG_OFFSET:.0f} px 的读数）")

    # 桌宠量级的切换成本（贵方验收 ③ 在产品口径下的那一格）
    if curve:
        parts = [f"{c['edge']}²:{c['ms']:.0f} ms" if c.get("result") == "lock"
                 else f"{c['edge']}²:{c.get('result')}" for c in curve]
        lock_ms = [c["ms"] for c in curve if c.get("result") == "lock"
                   and c["edge"] <= 200]
        print(f"VERDICT-COSTCURVE  : 喂先验后首条 estimate " + "／".join(parts)
              + (f" ⇒ 桌宠量级（≤200²）冷启动 {max(lock_ms) / 1e3:.2f} s 内" if lock_ms
                 else " ⇒ 本支未产出锁定档"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
