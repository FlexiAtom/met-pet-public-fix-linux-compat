#!/usr/bin/env python3
"""H25 · 判据要落在**产品伸得到的手**上：把内容在自己窗口里挪一下，闭环认不认。

出处：H24 四场之后人工的一句话——「预挂载没有产品可用的自证判据，把量完再挂卡了，
所以应该找判据，而不是跳到量完再挂」。这句是对的，H24 的三条出口把"移动这一手"
只剩两种可能：客户端自移窗（臂 C，实测 0 px）与合成器代发挪窗（臂 L，产品没有这只手，
那是 niri CLI）。**但这个枚举漏掉了第三种：产品控得住自己画什么。**

为什么这只手成立（先讲清坐标语义，否则整件读起来像绕圈）
----------------------------------------------------------
`fidus_position.py:5` 自陈 fidus 是**屏幕贴片匹配引擎**：产品喂给它的帧只当**模板源**
（`iter_candidates` 从离屏帧里裁贴片，`register_target(box,...)` 注册的是那块贴片），
而**搜索发生在 fidus 自己抓的那一张屏上**（`eng.calibrate_once()` 那 2 s + 屏幕闪就是为此）。
于是两件事同时成立：

* 读数本来就是**屏上坐标**，不经过 `_layer_geometry()` 那个谎言——这解释了 H23 臂 B /
  H24 臂 N 为什么能在信念误差 (−227,+342) 的情况下仍对独立尺子锁到 ≤0.3 px；
* 把**子控件在顶层窗口里平移**（`sprite_label` 是 `SpriteCanvas(self)` 的裸子控件，
  `render_host.py:541/1634`，不经布局器），屏上内容就跟着挪，而**顶层窗口一步没动**、
  离屏帧一字节没变。挪的是像素，不是窗口。

所以这一件量的判据是：**同一块已注册贴片，在"我方已知自移"前后各做一次冷获取，
读数差对得上屏上真实位移吗**。它比 H24 那两只手多证一件事——若屏上内容与离屏帧
**不同尺度**（分数缩放/合成器改尺度那一族），子控件挪 48 逻辑 px，屏上会挪 48×scale，
读数差就不对上 48；而"挪窗"那只手对尺度分歧是**免疫**的，量不出这一格。

五臂各答一问（另有一臂 N = 无信念全链，是 G/D 的先验来源，不是可选臂）
--------------------------------------------------------------------
* **H · 手有效性**（单独量，不混进闭环）：`widget.move(+48,0)` 之后独立尺子量到的真实位移
  是多少；同时比对**挪前/挪后的离屏帧 sha256**——帧没变、屏上变了，这一发才是**外接见证**
  而不是自我循环（"我把挪过的帧喂给它，它当然跟得上"那种绿不算）。
* **G · 闭环·好先验**：完整 `FP.locate` 走这只手，先验取臂 N 的无信念冷读数（不借独立尺子的
  真值当先验，那是 oracle）。判据要的是"锁对时闭环过"。
* **B · 闭环·信念先验**：先验换成产品现状那个谎言中心。H24 臂 L 在这一发上量到
  `b−a=(+47.98,−3.06)`——**2 px 档不过、产品 24 px 档过**，于是 `locate` 回了一个偏 +2.79 px 的
  Fix。本臂问的是：**换成这只手，那一发还漏不漏**。
* **T · 撒谎档**：只允许 64²。H14 已证 48²/32² **过了闭环却锁错**（真孪生峰，闭环对它结构失明）。
  64² 在这只手下是不是同类，就是"档位下限要不要进判据"的成本读数。
* **D · 屏上真孪生峰（判别力的那一发样本）**：把 `cand.box` —— 注册模板**本身** —— 贴成窗口里
  另一只**静止**的复制品（`QLabel` 子控件，落在不与子控件相交的空白区），然后在**不喂先验**
  （产品「量完再挂」那条路）下跑完整闭环。诱饵不动、真身被这只手挪 +48 ⇒
  读数若跟到诱饵上，`b−a` 必为 0 而屏上真实位移是 48 ⇒ 那一发就是判据**该响**的样本。
  两只同时在场时整幅 argmax 会自己挑一只报（诱饵与模板逐字节相同，峰不低）⇒ 这一臂把尺子
  改成 ROI 问法：`truth_fn` 指定"问真身那一只"，抓到没抓到才读得出来。

判别力怎么算（不许把"没出错"冒充"能抓错"）
------------------------------------------
按 (锁对/锁错) × (闭环过/不过) 四格记账：
* 出现"锁错 & 闭环不过" ⇒ 这只手**抓到**错锁，判据有判别力（本轮实测范围内）；
* 出现"锁错 & 闭环过" ⇒ 判据**不成立**，H14 那个形状在本手复现；
* 一格"锁对 & 闭环不过" ⇒ 误伤：会把好读数拒掉，代价要写进代码脸；
* **没有任何错锁样本 ⇒ 判别力未测**，明写。臂 D 就是为造出这一格样本而做的诱饵臂；
  它摆不下、上不了屏、或读数仍跟着真身，都各自记一条"仍未测"，不许合并成"能抓错"。

用法
----
    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h25_inwindow_content_shift.py

场景/尺子/冻相/对账逻辑与 H23、H24 同源：直接 import 那两个模块，闭环那套两档容差记账
复用 `H24.run_locate(mover=…, truth_fn=…, deltas_out=…)`（H24 为此开了三个注入口）——两只手
若各写一遍对账，H24 与 H25 的数字就不可比；`truth_fn` 换的是**问哪一只**，默认仍是整幅 argmax，
H24 自己的四场数字一个字节不变。

副作用（真机）：起一次真桌宠、一次 `calibrate_once`（约 2 s + 屏幕闪）、若干次整屏抓取、
**把桌宠的 Live2D 子控件在窗口内左右挪若干发并逐发挪回**（用户会看见桌宠在窗口里横向
滑动一下；顶层窗口与合成器状态都不动），臂 D 另外会在窗口空白处**贴一张与注册模板逐字节
相同的复制品**再撤掉（用户会看见桌宠旁边多出一块方片）。**不动焦点、不动窗口、不调 niri 的
改位动作**——交焦点会滚动平铺视野把桌宠推出屏（见 `focus_report`），比它要清的环更伤。
不改配置、不落盘、不挂 layer surface。
退出码：0 = 跑到出口（结论再否定也是 0）；1 = 量具自身不可用。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt5.QtWidgets import QApplication  # noqa: E402

import numpy as np                                     # noqa: E402
import probe_a10_geometry as A10              # noqa: E402  独立尺子的抓屏与相关器
import probe_h4_ceiling_infer as H4           # noqa: E402
import probe_h8_roi_on_screen as H8           # noqa: E402  luma_of：与尺子同一个亮度面
import probe_h12_product_path as H12          # noqa: E402
import probe_h15_product_wiring as H15        # noqa: E402
import probe_h23_premount_prior_vs_rung2 as H23  # noqa: E402  同一把尺子
import probe_h24_premount_closed_loop_via_float as H24  # noqa: E402  复用闭环对账逻辑
from meapet.desktop import fidus_position as FP  # noqa: E402

fact = H23.fact
TOL_PX = H24.TOL_PX                # 2.0：锁对阈值（与独立真值比）
LOOP_TOL_PX = H24.LOOP_TOL_PX      # 2.0：闭环阈值（读数差 vs 屏上真实位移）
MOVE_PX = H24.MOVE_PX              # 48：`FP.MOVE_PX`，产品自己请求的那个数
H23.DUMP_DIR = Path(os.environ.get("H25_DUMP_DIR", "/tmp/h25_dump"))

OUT: dict[str, object] = {
    "hand_px": None,            # 臂 H：子控件自移后屏上真实位移
    "frame_identical": None,    # 臂 H：挪前/挪后离屏帧是否一字节不变
    "clip_slack": None,         # 前置事实：窗口里还剩多少余量可挪
    "lock_n": None,             # 臂 N：无信念全链的锁差（G 的先验来源）
    "loop_good": None, "real_good": None, "pass_good": None, "lock_good": None,
    "loop_belief": None, "real_belief": None, "pass_belief": None, "lock_belief": None,
    "loop_t": None, "real_t": None, "pass_t_loop": None,
    "lock_t_cold": None, "lock_t_loop": None,
    "edge_n": None, "edge_t": None,
    "loop_d": None, "real_d": None, "pass_d": None, "lock_d": None,
    "took_decoy": None,         # 臂 D：读数落在诱饵那一只上吗
}


class ChildMover:
    """位移 = 把 Live2D 子控件在**顶层窗口内**平移。产品自己的手：不经合成器、不经 CLI、不经权限。

    `locate` 的契约要求"提交之后才返回" ⇒ 每发之后泵一拍（niri 的窗口动画与此无关，
    这一发改的是客户端自己提交的像素，但 Qt 的重绘与合成器收帧仍要时间）。
    与 `H24.Mover` 同签名（`__call__(dx,dy)` / `restore()` / `requested` / `notes`），
    因此能直接塞进同一套对账逻辑。
    """

    def __init__(self, app, widget) -> None:
        self.app, self.w = app, widget
        self.orig = (int(widget.x()), int(widget.y()))
        self.requested = [0.0, 0.0]
        self.notes: list[str] = []

    def __call__(self, dx: float, dy: float) -> None:
        before = (int(self.w.x()), int(self.w.y()))
        self.w.move(before[0] + int(round(dx)), before[1] + int(round(dy)))
        after = (int(self.w.x()), int(self.w.y()))
        self.requested = [self.requested[0] + dx, self.requested[1] + dy]
        self.notes.append(f"Qt 子控件 {before} → {after}（请求 ({dx:+.0f},{dy:+.0f})）")
        H4.pump(self.app, 600)

    def restore(self) -> None:
        if self.requested == [0.0, 0.0]:
            return
        self.w.move(self.orig[0], self.orig[1])
        fact("挪回原位", f"子控件回到 {self.orig}｜现在=({int(self.w.x())},{int(self.w.y())})")
        self.requested = [0.0, 0.0]
        H4.pump(self.app, 600)


def sha_of(arr) -> str:
    import numpy as np

    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()[:16]


def run_locate25(eng, frame, prior, base, ruler_box, ruler_off, widget, app, tag, sizes=FP.SIZES,
                 truth_fn=None, deltas_out=None):
    """把 H24 那套闭环对账接到**这只手**上。回 (Fix|None, b−a|None, 真实位移|None, 挪回后基准|None)。"""
    return H24.run_locate(eng, frame, prior, base, ruler_box, ruler_off, None, app, tag,
                          sizes=sizes, mover=ChildMover(app, widget), dump_prefix="h25",
                          truth_fn=truth_fn, deltas_out=deltas_out)


# ─────────────────────────────────────── 臂 D 的三件工具：摆放、按只问、判读到哪一只
ROI_HALF = 120        # ROI 半幅：要容得下 +48 探针与摆放误差，又要**装不下**另一只复制品
MIN_SEP = 220.0       # 两只的中心距下限（> ROI_HALF + MOVE_PX + 余量 ⇒ 两 ROI 互不越界）
# 臂 D 的"在屏"闸：不用 `H23.PRESENT_PEAK`(0.90)。五场实测：`iter_candidates` 选出的 96² 模板
# 盒内 alpha<250 占 3.3%，同一张抓屏上全不透明盒 1.0000 而这只盒只有 **0.8157~0.8209**
# （当场数过：屏上矩形内环色像素 0 只 ⇒ 与焦点环无关，是那 3.3% 掺了窗口身后的东西）。
# 那道 0.90 闸量的因此是"这只盒掺了多少"，不是"这只在不在屏上"——拿它当在屏判据会把臂 D 永远关掉。
# 在屏这件事由**位置**承担：ROI 量到的中心必须落在预测位 ±TOL_PX 内（相关噪声不会恰好落在
# 那 2 px 里）。0.75 只是把"根本没信号"与"信号被背景掺了"分开的地板，不是精度判据。
D_PEAK_FLOOR = 0.75


def patch_frame_topleft(cand, frame) -> tuple[float, float]:
    """候选贴片在**帧**里的左上角 —— `anchor` 那条定义反解出来的那一步。"""
    fh, fw = frame.shape[0], frame.shape[1]
    return (cand.anchor[0] + fw / 2.0 - cand.edge / 2.0,
            cand.anchor[1] + fh / 2.0 - cand.edge / 2.0)


def peak_in_roi(box, off, center, tag, samples: int = 2, half: int = ROI_HALF):
    """只问 `center` 附近那一只：整幅 argmax 在孪生峰在场时由"哪只峰高"决定报谁。

    诱饵是注册模板的**逐字节副本**，峰不会比真那一只低 ⇒ 让尺子自己挑就是让判据对着
    一个薛定谔的数对账。这里把搜索限制在 ROI 里，等于把"问哪一只"写进量具。
    `off` = 锚点（盒中心 − surface 中心），回值是**帧心**，与 `H24.truth_of` 同口径。

    回 `(帧心 | None, 最优峰, 各峰值, 那张抓屏, 贴片中心 | None)`。
    """
    x0, y0 = int(round(center[0] - half)), int(round(center[1] - half))
    x1, y1 = int(round(center[0] + half)), int(round(center[1] + half))
    best, peaks = None, []
    for i in range(samples):
        shot = A10.capture(f"{tag}_{i}")
        if shot is None:
            continue
        sh, sw = shot.shape[:2]
        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(sw, x1), min(sh, y1)
        sub = np.ascontiguousarray(shot[cy0:cy1, cx0:cx1])
        if sub.shape[0] <= box.shape[0] or sub.shape[1] <= box.shape[1]:
            fact("ROI", f"{tag}｜裁剪后 {sub.shape[1]}×{sub.shape[0]} 装不下 {box.shape[0]}² 盒 ⇒ 不可判")
            continue
        peak, px, py = A10.Correlator(H8.luma_of(sub)).peak(H8.luma_of(box))
        peaks.append(float(peak))
        cen = (cx0 + px + box.shape[1] / 2.0, cy0 + py + box.shape[0] / 2.0)
        frame_center = (cen[0] - off[0], cen[1] - off[1])
        if best is None or peak > best[1]:
            best = (frame_center, float(peak), peaks, shot, cen)
        H4.pump(QApplication.instance(), 120)
    if best is None:
        return None, 0.0, peaks, None, None
    return best


def truth_at_real(box, off, center):
    """给 `H24.run_locate(truth_fn=…)` 用的那一层壳：签名对齐 `H24.truth_of`。"""
    def measure(_box, _off, tag, samples: int = 2):
        r = peak_in_roi(box, off, center, tag, samples=samples)
        return r[0], r[1], r[2], r[3]
    return measure


def show_decoy(pet, widget, frame, cand, delta):
    """把**注册模板本身**贴成窗口里另一只**静止**的复制品 ⇒ 屏上出现真孪生峰。

    为什么这一发不是"自己给自己出题"：产品会喂的那条信念与屏上内容的唯一区别，就是
    屏幕上多了一只与我方无关的复制品 —— 而闭环要的恰恰是"读数跟着**已知自移**走吗"。
    诱饵不动、真身动，读数若钉在诱饵上，b−a 必为 0，那一发判据该响。
    """
    from PyQt5.QtGui import QImage, QPixmap
    from PyQt5.QtWidgets import QLabel

    fx, fy = patch_frame_topleft(cand, frame)
    lx = int(round(widget.x() + fx + delta[0]))
    ly = int(round(widget.y() + fy + delta[1]))
    arr = np.ascontiguousarray(cand.box)
    img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 4,
                 QImage.Format_RGBA8888).copy()
    lab = QLabel(pet)
    lab.setFrameStyle(0)
    lab.setGeometry(lx, ly, cand.edge, cand.edge)
    lab.setPixmap(QPixmap.fromImage(img))
    lab.raise_()
    lab.show()
    H4.pump(QApplication.instance(), 600)
    return lab, (lx, ly)


def decoy_delta(widget, frame, cand, pet_wh: tuple[int, int],
                real_center, screen_wh: tuple[int, int]) -> tuple[float, float] | None:
    """在窗口里找一块**不与子控件相交**、离真身尽量远、整盒既不出窗也不出屏的位置。

    由外向内试：先试大间距。理由与 `MIN_SEP` 同源——间距越大，两只越不会被同一只 ROI
    圈到，"读到的是哪一只"这件事就没有第二种解释。四轴 + 四对角一起排，取可行的最大间距。

    都不合就明写"这一场摆不下诱饵"，而不是硬塞一只压住真身或裁掉一半 ——
    那两种摆法造出的"锁错"与摆放失败分不开。
    """
    fx, fy = patch_frame_topleft(cand, frame)
    edge = cand.edge
    w0 = (int(widget.x()), int(widget.y()),
          int(widget.x()) + int(widget.width()), int(widget.y()) + int(widget.height()))
    dirs = ((1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
    seps = (520.0, 460.0, 400.0, 340.0, 300.0, MIN_SEP)
    best = None
    for sep in seps:
        for dx, dy in dirs:
            norm = (dx * dx + dy * dy) ** 0.5
            ux, uy = dx * sep / norm, dy * sep / norm
            lx = int(round(widget.x() + fx + ux))
            ly = int(round(widget.y() + fy + uy))
            if lx < 0 or ly < 0 or lx + edge > pet_wh[0] or ly + edge > pet_wh[1]:
                continue                                   # 出窗 ⇒ 被窗口边裁掉
            if not (lx >= w0[2] or lx + edge <= w0[0] or ly >= w0[3] or ly + edge <= w0[1]):
                continue                                   # 与子控件矩形相交 ⇒ 诱饵盖住真身
            sx, sy = real_center[0] + ux, real_center[1] + uy
            if (sx - edge / 2 < 0 or sy - edge / 2 < 0
                    or sx + edge / 2 > screen_wh[0] or sy + edge / 2 > screen_wh[1]):
                continue                                   # 出屏 ⇒ grim 里根本没有第二只
            if best is None or sep > best[2]:
                best = (ux, uy, sep, (lx, ly))
        if best is not None:
            break                       # 大间距试到了就不再降
    if best is None:
        return None
    fact("诱饵选址", f"取中心距 {best[2]:.0f} px ⇒ 偏移 ({best[0]:+.1f},{best[1]:+.1f})｜"
                     f"子控件本地左上={best[3]} {edge}²｜子控件矩形={w0}｜顶层="
                     f"{pet_wh[0]}×{pet_wh[1]}｜屏幅={screen_wh[0]}×{screen_wh[1]}")
    return (best[0], best[1])


def opaque_box(frame):
    """独立尺子的选盒：与 `H12.probe_box` **同一条路**（质心处往外收、取最大整盒方片），
    只把不透明那道闸从 `alpha > FP.ALPHA_MIN`(=8) 收严到 `alpha == 255`。

    为什么要收这一刀：屏上那一片是**合成之后**的像素，窗口身后是谁就掺谁。实测（10:10 那场，
    同一张抓屏、桌宠聚焦）：全不透明 72² 盒峰 **1.0000**、`alpha>8` 类 78² 盒 0.9970、
    引擎自己选的 96² 候选盒只有 **0.8209**——后两只盒内 alpha<250 分别占 0.001 / 0.033。
    差值跟着**半透明占比**走，不跟着焦点环走（当场数过：那只 96² 盒屏上矩形内环色像素 **0** 只）。
    全不透明的一盒把身后彻底挡住 ⇒ 尺子对"背景是谁"免疫，量的仍是同一件事：帧心在屏上哪里。

    刻意仍不复用被测模块的 `iter_candidates`（独立性那条不变），也刻意**不**替 fidus 修这件事：
    产品喂给它的那只盒由 `iter_candidates` 选，容的是 `alpha>8`——那一只在屏上确实比帧里那份脏。
    这一件要量的是"脏到什么程度、还锁不锁得对"，不是把它治好。
    """
    m = frame[..., 3] == 255
    ys, xs = np.nonzero(m)
    if len(xs) < 16:
        return None, None
    cx, cy = int(xs.mean()), int(ys.mean())
    h, w = frame.shape[:2]
    for edge in range(min(h, w), 15, -2):
        x0, y0 = cx - edge // 2, cy - edge // 2
        if x0 < 0 or y0 < 0 or x0 + edge > w or y0 + edge > h:
            continue
        if m[y0:y0 + edge, x0:x0 + edge].all():
            return np.ascontiguousarray(frame[y0:y0 + edge, x0:x0 + edge]), \
                (x0 + edge / 2.0 - w / 2.0, y0 + edge / 2.0 - h / 2.0)
    return None, None


def focus_report(app, pet):
    """只**报**焦点与环，不动焦点。

    两条各自独立：
      · **不动焦点**是被实测逼出来的：09:47 / 09:58 两次"把焦点交出去"都比留着环更糟——
        `focus-window` 会**滚动平铺视野**到那只窗口，桌宠直接被推出屏（09:58 那张抓屏里整屏
        只剩终端，在位闸 0.4529；09:47 那次挑中的还在别的工作区，连桌面都换了，0.2727）。
        合成器上没有"只改焦点不改视野"这只手 ⇒ 交焦点不是清除污染，是换一个更大的污染。
      · **环到底伤不伤**这一条我方先前判错了：09:41 那场环在场时尺子盒峰 0.6600，当时记成
        "环 ⊕ 模型"。10:10 这场同样聚焦、当场数环色像素 = **0** 只，而峰值族（1.0000 / 0.9970 /
        0.8209）跟着盒内半透明占比走。⇒ 环**不是**那一族峰值的成因，归因已改（见 `opaque_box`）；
        这条留在这里是因为它是"别把相关度当健康判据"那条结论的来源之一。
    """
    ring_rgb, ring_w, ring_src = H24.H3._ring_style()
    pid = H24.pet_win_id()
    me = next((w for w in H24.win_row() if w.get("id") == pid), {})
    return (f"桌宠 id={pid} 聚焦={me.get('is_focused')}｜工作区={me.get('workspace_id')}｜"
            f"{ring_src}｜active-color={ring_rgb} width={ring_w}｜"
            f"**不动焦点**：`focus-window` 会滚动平铺视野把桌宠推出屏（09:47/09:58 实测），"
            "环留在场按产品真实条件跑")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="HGBTD",
                    help="H=手有效性 G=闭环·好先验 B=闭环·信念先验 T=只允许64² D=屏上真孪生峰")
    args = ap.parse_args()
    arms = {a.upper() for a in args.arms if a.upper() in "HGBTD"}

    app = QApplication.instance() or QApplication(sys.argv)
    fact("锚点", __import__("fidus").__git_commit__)
    fact("被测代码", f"{FP.__file__}（生产选尺 / 冷获取 / locate 闭环，未复刻）")
    fact("臂", " ".join(sorted(arms)))

    sc, unit_line = H15.read_units(app)
    fact("合成器单位", unit_line)
    if sc != sc or abs(sc - 1.0) > 1e-9:
        print(f"VERDICT-H25 : ✗ 尺子按物理 px、引擎按逻辑 px，只在 scale=1.0 对表；当前 {sc} ⇒ 不跑，不猜")
        return 1

    pet = H23.boot_pet(app)
    if pet is None:
        print(f"VERDICT-H25 : ✗ {H23.BOOT_TIMEOUT} s 内拿不到离屏帧 ⇒ 量具自身失效")
        return 1
    widget = pet.sprite_label
    widget._draw_model = widget.l2d.model.Draw      # type: ignore[method-assign]
    H4.pump(app, 400)
    fact("相位", "`_draw_model` 只留 `model.Draw()` ⇒ 显示与离屏同源同相（与 H23/H24 同一冻法）")

    pet._set_layer_mode(False)
    H4.pump(app, 1500)

    # ── 焦点一律不动，这条是被实测逼出来的：09:47 / 09:58 两次"把焦点交出去"都比留着焦点环
    #    更糟——`focus-window` 会**滚动平铺视野**到那只窗口，09:58 那一场抓屏里整屏只剩终端、
    #    桌宠被推出视野（在位闸 0.4529），09:47 那次挑中的还在别的工作区，连桌面都换了（0.2727）。
    #    合成器上没有"只改焦点不改视野"这只手。尺子侧对背景的免疫交给 `opaque_box`。
    fact("焦点环", focus_report(app, pet))

    frame = pet._fidus_current_frame()
    if frame is None:
        print("VERDICT-H25 : ✗ 交互态取不到帧 ⇒ 量具断")
        return 1
    belief = H24.belief_center_of(pet)
    wx, wy = int(pet.width()), int(pet.height())
    cw, ch = int(widget.width()), int(widget.height())
    slack = (wx - (int(widget.x()) + cw), wy - (int(widget.y()) + ch))
    OUT["clip_slack"] = slack
    fact("态", f"交互态｜Qt 顶层=({pet.x()},{pet.y()}) {wx}×{wy}｜子控件=({widget.x()},{widget.y()}) "
               f"{cw}×{ch}｜帧 {frame.shape[1]}×{frame.shape[0]}｜右/下余量 {slack[0]}/{slack[1]} px"
               f"｜信念中心=({belief[0]:.2f},{belief[1]:.2f})")
    if slack[0] < MOVE_PX:
        fact("余量告警", f"窗口内横向只剩 {slack[0]} px < {MOVE_PX:.0f} ⇒ 挪过去会被窗口边裁掉贴片，"
                        "屏上内容与注册贴片不再同源，闭环的失败就分不清是引擎还是裁剪。"
                        "仍照跑，但那一发的峰与在位闸一起看")

    ruler_box, ruler_off = opaque_box(frame)
    if ruler_box is None:
        print("VERDICT-H25 : ✗ 独立尺子选不出全不透明盒 ⇒ 量具自身失效")
        return 1
    h12_box, h12_off = H12.probe_box(frame)
    fact("尺子选盒", f"`opaque_box`(alpha==255)={ruler_box.shape[0]}²｜"
                     f"`H12.probe_box`(alpha>{FP.ALPHA_MIN})="
                     f"{'无' if h12_box is None else f'{h12_box.shape[0]}²'}｜盒内 alpha 最小值="
                     f"{int(ruler_box[..., 3].min())}——收严这一刀是为了让尺子对环在场免疫，"
                     "引擎那一侧仍按它自己的 `iter_candidates` 选盒，不受影响")
    base, peak, peaks, shot = H24.truth_of(ruler_box, ruler_off, "h25_tiled")
    fact("在位闸", f"盒 {ruler_box.shape[0]}² 峰值 {[f'{p:.4f}' for p in peaks]} ⇒ {peak:.4f} "
                   f"{'✓' if base is not None else '✗'}")
    if base is None:
        H23.dump_pair("h25_visible_fail_tiled", frame, shot, ruler_box)
        print("VERDICT-H25 : ✗ 在位闸不过 ⇒ 全案不产出读数（量具断，不是被测物错）")
        return 1
    H24.ring_at(shot, base, ruler_box.shape[0], "平铺")
    fact("真值", f"帧心屏上=({base[0]:.2f},{base[1]:.2f})｜峰={peak:.4f}｜信念误差="
                 f"({belief[0] - base[0]:+.2f},{belief[1] - base[1]:+.2f}) px")

    # 峰值的账落到**引擎那只盒**上：尺子换了全不透明的一盒就稳在 1.0，但产品喂给 fidus 的那只
    # 盒由 `iter_candidates` 选（闸是 `alpha > FP.ALPHA_MIN`=8），盒内那几 % 半透明像素在屏上
    # 掺的是**窗口身后**的东西 ⇒ 同一个模板在不同桌面背景下峰值会飘。这一发量的是飘到哪。
    if h12_box is not None:
        _hb, p_h12, pk_h12, _sb = H24.truth_of(h12_box, h12_off, "h25_peak_account")
        fact("峰值的账", f"同一张抓屏三档盒：全不透明 {ruler_box.shape[0]}²={peak:.4f}（尺子）｜"
                         f"alpha>{FP.ALPHA_MIN} 类 {h12_box.shape[0]}²={p_h12:.4f} "
                         f"{[f'{x:.4f}' for x in pk_h12]}（盒内 alpha<250 占比 "
                         f"{H23.alpha_frac(h12_box):.3f}）｜桌宠聚焦="
                         f"{H24.H3._is_focused(H24.pet_win_id())} ⇒ 峰值随半透明占比与身后背景走，"
                         "**不能当健康判据**（引擎那一侧的候选盒见臂 D 的「引擎盒的账」）")

    eng = FP.FidusEngine()
    t_cal = time.perf_counter()
    eng.calibrate()
    fact("calibrate", f"{(time.perf_counter() - t_cal) * 1e3:.0f} ms（一次会话一发）")

    # ───────────────────────────────────── 臂 N（前提，不是可选臂）：无信念全链拿好先验
    print("\n=== 臂 N · 无信念全链（平铺态；G 臂的先验从这里来） ===")
    cand_n, ceiling_n, reading_n = H24.run_cold(eng, frame, belief, FP.SIZES, "N·平铺")
    if cand_n is None:
        fact("N·平铺", "生产选尺全被拒 ⇒ 好先验拿不到，G 臂跳过")
    else:
        OUT["edge_n"] = cand_n.edge
        OUT["lock_n"] = H24.lock_line("N·平铺", reading_n, base, ceiling_n, cand_n.edge)
    good = reading_n[:2] if reading_n is not None else None

    # ───────────────────────────────────── 臂 H · 这只手到底挪不挪得动屏上内容（单独量）
    if "H" in arms:
        print("\n=== 臂 H · 手有效性：子控件自移 → 屏上真实位移 + 帧恒等 ===")
        f_before = pet._fidus_current_frame()
        mover = ChildMover(app, widget)
        mover(MOVE_PX, 0.0)
        for note in mover.notes:
            fact("位移", f"H｜{note}")
        f_after = pet._fidus_current_frame()
        if f_before is not None and f_after is not None:
            same = sha_of(f_before) == sha_of(f_after)
            OUT["frame_identical"] = same
            fact("帧恒等", f"挪前 sha={sha_of(f_before)} 挪后 sha={sha_of(f_after)} ⇒ "
                          f"{'**同一份** ⇒ 喂给引擎的输入没变，变的只有屏上 ⇒ 这一发是外接见证' if same else '⚠ 帧也变了 ⇒ 闭环里掺了我方自己改的输入，绿不算'}")
        moved, mpeak, m_peaks, mshot = H24.truth_of(ruler_box, ruler_off, "h25_h", samples=2)
        if moved is None:
            fact("H", f"✗ 挪完在位闸不过（峰 {mpeak:.4f},峰值 {[f'{p:.4f}' for p in m_peaks]}）"
                      "⇒ 屏上内容与帧不再同源，这一发不可判（先看上面那个余量告警）")
            H23.dump_pair("h25_visible_fail_move", f_after, mshot, ruler_box)
        else:
            disp = (moved[0] - base[0], moved[1] - base[1])
            OUT["hand_px"] = disp
            H24.ring_at(mshot, moved, ruler_box.shape[0], "H·挪后")
            fact("H·屏上真实位移", f"({disp[0]:+.2f},{disp[1]:+.2f}) px vs 请求 (+{MOVE_PX:.0f},0)")
        mover.restore()
        back, bpeak, _ = H24.truth_of(ruler_box, ruler_off, "h25_h_back", samples=2)[:3]
        if back is not None:
            base = back
            fact("H·复原", f"基准更新为 ({back[0]:.2f},{back[1]:.2f})")
        else:
            fact("H·复原", f"✗ 挪回后在位闸不过（峰 {bpeak:.4f}）⇒ 基准不更新，后面各发按旧基准")

    # ───────────────────────────────────── 臂 G / B · 完整 locate 走这只手
    for key, tag, prior in (("G", "好先验", good), ("B", "信念先验", belief)):
        if key not in arms:
            continue
        print(f"\n=== 臂 {key} · 内容自移闭环（{tag}） ===")
        if prior is None:
            fact(key, "先验拿不到 ⇒ 这一臂跳过")
            continue
        fact("喂的先验", f"{tag}｜对独立真值误差=({prior[0] - base[0]:+.2f},{prior[1] - base[1]:+.2f}) px")
        fix, loop, real_disp, back = run_locate25(eng, frame, prior, base, ruler_box,
                                                  ruler_off, widget, app, f"{key}·{tag}")
        k_loop, k_real, k_pass, k_lock = {
            "G": ("loop_good", "real_good", "pass_good", "lock_good"),
            "B": ("loop_belief", "real_belief", "pass_belief", "lock_belief"),
        }[key]
        OUT[k_loop], OUT[k_real] = loop, real_disp
        OUT[k_pass] = H24.loop_verdict(loop, real_disp)
        if back is not None:
            base = back
        if fix is None:
            fact(key, "locate 回 None ⇒ 该臂无锁读数（闭环没过或被拒，见上面每条 log）")
            continue
        OUT[k_lock] = H24.lock_line(f"{key}·{tag}", (fix.center[0], fix.center[1], fix.conf),
                                    base, fix.ceiling, fix.edge)

    # ───────────────────────────────────── 臂 T · 只允许 64²（撒谎档）
    if "T" in arms:
        print("\n=== 臂 T · 只允许 64²（冷一遍 + 这只手的闭环一遍） ===")
        cand_t, ceiling_t, reading_t = H24.run_cold(eng, frame, belief, (64,), "T·冷")
        if cand_t is None:
            fact("T·冷", "64² 档全被拒 ⇒ 本档不可用（响亮退回，属安全一侧）")
        else:
            OUT["edge_t"] = cand_t.edge
            OUT["lock_t_cold"] = H24.lock_line("T·冷", reading_t, base, ceiling_t, cand_t.edge)
            if reading_t is not None:
                fact("T·闭环", "带闭环再跑一遍——H14 的 48²/32² 正是**过了闭环却锁错**，本臂要的就是这一格")
                fix_t, loop_t, real_t, back_t = run_locate25(
                    eng, frame, reading_t[:2], base, ruler_box, ruler_off, widget, app,
                    "T·闭环", sizes=(64,))
                OUT["loop_t"], OUT["real_t"] = loop_t, real_t
                OUT["pass_t_loop"] = H24.loop_verdict(loop_t, real_t)
                if back_t is not None:
                    base = back_t
                if fix_t is not None:
                    OUT["lock_t_loop"] = H24.lock_line("T·闭环",
                                                      (fix_t.center[0], fix_t.center[1], fix_t.conf),
                                                      base, fix_t.ceiling, fix_t.edge)

    # ───────────────────────────────────── 臂 D · 屏上真孪生峰（判别力的那一发样本）
    if "D" in arms:
        print("\n=== 臂 D · 诱饵：把注册模板逐字节复制成窗口里另一只**静止**的贴片 ===")
        fact("D·为什么", "上面各臂只证明了「锁对时闭环过」。「没出错」不等于「能抓错」——"
                         "要测判别力就得当场造一个**真会锁错**的局面。H14 的 48²/32² 是天然孪生峰，"
                         "本臂把它做成可控的：复制的是 `cand.box` 本身，所以诱饵与模板逐字节相同。")
        pred = (base[0] + cand_n.anchor[0], base[1] + cand_n.anchor[1]) if cand_n else None
        rc, rpeak, rpeaks, rshot, real_center = (peak_in_roi(cand_n.box, cand_n.anchor, pred,
                                                            "h25_d_real0", samples=2)
                                                if cand_n else (None, 0.0, [], None, None))
        if cand_n is None:
            fact("D", "✗ 臂 N 没选出候选 ⇒ 没有可复制的模板。这是**判别力仍不可测**，不是「没抓到」")
        elif not (rc is not None and rpeak >= D_PEAK_FLOOR
                  and max(abs(rc[0] - base[0]), abs(rc[1] - base[1])) <= TOL_PX):
            fact("D", f"✗ 真身那一只问不到：ROI 峰 {[f'{p:.4f}' for p in rpeaks]}（地板 {D_PEAK_FLOOR}）"
                      f"｜量到帧心={('--' if rc is None else f'({rc[0]:.2f},{rc[1]:.2f})')} vs 整幅基准"
                      f"({base[0]:.2f},{base[1]:.2f}) 差="
                      f"{('--' if rc is None else f'({rc[0]-base[0]:+.2f},{rc[1]-base[1]:+.2f})')} px"
                      f"（须 ≤{TOL_PX:.0f}）⇒ 本臂不判（诱饵与真身分不出谁没上屏）")
        else:
            fact("ROI↔整幅对表", f"候选 {cand_n.edge}² 帧心 ROI 量到=({rc[0]:.2f},{rc[1]:.2f})｜"
                                  f"整幅尺子基准=({base[0]:.2f},{base[1]:.2f})｜差="
                                  f"({rc[0] - base[0]:+.2f},{rc[1] - base[1]:+.2f}) px ⇒ "
                                  f"{'`anchor` 那条算术与两把尺子自洽' if max(abs(rc[0]-base[0]), abs(rc[1]-base[1])) <= TOL_PX else '⚠ 两把尺子对不上，本臂一律以 ROI 那把为准'}")
            # 本臂的基准一律取 ROI 那把：读数与真值同一口径，差值才只反映"读到哪一只"，
            # 不掺进两把尺子之间的系统性错位（上面那行对表就是来看它俩有没有错位的）。
            base_d = rc
            # 把账落到**引擎那只盒**上：H23 记过一列"在位闸过、贴片真值只有 0.81x，而 alpha<250
            # 只占 3% ⇒ 不足以解释，成因未定"。这里当场数环色像素、并报该盒 ROI 峰与半透明占比：
            # 环色为 0 而峰仍只有 0.82 ⇒ 那一族不是环，是半透明像素掺了窗口身后的东西。
            _rgb, _rw, _src = H24.H3._ring_style()
            n_ring = (H23.decor_inside(rshot, real_center[0], real_center[1], cand_n.edge)
                      if rshot is not None else -1)
            fact("引擎盒的账", f"候选 {cand_n.edge}² 屏上矩形内 {_rgb} 色像素 {n_ring} 只"
                               f"（环 width={_rw}，桌宠聚焦={H24.H3._is_focused(H24.pet_win_id())}）｜"
                               f"该盒 ROI 峰={rpeak:.4f}｜盒内 alpha<250 占比="
                               f"{H23.alpha_frac(cand_n.box):.3f} ⇒ "
                               f"{'环在场，峰里掺了环' if n_ring > 0 else '环没进这只盒 ⇒ 峰低另有其因（半透明像素身后的东西）'}")
            delta = decoy_delta(widget, frame, cand_n,
                                (int(pet.width()), int(pet.height())), real_center,
                                (int(shot.shape[1]), int(shot.shape[0])))
            if delta is None:
                fact("D", f"✗ 窗口里放不下「不与子控件相交、中心距 ≥{MIN_SEP:.0f} px、整盒不出窗」的"
                          f"第二只 ⇒ 这一场造不出孪生峰，判别力**仍不可测**（子控件="
                          f"({widget.x()},{widget.y()}) {int(widget.width())}×{int(widget.height())}｜"
                          f"顶层={pet.width()}×{pet.height()}）")
            else:
                lab, (lx, ly) = show_decoy(pet, widget, frame, cand_n, delta)
                dpred = (real_center[0] + delta[0], real_center[1] + delta[1])
                _dc, dpeak, dpeaks, _dshot, dcenter = peak_in_roi(
                    cand_n.box, cand_n.anchor, dpred, "h25_d_decoy", samples=2)
                fact("诱饵", f"子控件本地 ({lx},{ly}) {cand_n.edge}²｜请求偏移 ({delta[0]:+.0f},"
                              f"{delta[1]:+.0f})｜ROI 预测中心=({dpred[0]:.2f},{dpred[1]:.2f}) 量到="
                              f"{('--' if dcenter is None else f'({dcenter[0]:.2f},{dcenter[1]:.2f}) 差 ({dcenter[0]-dpred[0]:+.2f},{dcenter[1]-dpred[1]:+.2f})')}｜"
                              f"峰 {[f'{p:.4f}' for p in dpeaks]} ⇒ {dpeak:.4f}｜真身峰={rpeak:.4f}｜"
                              f"盒内 alpha<250 占比={H23.alpha_frac(cand_n.box):.3f}")
                dpos = None if dcenter is None else max(abs(dcenter[0] - dpred[0]),
                                                         abs(dcenter[1] - dpred[1]))
                if not (dcenter is not None and dpeak >= D_PEAK_FLOOR and dpos <= TOL_PX):
                    fact("D", f"✗ 诱饵没上屏或没到位（峰 {dpeak:.4f} 地板 {D_PEAK_FLOOR}｜"
                              f"位差 {('--' if dpos is None else f'{dpos:.2f}')} px 须 ≤{TOL_PX:.0f}）"
                              "⇒ 屏上仍只有一只，判别力**仍不可测**")
                else:
                    fact("D·先验", "整臂**不喂先验**（`register(box, None)`）——产品「量完再挂」走的就是"
                                   "这条路；判别力要成立就得测在这条路上，而不是测在一条产品拿不到的路上")
                    deltas: list[tuple[float, float]] = []
                    with H24.beliefless(eng):
                        fix_d, loop_d, real_d, back_d = run_locate25(
                            eng, frame, base_d, base_d, ruler_box, ruler_off, widget, app,
                            "D·诱饵", truth_fn=truth_at_real(cand_n.box, cand_n.anchor, real_center),
                            deltas_out=deltas)
                    a = deltas[0] if deltas else None
                    OUT["loop_d"], OUT["real_d"] = loop_d, real_d
                    OUT["pass_d"] = H24.loop_verdict(loop_d, real_d)
                    if a is None:
                        fact("D", f"日志里凑不出 a（拿到 {len(deltas)} 发）⇒ 读数无从归位")
                    else:
                        d_real = max(abs(a[0] - real_center[0]), abs(a[1] - real_center[1]))
                        d_dec = max(abs(a[0] - dcenter[0]), abs(a[1] - dcenter[1]))
                        took = d_dec < d_real
                        OUT["took_decoy"] = took
                        OUT["lock_d"] = (a[0] - cand_n.anchor[0] - base_d[0],
                                         a[1] - cand_n.anchor[1] - base_d[1])
                        fact("D·读到哪一只", f"a=({a[0]:.2f},{a[1]:.2f})｜离真身 {d_real:.2f} px "
                                             f"离诱饵 {d_dec:.2f} px ⇒ "
                                             f"{'**读到诱饵上了**（锁错样本到手）' if took else '读到真身（诱饵未生效）'}")
                        if took and fix_d is not None:
                            fact("D·conf", f"锁错这一发的 conf={fix_d.conf:.6f} vs ceiling="
                                            f"{fix_d.ceiling:.6f} ⇒ 满值置信照样为错锁背书"
                                            "（与 `fidus_position.py:11-13` 同一条，本臂复证）")
                    lab.hide()
                    lab.deleteLater()
                    H4.pump(app, 600)
                    after, apeak, apeaks, _ = H24.truth_of(ruler_box, ruler_off, "h25_d_after",
                                                           samples=2)
                    if after is not None:
                        base = after
                        fact("D·撤诱饵", f"移除后整幅尺子基准更新为 ({after[0]:.2f},{after[1]:.2f})｜"
                                         f"峰 {apeak:.4f}")
                    else:
                        fact("D·撤诱饵", f"✗ 移除后在位闸不过（峰 {[f'{p:.4f}' for p in apeaks]}）"
                                         "⇒ 基准不更新；撤带是 Qt 子控件销毁，与合成器无关")

    # ───────────────────────────────────── 出口
    print("\n=== 出口 ===")
    if OUT["hand_px"] is not None:
        hx, hy = OUT["hand_px"]
        ok_hand = abs(hx - MOVE_PX) <= LOOP_TOL_PX and abs(hy) <= LOOP_TOL_PX
        print(f"VERDICT-HAND : 子控件自移 → 屏上真实位移 ({hx:+.2f},{hy:+.2f}) px vs 请求 "
              f"(+{MOVE_PX:.0f},0) ⇒ 这一只手{'**有效**' if ok_hand else '**无效**'}"
              f"｜帧恒等={OUT['frame_identical']}｜窗口余量={OUT['clip_slack']}")
    else:
        print("VERDICT-HAND : 未跑/不可判")

    for k_loop, k_real, k_pass, k_lock, label in (
            ("loop_good", "real_good", "pass_good", "lock_good", "G-好先验"),
            ("loop_belief", "real_belief", "pass_belief", "lock_belief", "B-信念先验"),
            ("loop_t", "real_t", "pass_t_loop", "lock_t_loop", "T-64²"),
            ("loop_d", "real_d", "pass_d", "lock_d", "D-孪生峰")):
        if OUT[k_loop] is not None:
            lx, ly = OUT[k_loop]
            print(f"VERDICT-{label}-闭环 : b−a=({lx:+.2f},{ly:+.2f}) px ⇒ "
                  f"{'过' if OUT[k_pass] else '不过'}（对上屏上真实位移 {OUT[k_real]}）")
        if OUT[k_lock] is not None:
            dx, dy = OUT[k_lock]
            print(f"VERDICT-{label}-锁 : 差 ({dx:+.2f},{dy:+.2f}) px ⇒ "
                  f"{'锁对' if max(abs(dx), abs(dy)) <= TOL_PX else '锁错'}")
        if k_lock == "lock_d" and OUT["took_decoy"] is not None:
            print(f"VERDICT-D-归位 : 读数{'落在**诱饵**那一只 ⇒ 锁错是真的，不是尺子噪声' if OUT['took_decoy'] else '落在**真身** ⇒ 诱饵未生效'}")

    # 判别力四格：只按**实测到**的样本说话。
    cells: list[tuple[str, str]] = []
    for label, k_lock, k_pass in (("G-好先验", "lock_good", "pass_good"),
                                  ("B-信念先验", "lock_belief", "pass_belief"),
                                  ("T-64²", "lock_t_loop", "pass_t_loop"),
                                  ("D-孪生峰", "lock_d", "pass_d")):
        lk, ps = OUT[k_lock], OUT[k_pass]
        if lk is None or ps is None:
            continue
        wrong = max(abs(lk[0]), abs(lk[1])) > TOL_PX
        cells.append((label, f"{'锁错' if wrong else '锁对'} & {'闭环过' if ps else '闭环不过'}"))
    caught = [c for c, v in cells if v == "锁错 & 闭环不过"]
    missed = [c for c, v in cells if v == "锁错 & 闭环过"]
    hurt = [c for c, v in cells if v == "锁对 & 闭环不过"]
    fact("判别力样本", "｜".join(f"{c}:{v}" for c, v in cells) if cells else "无可判样本")
    if missed:
        print(f"VERDICT-DISCRIMINATES : ✗ {missed} **锁错却过了闭环** ⇒ 这只手不是判据"
              "（H14 那个结构失明的形状在本手复现）")
    elif caught and not hurt:
        print(f"VERDICT-DISCRIMINATES : ✓ {caught} 被抓到且不误伤 ⇒ 预挂载判据 = "
              f"内容自移闭环 @ {LOOP_TOL_PX} px（产品自己的手）")
    elif caught and hurt:
        print(f"VERDICT-DISCRIMINATES : △ {caught} 抓到但 {hurt} 被误伤 ⇒ 判据可用有代价，"
              f"阈值那一格得单独对账（`locate(tol_px=…)` 是入参，不是常量）")
    else:
        if "D" in arms and OUT["took_decoy"] is False:
            print("VERDICT-DISCRIMINATES : 臂 D 跑了、诱饵也在屏上，但读数仍跟着真身 ⇒ **本轮没造出错锁样本**。"
                  "诱饵是模板的逐字节副本却没被选中，这条本身是信息（先验/搜索窗把两只分开了），"
                  "但它不等于「能抓错」——判别力仍未测")
        elif "D" in arms and OUT["took_decoy"] is None:
            print("VERDICT-DISCRIMINATES : 臂 D 未产出可判样本（摆放失败 / 诱饵没上屏 / 无读数，见上面 D 各行）"
                  " ⇒ 判别力**仍未测**")
        else:
            print("VERDICT-DISCRIMINATES : 本轮**没有错锁样本** ⇒ 判别力**未测**（臂 D 没跑）。"
                  "不许把'没出错'读成'能抓错'；要测就带 --arms …D 造屏上真孪生峰")

    print("VERDICT-CRITERION : 判据不在脚本里自动定——HAND 与 DISCRIMINATES 两行合起来才说话，"
          "而「产品要不要用这只手」里还有可见代价（桌宠在窗口里横向滑一下），由人工点选")
    print("VERDICT-RUN : 跑到出口")
    return 0


if __name__ == "__main__":
    sys.exit(main())
