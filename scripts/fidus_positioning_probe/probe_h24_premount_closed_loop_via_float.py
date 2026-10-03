#!/usr/bin/env python3
"""H24 · 预挂载的「可信」判据要先**量**出来，不沿用别人给的那句结论。

出处：人工对「量完再挂」四条裁决里的第四条，原话——
「这条需要实测作为判据，因为"位移闭环在 Wayland 预挂载结构性不可用，用不了"不是我说的（我缺上下文）」

那句话的**出处**是两处材料，都不是本机的判决：
* fidus 裁定（乙）：「位移闭环要求宿主能改变被测内容的视觉位置 ⇒ Wayland 下对任何引擎都不可用」；
* 我方 v7 现场：`pet.move()` 调了、Qt 几何变了、**屏上没动**——而那一场桌宠是**平铺**窗。
平铺窗不被客户端自移，不等于浮动窗不能被移动。所以本件把「移动这一手到底通不通」量出来，
判据按量出来的结果选，不按引用选。

四臂各自只答一个问题
--------------------
* **C · 客户端自移**（浮动态）：`pet.move(+48,0)` 之后用独立尺子量**屏上真实位移**。
  ⇒ 回答「v7 那句到底归因给平铺，还是归因给 Wayland 协议」。产品只有这一只手。
* **L · 合成器代发位移**（浮动态）：`move-floating-window --id <桌宠> --x +48 --y 0`，
  跑完整 `FP.locate()`（好先验 / 信念先验各一遍）。闭环算不算过，判据是**读数差 `b − a`
  对上独立尺子量到的真实位移**——不是对上请求的 48：撞边时合成器给不出 48，那笔账不该记在引擎头上。
* **N · 完全无信念**：连 `select_template` 那一发注册也不喂先验（H23 只在测量那一发不喂）。
  产品要走的就是这一条，量具必须先在这条路上验过：锁对率、选定档、ceiling 有无变化。
  平铺态与浮动态各跑一遍（浮动会改尺寸，分叉与否由在位闸回答，不靠推断）。
* **T · 撒谎档发生率**：只允许 64²、走无信念；冷获取一遍 + 闭环一遍。
  H14 已证 48²/32² 会**安静地**锁孪生峰且照样过闭环；64² 在「量完再挂」这一路是不是同类，
  是「判据里要不要档位下限」的成本读数，不是可选项。

出口写死三条，按实测选一条、不预设
----------------------------------
1. L 通过 ⇒ 预挂载可用**位移闭环**当判据（与 post-mount 同口径）。
2. L 不通过、且 T 显示 64² 会撒谎 ⇒ 判据 = `_settle` 定住 **且** 档位下限 ≥ 96²；`ceiling` 只写日志不判决。
3. 两者之间任何形态 ⇒ 明写「未发现可靠判据」，预挂载只做**候选**、不改挂载顺序。

**L 与 C 两条读数不许合并**：闭环即使被 L 证明"结构上能跑"，产品用不用得上取决于它有没有那一只手。
同理 L 的"过"也不自动等于"能当判据"——信念先验那一发若**过了闭环却锁错**（H14 的孪生峰形状），
判据就得另找。三条出口由人工按出口段那几行点选，脚本不自动定：取舍里有"产品能不能请求浮动"
这类脚本外事实。

用法
----
    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h24_premount_closed_loop_via_float.py

场景与量具与 H23 同源（同一台机、同一素材、生产选尺与冷获取未复刻、相位冻结、独立不透明盒当真值）：
本件**直接 import H23 那一个模块**复用 `template_truth` / `cold_acquire` / `dump_pair` / `boot_pet`，
不复制第二条尺子——两条尺子各量一遍只会多出一族"哪条对"的争议。

副作用（真机）：起一次真桌宠、一次 `calibrate_once`（约 2 s + 屏幕闪）、若干次整屏抓取、
**把桌宠自己的窗口浮动起来**（先按 id 确认聚焦的就是桌宠才敢下 `toggle-window-floating`；
聚焦守卫失败就不动任何窗口）、用 niri 把它左右挪若干发并**逐发挪回**。
不改配置、不落盘、**不挂 layer surface**。
退出码：0 = 跑到出口（结论再否定也是 0）；1 = 量具自身不可用。
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt5.QtWidgets import QApplication  # noqa: E402

import probe_h3_targetability as H3              # noqa: E402  复用 _niri / 聚焦与窗口表
import probe_h4_ceiling_infer as H4              # noqa: E402
import probe_h12_product_path as H12             # noqa: E402
import probe_h15_product_wiring as H15           # noqa: E402
import probe_h23_premount_prior_vs_rung2 as H23  # noqa: E402  复用同一把尺子，不另造
import probe_wd_translucency as WD               # noqa: E402  复用 niri_move（按 id 点名）
from meapet.desktop import fidus_position as FP  # noqa: E402

fact = H23.fact
PRESENT_PEAK = H12.PRESENT_PEAK      # 0.90 在位闸：过不去是量具断
TOL_PX = 2.0                          # 锁对阈值，与 H23 / 产品 `TOL_PX` 同口径
LOOP_TOL_PX = 2.0                     # 闭环：读数差 vs 屏上真实位移 的容差
PET_TITLE = "mea-pet"                 # app.py:268 的窗口标题（keepalive/splash 不带连字符，撞不上）
MOVE_PX = float(FP.MOVE_PX)
H23.DUMP_DIR = Path(os.environ.get("H24_DUMP_DIR", "/tmp/h24_dump"))

# 读数只由实测填；None = 那一臂没跑到或不可判。出口段按这些数选判据。
OUT: dict[str, object] = {
    "float_ok": None, "client_move_px": None,
    "real_move_good": None, "real_move_belief": None, "real_move_t": None,
    "loop_good": None, "loop_belief": None, "loop_t": None,
    "pass_good": None, "pass_belief": None, "pass_t_loop": None,
    "lock_l_good": None, "lock_l_belief": None,
    "lock_n_tiled": None, "lock_n_float": None,
    "lock_t_cold": None, "lock_t_loop": None,
    "edge_n": None, "edge_t": None,
}


def win_row() -> list[dict]:
    return H3._windows_json()


def pet_win_id():
    return next((w.get("id") for w in win_row() if str(w.get("title")) == PET_TITLE), None)


def pet_win(win_id) -> dict:
    return next((w for w in win_row() if w.get("id") == win_id), {})


def win_size(win_id) -> tuple:
    return tuple((pet_win(win_id).get("layout") or {}).get("window_size") or ())


def belief_center_of(pet) -> tuple[float, float]:
    """产品现状那个"信念"：`_layer_geometry()` 合成的 surface 中心（Qt 侧，不经屏幕）。"""
    lx, ly, lw, lh = pet._layer_geometry()
    return (lx + lw / 2.0, ly + lh / 2.0)


def truth_of(box, off, tag: str, samples: int = H23.TRUTH_SAMPLES):
    """独立尺子量「**帧心**在屏上的位置」：连抓 N 张取最优峰。

    回 `(帧心 | None, 最优峰, 各峰值, 那张抓屏)`。抓屏留着是为了**当场**数焦点环底色
    （见 `ring_at`）——焦点状态会改屏上像素，事后拿不到那一帧就补不了这条账。
    """
    ruler, peaks = H23.template_truth(box, tag, samples)
    peak = max(peaks) if peaks else 0.0
    if ruler is None or peak < PRESENT_PEAK:
        return None, peak, peaks, (ruler[3] if ruler else None)
    return (ruler[0] - off[0], ruler[1] - off[1]), peak, peaks, ruler[3]


def ring_at(shot, center, edge: int, tag: str) -> None:
    """帧心矩形里有多少只像素正是 niri 焦点环底色 —— **证明**屏上没被环污染，不是假设。

    出处是 H3 的现场：焦点环画成"贴着窗口外扩 width 的实心矩形、**在窗口底下**"，
    桌宠一聚焦，它的透明区就透出那块 `active-color` ⇒ 量出来的蓝是合成器画的，不是客户端提交的。
    """
    if shot is None:
        fact("焦点环", f"{tag}｜无抓屏可比 ⇒ 不判")
        return
    fact("焦点环", f"{tag}｜帧心 {edge}² 矩形内 #7fc8ff 像素 {H23.decor_inside(shot, center[0], center[1], edge)} 只")


@contextlib.contextmanager
def beliefless(eng: FP.FidusEngine):
    """把 `eng.register` 的先验一律丢掉——生产选尺链一行不改，只是不喂。

    为什么不复刻 `select_template`：复刻它就得把候选枚举一起搬进探针，而 H14 的教训正是
    "探针的候选链与生产的候选链不等价"。这里被换掉的只有 `initial_center` 这一个入参，
    臂间唯一变量就是"喂没喂"。
    """
    real = eng.register
    eng.register = lambda box, _prior=None: real(box, None)     # type: ignore[method-assign]
    try:
        yield
    finally:
        del eng.register                    # 解绑实例属性 ⇒ 回到类上那个真方法


def run_cold(eng, frame, belief, sizes, tag: str):
    """无信念选尺 + 无信念冷获取。回 (候选, ceiling, 帧心读数三元组 | None)。"""
    with beliefless(eng):
        picked = FP.select_template(eng, frame, belief, sizes,
                                    log=lambda k, v: fact(f"    {k}", v))
        if picked is None:
            return None, None, None
        cand, ceiling = picked
        t0 = time.perf_counter()
        status, payload, _ce, secs_reg = H23.cold_acquire(eng, cand.box, None, tag)
        secs = time.perf_counter() - t0
    fact("冷获取耗时", f"{tag}｜注册 {secs_reg * 1e3:.0f} ms + 定住 = 整发 {secs * 1e3:.0f} ms"
                       f"（等待预算要的就是这个数）")
    if payload is None:
        fact("读数", f"{tag} {status} ⇒ 无位置读数")
        return cand, ceiling, None
    x, y, conf = payload
    return cand, ceiling, (x - cand.anchor[0], y - cand.anchor[1], conf)


def lock_line(tag: str, reading, truth, ceiling, edge) -> tuple | None:
    """把一发读数对到独立真值上，记进出口。回锁差 (dx, dy) 或 None。"""
    if reading is None or truth is None:
        fact(tag, "无读数或量具断 ⇒ 不判")
        return None
    dx, dy = reading[0] - truth[0], reading[1] - truth[1]
    ok = max(abs(dx), abs(dy)) <= TOL_PX
    fact(f"{tag}·锁", f"贴片 {edge}² ceiling={ceiling:.6f}｜读数帧心=({reading[0]:.2f},"
                       f"{reading[1]:.2f})｜真值帧心=({truth[0]:.2f},{truth[1]:.2f})｜差="
                       f"({dx:+.2f},{dy:+.2f}) px {'✓ 锁对' if ok else '✗ 锁错'}｜"
                       f"conf==ceiling: {abs(reading[2] - ceiling) < 1e-12}")
    return (dx, dy)


def make_floating(app, pet):
    """把桌宠请求成浮动窗，然后把**焦点交还原窗口**。两道守卫各挡一件事：

    * `toggle-window-floating` 的作用对象是"当前聚焦窗口" ⇒ 聚焦对不上就不动任何窗口
      （那是用户的终端，不该由我改动）；
    * 焦点环（H3 现场）：桌宠一聚焦，它的透明区就透出合成器画的 `active-color` 实心矩形，
      屏上真值会量到那块蓝而不是客户端提交的像素。浮动与置顶不依赖焦点，所以交还焦点
      不影响后面各臂——反而是后面各臂的前提。
    """
    win = pet_win_id()
    if win is None:
        fact("浮动", "✗ niri 窗口表里没有 title=='mea-pet' 的窗口 ⇒ 无从点名")
        return None
    orig = H3._focused_win_id()
    orig_title = next((w.get("title") for w in win_row() if w.get("id") == orig), None)
    fact("原聚焦", f"id={orig} title={orig_title!r}（结束时把焦点交还给它）")

    def give_back() -> None:
        if orig is not None and orig != win:
            fact("焦点交还原窗口", f"{H3._focus_window(orig)}｜桌宠失焦="
                                  f"{not H3._is_focused(win)}")
            H4.pump(app, 700)

    focus = H3._focused_is_pet(pet.width(), pet.height())
    if str(focus.get("title")) != PET_TITLE:
        got = H3._focus_window(win)
        H4.pump(app, 600)
        focus = H3._focused_is_pet(pet.width(), pet.height())
        fact("按 id 聚焦桌宠", f"{got}｜现在聚焦={focus.get('title')!r}")
    if str(focus.get("title")) != PET_TITLE:
        fact("浮动", f"✗ 聚焦的不是桌宠（{focus.get('title')!r}）⇒ 不对别的窗口动手，C/L/T 臂不判")
        give_back()
        return None
    fact("浮动前", f"id={win}｜is_floating={pet_win(win).get('is_floating')}｜"
                   f"合成器给的尺寸={win_size(win)}｜Qt 请求={pet.width()}×{pet.height()}")
    fact("请求浮动", H3._niri("toggle-window-floating"))
    H4.pump(app, 900)          # niri 默认开窗口动画，不等够会把动画当成"窗口长这样"
    if not pet_win(win).get("is_floating"):
        fact("浮动", "✗ 请求下去却没浮起来 ⇒ C/L/T 臂不判（平铺下合成器不接受按 id 挪窗）")
        give_back()
        return None
    fact("浮动后", f"is_floating=True｜合成器给的尺寸={win_size(win)}｜"
                   f"Qt 顶层=({pet.x()},{pet.y()}) {pet.width()}×{pet.height()}")
    give_back()
    return win


class Mover:
    """位移交给**合成器**代发。`locate` 的契约要求"提交之后才返回"，所以每发之后泵一拍。"""

    def __init__(self, app, win_id) -> None:
        self.app, self.win = app, win_id
        self.requested = [0.0, 0.0]
        self.notes: list[str] = []

    def __call__(self, dx: float, dy: float) -> None:
        out = WD.niri_move(self.win, int(round(dx)), int(round(dy)))
        self.requested = [self.requested[0] + dx, self.requested[1] + dy]
        self.notes.append(f"请求 ({dx:+.0f},{dy:+.0f}) → {out}")
        H4.pump(self.app, 400)

    def restore(self) -> None:
        dx, dy = self.requested
        if (dx, dy) == (0.0, 0.0):
            return
        fact("挪回原位", WD.niri_move(self.win, int(round(-dx)), int(round(-dy))))
        self.requested = [0.0, 0.0]
        H4.pump(self.app, 500)


READ_RE = re.compile(r"第 \d+ 发定住 \((-?[\d.]+),(-?[\d.]+)\)")


def read_deltas(lines: list[tuple[str, str]]) -> list[tuple[float, float]]:
    """从 `locate` 的日志里取出 `_settle` 那两发定住读数（a 在前、b 在后）。

    标签在**值**里不在键里：`_settle` 发的是 `("读数", "获取 第 2 发定住 (x,y)")`，
    键恒为 `读数`，"获取/闭环"是值的前缀。按键筛会一发也取不到。
    """
    out = []
    for k, v in lines:
        if k != "读数" or not ("获取" in v or "闭环" in v):
            continue
        m = READ_RE.search(v)
        if m:
            out.append((float(m.group(1)), float(m.group(2))))
    return out


def loop_verdict(loop, real_disp) -> bool | None:
    """闭环算不算过：**读数差对上屏上真实位移**，不对上请求的 48。

    撞边时合成器给不出 48，那笔账不该记在引擎头上；反过来读数差对上请求、屏上却没动，
    才是闭环该抓的那种假（v7 就是这个形状）。
    """
    if loop is None or real_disp is None:
        return None
    return (abs(loop[0] - real_disp[0]) <= LOOP_TOL_PX
            and abs(loop[1] - real_disp[1]) <= LOOP_TOL_PX)


def run_locate(eng, frame, prior, base, ruler_box, ruler_off, win, app, tag: str,
               sizes: tuple = FP.SIZES, mover=None, dump_prefix: str = "h24",
               truth_fn=None, deltas_out=None):
    """跑一发完整 `FP.locate`。默认位移交给**合成器**代发（niri CLI）。

    `mover` 是注入点：H25 要问的是"**产品自己的手**能不能摆出一个已知位移"，那一发必须
    换掉这只手而**不**换掉对账逻辑——闭环的两档容差、真实位移的先后顺序、挪回核验，
    两件共用同一段代码，否则 H24 与 H25 的数字不可比。

    `truth_fn` 是同一个注入点换到**尺子**上（签名与 `truth_of` 一致）。孪生峰在场时
    （H25 臂 D）整幅 argmax 自己决定报哪一只，而诱饵是注册模板的**逐字节副本**、峰不低
    ⇒ "屏上真实位移"成了薛定谔的数。那一发改成把 ROI 指到**真那一只**，否则对账对象本身
    含混，抓到没抓到都读不出来。默认仍是整幅 argmax，H24 自己的数字一个字节不变。

    `deltas_out` 是把日志里那两发定住读数 (a,b) 旁路带出来：闭环不过时 `locate` 回 `None`，
    而臂 D 要的恰恰是"它到底读到了哪一只"——`fix is None` 时更需要这两个数。

    回 `(Fix | None, 读数差 b−a | None, 屏上真实位移 | None, 挪回后的帧心 | None)`。
    「先量真实位移、再挪回」这个顺序是**判据本身**：挪回之后再量只能得到 ~0，
    那一发的 `b − a` 就会莫名其妙地对上 48。
    """
    mover = Mover(app, win) if mover is None else mover
    measure = truth_of if truth_fn is None else truth_fn
    log_lines: list[tuple[str, str]] = []

    def log(k: str, v: str) -> None:
        log_lines.append((k, v))
        fact(f"    {k}", v)

    t0 = time.perf_counter()
    fix = FP.locate(eng, frame, prior, sizes=sizes, move=mover, log=log)
    fact("locate 整发", f"{tag}｜{(time.perf_counter() - t0) * 1e3:.0f} ms（等待预算的实测口径）")
    for note in mover.notes:
        fact("位移", f"{tag}｜{note}")
    moved, mpeak, _ , mshot = measure(ruler_box, ruler_off, f"{dump_prefix}_{tag}", samples=2)
    real_disp = None if moved is None else (moved[0] - base[0], moved[1] - base[1])
    if real_disp is None:
        fact("屏上真实位移", f"{tag}｜✗ 不可判（峰 {mpeak:.4f}）")
    else:
        fact("屏上真实位移", f"{tag}｜({real_disp[0]:+.2f},{real_disp[1]:+.2f}) px vs 请求 "
                             f"(+{MOVE_PX:.0f},0)｜峰={mpeak:.4f}")
        ring_at(mshot, moved, ruler_box.shape[0], tag)
    mover.restore()
    back, bpeak, _ = measure(ruler_box, ruler_off, f"{dump_prefix}_{tag}_back", samples=2)[:3]
    if back is None:
        fact("复原核验", f"{tag}｜✗ 挪回后在位闸不过（峰 {bpeak:.4f}）⇒ 该发基准不更新")
    deltas = read_deltas(log_lines)
    if deltas_out is not None:
        deltas_out.extend(deltas)
    loop = (deltas[1][0] - deltas[0][0], deltas[1][1] - deltas[0][1]) if len(deltas) == 2 else None
    if loop is None:
        fact("闭环", f"{tag}｜日志里凑不出 a/b 两发（拿到 {len(deltas)} 发）⇒ 读数差不可判")
    else:
        # 两档容差都要报：本件按 LOOP_TOL_PX=2 判"闭环真的对上了移动"，而产品 `locate` 用的是
        # FP.TOL_PX = MOVE_PX//2 = 24 ⇒ 同一发读数可以一档过一档不过，这一格差**就是**判据之差。
        prod = abs(loop[0] - MOVE_PX) <= FP.TOL_PX and abs(loop[1]) <= FP.TOL_PX
        strict = abs(loop[0] - MOVE_PX) <= LOOP_TOL_PX and abs(loop[1]) <= LOOP_TOL_PX
        fact("闭环读数差", f"{tag}｜b−a=({loop[0]:+.2f},{loop[1]:+.2f}) px｜本件 {LOOP_TOL_PX} px 档"
                          f"{'过' if strict else '不过'}｜产品 {FP.TOL_PX} px 档"
                          f"{'过' if prod else '不过'}｜locate "
                          f"{'回 Fix' if fix is not None else '回 None'}")
    return fix, loop, real_disp, back


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="CLNT",
                    help="要跑哪些臂：C=客户端自移 L=闭环 N=无信念全链 T=64² 撒谎档")
    args = ap.parse_args()
    arms = {a.upper() for a in args.arms if a.upper() in "CLNT"}

    app = QApplication.instance() or QApplication(sys.argv)
    fact("锚点", __import__("fidus").__git_commit__)
    fact("被测代码", f"{FP.__file__}（生产选尺 / 冷获取 / locate 闭环，未复刻）")
    fact("臂", " ".join(sorted(arms)))

    sc, unit_line = H15.read_units(app)
    fact("合成器单位", unit_line)
    if sc != sc or abs(sc - 1.0) > 1e-9:
        print(f"VERDICT-H24 : ✗ 尺子按物理 px、引擎按逻辑 px，只在 scale=1.0 对表；当前 {sc} ⇒ 不跑，不猜")
        return 1

    pet = H23.boot_pet(app)
    if pet is None:
        print(f"VERDICT-H24 : ✗ {H23.BOOT_TIMEOUT} s 内拿不到离屏帧 ⇒ 量具自身失效")
        return 1
    widget = pet.sprite_label
    widget._draw_model = widget.l2d.model.Draw      # type: ignore[method-assign]
    H4.pump(app, 400)
    fact("相位", "`_draw_model` 只留 `model.Draw()` ⇒ 显示与离屏同源同相（与 H23 同一冻法）")

    pet._set_layer_mode(False)
    H4.pump(app, 1500)
    frame = pet._fidus_current_frame()
    if frame is None:
        print("VERDICT-H24 : ✗ 交互态取不到帧 ⇒ 量具断")
        return 1
    belief = belief_center_of(pet)
    fact("态", f"交互态｜Qt 顶层=({pet.x()},{pet.y()}) {pet.width()}×{pet.height()}｜"
               f"帧 {frame.shape[1]}×{frame.shape[0]}｜信念中心=({belief[0]:.2f},{belief[1]:.2f})")

    ruler_box, ruler_off = H12.probe_box(frame)
    if ruler_box is None:
        print("VERDICT-H24 : ✗ 独立尺子选不出盒 ⇒ 量具自身失效")
        return 1
    base, peak, peaks, shot = truth_of(ruler_box, ruler_off, "h24_tiled")
    fact("在位闸·平铺", f"盒 {ruler_box.shape[0]}² 峰值 {[f'{p:.4f}' for p in peaks]} ⇒ {peak:.4f} "
                       f"{'✓' if base is not None else '✗'}")
    if base is None:
        H23.dump_pair("h24_visible_fail_tiled", frame, shot, ruler_box)
        print("VERDICT-H24 : ✗ 平铺态在位闸不过 ⇒ 全案不产出读数（量具断，不是被测物错）")
        return 1
    pet_id = pet_win_id()
    fact("焦点·平铺", f"桌宠 id={pet_id} 是否聚焦={H3._is_focused(pet_id) if pet_id else '读不到窗 id'}")
    ring_at(shot, base, ruler_box.shape[0], "平铺")
    fact("真值·平铺", f"帧心屏上=({base[0]:.2f},{base[1]:.2f})｜峰={peak:.4f}｜信念误差="
                      f"({belief[0] - base[0]:+.2f},{belief[1] - base[1]:+.2f}) px")

    eng = FP.FidusEngine()
    t_cal = time.perf_counter()
    eng.calibrate()
    fact("calibrate", f"{(time.perf_counter() - t_cal) * 1e3:.0f} ms（一次会话一发）")

    # ───────────────────────────────────── 臂 N（平铺态，不动窗）
    if "N" in arms:
        print("\n=== 臂 N · 连 select_template 也不喂先验（平铺态） ===")
        cand, ceiling, reading = run_cold(eng, frame, belief, FP.SIZES, "N·平铺")
        if cand is None:
            fact("N·平铺", "生产选尺全被拒 ⇒ 不可判")
        else:
            OUT["edge_n"] = cand.edge
            OUT["lock_n_tiled"] = lock_line("N·平铺", reading, base, ceiling, cand.edge)

    # ───────────────────────────────────── 浮动：C / L / T 的共同前提
    win = None
    if arms & {"C", "L", "T"}:
        print("\n=== 浮动（C/L/T 的共同前提） ===")
        win = make_floating(app, pet)
        OUT["float_ok"] = win is not None
    truth_float = None
    if win is not None:
        ask = (pet.width(), pet.height())
        got_size = win_size(win)
        if got_size and got_size != ask:
            fact("尺寸分叉", f"合成器给 {got_size[0]}×{got_size[1]} vs Qt 请求 {ask[0]}×{ask[1]}"
                             " ⇒ 屏上内容与帧是否同尺度，由下面的在位闸回答，不靠推断")
        belief = belief_center_of(pet)
        cand_f, reading_f = None, None
        got, peak_f, p_peaks, shot_f = truth_of(ruler_box, ruler_off, "h24_float")
        fact("在位闸·浮动", f"峰值 {[f'{p:.4f}' for p in p_peaks]} ⇒ {peak_f:.4f}")
        if got is None:
            H23.dump_pair("h24_visible_fail_float", frame, shot_f, ruler_box)
            fact("浮动后", f"✗ 在位闸不过（峰 {peak_f:.4f}）⇒ 浮动把屏上内容改到与帧不同源，"
                           "N(浮动)/C/L/T 不判")
        else:
            truth_float = got
            ring_at(shot_f, got, ruler_box.shape[0], "浮动")
            fact("真值·浮动", f"帧心屏上=({truth_float[0]:.2f},{truth_float[1]:.2f})｜与平铺之差="
                              f"({truth_float[0] - base[0]:+.2f},{truth_float[1] - base[1]:+.2f}) px")
            fact("信念·浮动", f"信念中心=({belief[0]:.2f},{belief[1]:.2f})｜先验误差="
                              f"({belief[0] - truth_float[0]:+.2f},{belief[1] - truth_float[1]:+.2f}) px")

            # ───────────────────── 臂 N（浮动态再量一遍：产品若就在浮动下测，走的是这一条）
            if "N" in arms:
                print("\n=== 臂 N · 无信念全链（浮动态） ===")
                cand_f, ceiling_f, reading_f = run_cold(eng, frame, belief, FP.SIZES, "N·浮动")
                if cand_f is not None:
                    OUT["lock_n_float"] = lock_line("N·浮动", reading_f, truth_float,
                                                    ceiling_f, cand_f.edge)

            # ───────────────────── 臂 C · 客户端自移（浮动态）
            if "C" in arms:
                print("\n=== 臂 C · 客户端自己 move（浮动态） ===")
                qx, qy = pet.x(), pet.y()
                pet.move(int(round(qx + MOVE_PX)), int(round(qy)))
                H4.pump(app, 800)
                moved, cpeak, c_peaks, _cshot = truth_of(ruler_box, ruler_off, "h24_c", samples=2)
                fact("Qt 自报", f"move 前 ({qx},{qy}) → 后 ({pet.x()},{pet.y()}) ⇒ Qt 说动了 "
                                f"({pet.x() - qx:+d},{pet.y() - qy:+d}) px（v7 就是这一格）")
                if moved is None:
                    fact("C", f"✗ 挪完在位闸不过（峰 {cpeak:.4f},峰值 "
                              f"{[f'{p:.4f}' for p in c_peaks]}）⇒ 不可判")
                else:
                    dxy = (moved[0] - truth_float[0], moved[1] - truth_float[1])
                    OUT["client_move_px"] = dxy
                    fact("C·屏上真实位移", f"({dxy[0]:+.2f},{dxy[1]:+.2f}) px vs 请求 (+{MOVE_PX:.0f},0)")
                pet.move(int(round(qx)), int(round(qy)))
                H4.pump(app, 500)
                back, bpeak, _ , _bshot = truth_of(ruler_box, ruler_off, "h24_c_back", samples=2)
                if back is not None:
                    truth_float = back
                    fact("C·复原", f"基准更新为 ({back[0]:.2f},{back[1]:.2f})（自移若有效这发才挪得回来）")
                else:
                    fact("C·复原", f"✗ 在位闸不过（峰 {bpeak:.4f}）⇒ 基准不更新，后面各发按旧基准")

            # ───────────────────── 臂 L · 合成器代发位移 + 完整闭环（浮动态）
            if "L" in arms:
                print("\n=== 臂 L · 合成器代发的位移闭环（浮动态） ===")
                good = reading_f[:2] if reading_f is not None else None
                if good is None:
                    fact("L·好先验", "✗ 浮动态那一发无信念冷读数没拿到 ⇒ 好先验这一臂跳过"
                                    "（不借独立尺子的真值当先验，那是 oracle）")
                for tag, prior, k_lock, k_loop, k_real, k_pass in (
                        ("好先验", good, "lock_l_good", "loop_good", "real_move_good", "pass_good"),
                        ("信念先验", belief, "lock_l_belief", "loop_belief",
                         "real_move_belief", "pass_belief")):
                    print(f"\n--- L · {tag}（{('surface 中心=(%.2f,%.2f)' % prior) if prior else '无'}）---")
                    if prior is None:
                        continue
                    fact("喂的先验", f"{tag}｜对独立真值误差="
                                    f"({prior[0] - truth_float[0]:+.2f},{prior[1] - truth_float[1]:+.2f}) px")
                    fix, loop, real_disp, back = run_locate(eng, frame, prior, truth_float,
                                                            ruler_box, ruler_off, win, app,
                                                            f"L·{tag}")
                    OUT[k_loop], OUT[k_real] = loop, real_disp
                    OUT[k_pass] = loop_verdict(loop, real_disp)
                    if back is not None:
                        truth_float = back
                    if fix is None:
                        fact(f"L·{tag}", "locate 回 None ⇒ 该臂无读数")
                        continue
                    OUT[k_lock] = lock_line(f"L·{tag}", (fix.center[0], fix.center[1], fix.conf),
                                            truth_float, fix.ceiling, fix.edge)

            # ───────────────────── 臂 T · 只允许 64²（撒谎档成本读数）
            if "T" in arms:
                print("\n=== 臂 T · 只允许 64²（浮动态：冷一遍 + 闭环一遍） ===")
                cand_t, ceiling_t, reading_t = run_cold(eng, frame, belief, (64,), "T·冷")
                if cand_t is None:
                    fact("T·冷", "64² 档全被拒 ⇒ 本档不可用（响亮退回，属安全一侧）")
                else:
                    OUT["edge_t"] = cand_t.edge
                    OUT["lock_t_cold"] = lock_line("T·冷", reading_t, truth_float,
                                                   ceiling_t, cand_t.edge)
                    if reading_t is not None and win is not None:
                        fact("T·闭环", "带闭环再跑一遍——H14 的 48²/32² 正是**过了闭环却锁错**，"
                                       "本臂要的就是这一格")
                        fix_t, loop_t, real_t, back_t = run_locate(
                            eng, frame, (reading_t[0], reading_t[1]), truth_float,
                            ruler_box, ruler_off, win, app, "T·闭环", sizes=(64,))
                        OUT["loop_t"], OUT["real_move_t"] = loop_t, real_t
                        OUT["pass_t_loop"] = loop_verdict(loop_t, real_t)
                        if back_t is not None:
                            truth_float = back_t
                        if fix_t is not None:
                            OUT["lock_t_loop"] = lock_line("T·闭环",
                                                           (fix_t.center[0], fix_t.center[1], fix_t.conf),
                                                           truth_float, fix_t.ceiling, fix_t.edge)
    elif arms & {"C", "L", "T"}:
        fact("C/L/T", "没浮起来 ⇒ 三臂全部不判（平铺下合成器不接受按 id 挪窗，v7 那一格已实证）")

    # ───────────────────────────────────── 出口
    print("\n=== 出口 ===")
    print(f"VERDICT-FLOAT : {'浮动成功' if OUT['float_ok'] else '未浮动/未跑'}｜平铺在位闸 ✓｜"
          f"浮动在位闸 {'✓' if truth_float is not None else ('✗' if OUT['float_ok'] else '未测')}")
    if OUT["client_move_px"] is not None:
        cx, cy = OUT["client_move_px"]
        print(f"VERDICT-C : 浮动态下客户端自移 → 屏上真实位移 ({cx:+.2f},{cy:+.2f}) px vs 请求 "
              f"(+{MOVE_PX:.0f},0) ⇒ 这一只手{'**有效**' if abs(cx - MOVE_PX) <= LOOP_TOL_PX else '**无效**'}"
              f"｜{'平铺不是唯一成因' if abs(cx - MOVE_PX) <= LOOP_TOL_PX else 'v7 那一格在浮动态复现'}")
    else:
        print("VERDICT-C : 未跑/不可判")
    for k, label in (("real_move_good", "L-好先验"), ("real_move_belief", "L-信念先验"),
                     ("real_move_t", "T-64²")):
        v = OUT[k]
        if v is None:
            print(f"VERDICT-{label}-MOVE : 未跑/不可判")
        else:
            print(f"VERDICT-{label}-MOVE : 合成器代发 → 屏上真实位移 ({v[0]:+.2f},{v[1]:+.2f}) px "
                  f"vs 请求 (+{MOVE_PX:.0f},0) ⇒ 这一只手"
                  f"{'**有效**' if abs(v[0] - MOVE_PX) <= LOOP_TOL_PX else '**没给够**（撞边或动画未提交）'}")
    for k_loop, k_pass, label in (("loop_good", "pass_good", "L-好先验-闭环"),
                                  ("loop_belief", "pass_belief", "L-信念先验-闭环"),
                                  ("loop_t", "pass_t_loop", "T-64²-闭环")):
        loop, passed = OUT[k_loop], OUT[k_pass]
        if loop is None:
            print(f"VERDICT-{label} : 读数差不可判")
        else:
            print(f"VERDICT-{label} : b−a=({loop[0]:+.2f},{loop[1]:+.2f}) px ⇒ "
                  f"{'过（对上屏上真实位移）' if passed else ('不过' if passed is False else '无真实位移可对照')}")
    for key, label in (("lock_l_good", "L-好先验-锁"), ("lock_l_belief", "L-信念先验-锁"),
                       ("lock_n_tiled", "N-平铺-锁"), ("lock_n_float", "N-浮动-锁"),
                       ("lock_t_cold", "T-64²冷-锁"), ("lock_t_loop", "T-64²闭环-锁")):
        v = OUT[key]
        if v is None:
            print(f"VERDICT-{label} : 无读数/不可判")
        else:
            print(f"VERDICT-{label} : 差 ({v[0]:+.2f},{v[1]:+.2f}) px ⇒ "
                  f"{'锁对' if max(abs(v[0]), abs(v[1])) <= TOL_PX else '锁错'}")
    lying_t = (OUT["pass_t_loop"] is True and OUT["lock_t_loop"] is not None
               and max(abs(OUT["lock_t_loop"][0]), abs(OUT["lock_t_loop"][1])) > TOL_PX)
    print(f"VERDICT-T-LIES : {'64² 过闭环却锁错 ⇒ 撒谎档，判据需要档位下限' if lying_t else ('未跑/不可判' if OUT['pass_t_loop'] is None else '本轮未见 64² 撒谎（发生率另计，单场不是率）')}")
    print("VERDICT-CRITERION : 判据不在脚本里自动定——三条出口的取舍含「产品有没有那一只手」"
          "（C 与 L 两条不许合并）这类脚本外事实，由人工按上面几行点选")
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
