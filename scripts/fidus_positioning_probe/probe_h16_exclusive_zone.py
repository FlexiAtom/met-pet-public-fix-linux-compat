#!/usr/bin/env python3
"""H16 · 可用区与遮挡：真的挂一枚带 exclusive-zone 的面板，量三件产品吃得着的事。

#74 那一格。H15 量到"信念误差 0.00 px"，当时记为**假绿**（本机没有面板 ⇒ `geometry()` 与
`availableGeometry()` 逐位相等，偏移这一格结构上量不出来）。本件先推翻这个 framing：
面板挂起来之后 Qt 的两个矩形**仍然逐位相同**——Wayland 根本没有"可用区"这个客户端可见概念
（X11 才有 `_NET_WORKAREA`）。所以这一格不是"量不到"，而是"产品吃的那个量在本平台上恒等于整屏"。
剩下能量、且产品真吃得着的两件事是：

* **N0/N1 保留区会不会挪动我们**请求的**落点**：用**产品自己的** `_place_bottom_right()`（它读
  `availableGeometry()`）摆位、挂层，用独立尺子（grim＋宿主 NCC，不经 fidus）读真值。
  无面板 = 对照，有面板 = 本判。N1 若读不到，再分三形：**N1-T** 同位置多等 1.5 s 重量一次
  （区分"抓在淡入动画里"与"常驻读不到"）；**N1d** 把模板按只压垂直的几档比例各做一次全局峰
  （区分"内容被非等比塞进变小的矩形"与"内容没了"）；**N1e** 把桌宠整块挪到保留带**之下**
  重量一次（证"面板在场时我方是否被合成"）；**N1f** 问 `niri msg -j layers` 我方 surface
  在不在册（把"像素不在"与"映射没成立"分开——niri 不吐几何，但"在不在、哪一层"是硬事实）；
  **N1g** 把 surface 整块挪出屏右、两次抓帧做差，**不经模板匹配**直接量出"合成器画在屏上的
  我方轮廓"到底在哪、多大；**N1i** 直接读产品自己的**黏性错误槽**（`layer_last_error`，探针
  CDLL 同一个 `.so`，不经抓帧不经人眼）——帧闸门在"声称尺寸 ≠ configure 给的逻辑尺寸"时每帧
  丢弃，那是"没上屏"的**原因层**读数。三读：面板前／面板后未重挂／重挂后，只认变化。
  **N1b** 再把桌宠顶进保留带，让"我方全不透明像素"真的落在面板行里，直接量 Top 层面板与
  我们（OVERLAY 层）的 z 序——层序不推定，且其解读**以 N1e 为闸**：带外也读不到时，
  带内读到面板色不构成 z 序证据。**N1h** 在同一相把同一个矩形按"请求 y + 保留带厚度"重量
  一次：这一臂测的是**坐标假设**（margin 是在扣掉保留区的可用区里量的、还是在整屏里量的），
  它过了在位闸，N1b 那个占比才有资格被解释成"尺子取样取错了行"而不是"面板压在我们上面"。最后 **N4** 撤掉面板重走一次同样的切换，分"只在面板在场
  期间坏（可逆）"与"我方表面被改坏了（不可逆）"。

> 曾经还有一形"**N1c** 藏／显做差"（把两次抓屏逐像素相减当合成器实际画了什么）。**已删**：
> `_set_layer_mode(False)` 同时会把可交互的 Qt 窗口映射回来，差集里混进了窗口与动画（实测差集
> 面积 14 万 px vs 桌宠不透明 5.6 万 px），这个量具在本产品形态下给不出干净读数。
* **N2/N3 面板在场时 fidus 的校正还对不对**：N2 面板在 Top 层；N3 把面板换成 OVERLAY 层、
  在桌宠挂载**之后**再挂 ⇒ 两种层序下"谁在上"都是待验事实，判据统一用**N1b 那一个量**
  （按最后摆位算，保留带行内我方**全不透明**像素读成面板色的占比；整行数会因轮廓顶端本就
  透明而说谎）。前提不过就报"本机造不出遮挡相"，不硬判。N3 判的是"顶部被压掉一截时，
  校正是**退回**、还是**报一个错数**"——后者才是要防的。

面板 = `scripts/rust_layer_probe/src/bin/exclusive_panel.rs`（本件唯一的合成副作用；
主动 terminate，另有 TTL 兜底，收尾核验合成器侧不留面板）。

**做差臂有一道前置闸 `VERDICT-QUIESCENCE`**：`ambient_diff` 什么都不动连抓两发，差集超
`QUIESCENT_MAX_PX` 就是桌面自己在动（轮播壁纸／播放中的图／滚动的终端），此时**所有做差读数作废**
——只在状态翻转时出声，同一状态连刷是噪声。K 轮与 N 轮各撞过一次（N 轮底噪 12.4 万 px，
那条"面板改了落点或尺寸"是环境给的）。跑之前先把活动工作区里会动的东西清掉。

用法：

    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h16_exclusive_zone.py

    # 带人眼窗（判"没上屏"还是"没进抓帧"、以及 Top 带与我方谁压谁——两件事仪器都分不开）：
    ... probe_h16_exclusive_zone.py --eyeball 90
    #   ⇒ 三扇（顺序即桌面状态变化顺序，探针只出问句、不代答）：
    #     一·N1 挂载位（secs）："红条在不在？桌宠看得见吗（要在动）" → A／B／C／X
    #     二·N1e 带外、**被挪过之后**（max(15, secs/3)）："这一相看得见桌宠吗" → Y／N／P
    #     三·N1b 头钉进保留带（max(15, secs/3)）："桌宠压红条，还是红条压桌宠" → 1／2／3
    #     二、三两扇是 P 轮人的读数（一·B 与 三·1 相互矛盾）逼出来的：只隔几发 set_position，
    #     一相看不见、另一相看得见 ⇒ 要把"挂载态没提交内容"与"面板在场一律不画"分开钉。
    #     这一格**不接受截屏作证据**（grim 与被怀疑的那条路同源），只有人眼或光学路径算。

退出码：0 = 各臂都跑到出口；1 = 量具／面板不可用（不产出任何主张）；2 = 判据落红
（含 N1g 的"面板在场时我方像素一个都没进抓帧"——那条在 L 轮实测成立，是**预期里的红**，
不是崩溃；它要不要转绿属产品侧裁决，见状态件 §7.9）。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import probe_a10_geometry as A10  # noqa: E402
import probe_h12_product_path as H12  # noqa: E402
import probe_h15_product_wiring as H15  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402

fact = H12.fact
TOL_PX = H15.TOL_PX
BAND_PX = 180             # 保留带＝面板自身厚度：盖住桌宠轮廓上三分之一强
PANEL_NS = "h16-exclusive-panel"
MINE_NS = "meapet"            # 产品写死的层表面 namespace（`wayland.rs::create_layer_ctx_objects`）

PANEL_RGB = (0xC0, 0x40, 0x40)   # 面板自涂的纯色（ABGR8888 写 0xFF4040C0 的落屏结果）
PANEL_BIN = REPO_ROOT / "scripts/rust_layer_probe/target/debug/exclusive_panel"
PANEL_TTL = 900
# 桌面静置闸：**什么都不动**时两发抓帧的差集面积上限（px）。桌宠自己一份不透明内容约 5.6 万 px，
# 底噪超过 2000 就意味着桌面在动（轮播壁纸／播放中的图／滚动终端），做差量具随之失效——
# K 轮与 N 轮都撞上过（N 轮底噪 12.4 万 px，那条 N1g"面板改了落点"是环境漂移，不是结论）。
QUIESCENT_MAX_PX = 2000
# 残余闸：把 surface 整块挪出屏，屏上该变的量**只能是我方内容的面积**。若差集面积连无面板时
# 基线轮廓（N0）的这个分数都不到，那不是"轮廓挪了位"，是散点残余（时钟跳一格、指针动一下），
# 读成"落点变了"就是拿噪声编故事。R 轮实测：差集 332 px、底噪 111 px、N0 基线 61177 px——
# 332 只够过 `2×底噪` 那道闸，却是我方面积的 0.5%，被上一版读成了"面板改了落点或尺寸"。
RESIDUE_FRAC = 0.25
# "全不透明"取 250 而非 `FP.ALPHA_MIN`（=8，产品侧"算不算内容"的阈值）：半透明像素是我方与
# 面板的**混合色**，两种 z 序下都可能部分命中面板色，会把占比这个数糊掉。
OPAQUE_MIN = 250


def spawn_panel(layer: str, zone: int = BAND_PX) -> subprocess.Popen | None:
    if not PANEL_BIN.exists():
        print(f"[panel] ✗ 未找到 {PANEL_BIN}；先 "
              "`cargo build --offline --bin exclusive_panel`")
        return None
    env = {**os.environ, "ZONE": str(zone), "HEIGHT": str(BAND_PX),
           "TTL_SECS": str(PANEL_TTL), "LAYER": layer}
    return subprocess.Popen([str(PANEL_BIN)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def panel_up() -> bool:
    return any(d.get("namespace") == PANEL_NS for d in H15.niri_layers())


def eyeball_secs() -> int:
    """`--eyeball [秒]`：留一段**只给人眼**的观察窗（默认 60 s）。
    为什么需要人眼：本机两条抓屏路（grim 与 fidus 引擎）共用 `zwlr_screencopy`
    （在 `fidus.abi3.so` 里能直接看到 `zwlr_screencopy_` 符号），XWayland 的 XGetImage 又看不见
    Wayland 表面 ⇒ "没进抓帧"与"没上屏"在**仪器**上分不开，只有人不分。"""
    if "--eyeball" not in sys.argv:
        return 0
    i = sys.argv.index("--eyeball")
    try:
        return max(5, int(sys.argv[i + 1])) if i + 1 < len(sys.argv) else 60
    except ValueError:
        return 60


def eyeball_hold(app, secs: int, ask: str) -> None:
    """人眼窗：**只出问句、不代答**。两扇（N1 一扇问"在不在"，N1b 一扇问"谁压谁"），
    因为这两个问题各自要求一个桌面状态：桌宠在原位／桌宠被顶进保留带。"""
    print(f"VERDICT-EYEBALL : {ask}　观察窗 {secs} s（期间别点别拖，答案只由人给）")
    t_end = time.perf_counter() + secs
    while time.perf_counter() < t_end:
        H4.pump(app, 500)
    print("VERDICT-EYEBALL : 观察窗结束 ⇒ 继续跑量具臂")


def wait_panel(app, want: bool, timeout: float = 10.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if panel_up() is want:
            return True
        H4.pump(app, 200)
    return False


def band_rows_visible() -> tuple[int, int]:
    """grim 里数出"这一行整体是面板色"的行 ⇒ (可见行数, 抓帧高)。"""
    shot = A10.capture("h16_band")
    if shot is None:
        return (-1, -1)
    want = np.array(PANEL_RGB, dtype=np.int16)
    hits = np.mean(np.abs(shot.astype(np.int16) - want), axis=2) < 12
    rows = np.count_nonzero(hits.all(axis=1))
    return (int(rows), int(shot.shape[0]))


def layer_inventory() -> list[tuple[str, str]]:
    """合成器眼中的**非壁纸**浮层在册清单 ⇒ [(namespace, layer)]（不经我方任何代码）。
    尺子只能回答"像素在不在请求位"；"表面到底有没有被合成器收下"要问 niri。"""
    return [(str(d.get("namespace", "?")), str(d.get("layer", "?")))
            for d in H15.niri_layers()
            if d.get("namespace") != H15.WALLPAPER_NS]


def rects(app):
    s = app.primaryScreen()
    return H15.rect_of(s.geometry()), H15.rect_of(s.availableGeometry())


def measured(box, off, tag, geom):
    """(尺子中心 − 产品请求中心, 峰值)；抓帧本身失败才返回 None（峰值低照实回，由调用方判闸）。"""
    truth = H12.box_truth(box, off, tag)
    if truth is None:
        return None
    cx = geom[0] + geom[2] / 2.0
    cy = geom[1] + geom[3] / 2.0
    return (truth[0] - cx, truth[1] - cy, truth[2])


def gated(m):
    """读数可用 = 抓到了图且过在位闸。"""
    return m is not None and m[2] >= H12.PRESENT_PEAK


def place_by_product(app, host, fw, fh):
    """摆尺寸后调用**产品自己的**可用区派生摆放，回读它据以请求的矩形。"""
    host.resize(fw, fh)
    host.sprite_label.setGeometry(0, 0, fw, fh)
    H4.pump(app, 300)
    host._place_bottom_right()
    H4.pump(app, 350)
    return host._layer_geometry()


def run_one_switch(app, host, box, off, tag, fw, fh):
    """一次真实切换（fidus 已开）：摆位→挂载→等收尾→量真值。"""
    host.places.clear()
    host.bubbles.clear()
    if host.isVisible() and getattr(host, "_layer_backend", None) is not None:
        host._set_layer_mode(False)
        H4.pump(app, 500)
    geom = place_by_product(app, host, fw, fh)
    t0 = time.perf_counter()
    host._set_layer_mode(True)
    mount_ms = (time.perf_counter() - t0) * 1000.0
    stuck = H15.run_until_settled(app, host)
    H4.pump(app, 400)
    m = None if stuck else measured(box, off, tag, geom)
    return geom, mount_ms, stuck, m, list(host.places), list(host.bubbles)


def reading(geom, m):
    """一条出口一段事实：量具断也要把峰值报出来，不许只说"看不见"。"""
    if m is None:
        return "✗ 抓帧不可得（grim 未出图）⇒ 本臂无读数"
    gate = "" if m[2] >= H12.PRESENT_PEAK else f"（峰＜在位闸 {H12.PRESENT_PEAK}）"
    return (f"产品请求 {geom}｜尺子−请求 = ({m[0]:+.2f},{m[1]:+.2f}) px "
            f"峰={m[2]:.4f}{gate}")


def requested_tl(geom, off, edge):
    """由『产品请求矩形 + 选盒偏移』算出盒左上应落在屏上的物理位置（不经任何测量）。"""
    cx = geom[0] + geom[2] / 2.0 + off[0]
    cy = geom[1] + geom[3] / 2.0 + off[1]
    return int(round(cx - edge / 2.0)), int(round(cy - edge / 2.0))


def ncc_at(shot_rgb, box, x, y):
    """在**指定**左上直接算一次盒的零均值 NCC（不做全局搜索）——把『图案在不在请求点』
    与『全局 argmax 被大平面带偏到哪儿』拆成两个数。越界返回 None。"""
    th, tw = box.shape[0], box.shape[1]
    if x < 0 or y < 0 or x + tw > shot_rgb.shape[1] or y + th > shot_rgb.shape[0]:
        return None
    win = H12.luma(shot_rgb[y:y + th, x:x + tw])
    a = win - win.mean()
    b = box - box.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 1e-9 else -2.0


def panel_hits(shot_rgb):
    """这一片像素是不是面板自涂的纯色（与 `band_rows_visible` 同一判据、同一容差）。"""
    want = np.array(PANEL_RGB, dtype=np.int16)
    return np.mean(np.abs(shot_rgb.astype(np.int16) - want), axis=2) < 12


def occlusion(shot_rgb, geom, frame):
    """z 序的**直接**量法：只问"我方**全不透明**的像素、且落在保留带行里"的那些点，
    屏上这一处是不是面板色。
    面板在我方之上 ⇒ 这些点几乎全是面板色；面板在我方之下 ⇒ 这些点是我方内容（透明处会透出面板色，
    所以**不能**拿整块请求矩形的占比当判据——那在两种 z 序下都非零）。"""
    hits = panel_hits(shot_rgb)
    opaque = frame[..., 3] >= OPAQUE_MIN
    sh, sw = hits.shape[:2]
    y0, y1 = max(0, geom[1]), min(sh, geom[1] + geom[3], BAND_PX)
    x0, x1 = max(0, geom[0]), min(sw, geom[0] + geom[2])
    if y1 <= y0 or x1 <= x0:
        return (0, None)
    fy0, fy1 = y0 - geom[1], y1 - geom[1]
    fx0, fx1 = x0 - geom[0], x1 - geom[0]
    sel = opaque[fy0:fy1, fx0:fx1]
    n = int(sel.sum())
    if n == 0:
        return (0, None)
    return (n, float(hits[y0:y1, x0:x1][sel].mean()))


def discriminate(box, off, geom, tag, frame):
    """一次抓屏给三个数：全局 argmax（峰、左上）、请求点直算 NCC、以及保留带里的 z 序量。
    前两个把"尺子被大平面带偏"与"桌宠真没了"分开；第三个（`occlusion`）把"我们被压在 Top 带下面"
    这一机制**直接量出来**，不靠推定——N3 已证"我们后映射的 OVERLAY 仍在上面"，Top 层那一相是否相反只能量。"""
    shot = A10.capture(tag)
    if shot is None:
        return None
    bl = H12.luma(box)
    peak, ax, ay = A10.Correlator(H12.luma(shot)).peak(bl)
    rx, ry = requested_tl(geom, off, box.shape[0])
    n_occ, panel_frac = occlusion(shot, geom, frame)
    return {"argmax_peak": peak, "argmax_tl": (ax, ay), "req_tl": (rx, ry),
            "at_peak": ncc_at(shot, bl, rx, ry),
            "occ_pixels": n_occ, "occ_panel": panel_frac}


def nudge(host, x: int, y: int) -> bool:
    """不走产品的 `_fidus_place`：它要 `self._fidus_surface`，而那个字段只在 fidus
    真的起跑过时才被写。本臂 fidus 是关的，直接对后端下一次 `set_position`。"""
    backend = getattr(host, "_layer_backend", None)
    setter = getattr(backend, "set_position", None)
    if not callable(setter):
        return False
    try:
        setter(int(x), int(y))
    except Exception as exc:
        print(f"[nudge] set_position 失败：{exc}")
        return False
    return True


def _robust_extent(counts, area: int) -> tuple[int, int]:
    """一维投影的**稳健**区间：取累计面积落在 [0.5%, 99.5%] 的那一段。
    直接用 `np.nonzero` 的 min/max 会被**单个**噪声像素（桌面指针、时钟、壁纸动画）把包围盒
    撑到整屏——那一格读数就全废了，所以按面积分位收边。"""
    if area <= 0:
        return (0, 0)
    c = np.cumsum(counts) / float(area)
    lo = int(np.searchsorted(c, 0.005))
    hi = int(np.searchsorted(c, 0.995))
    return (lo, hi + 1)


def _diff_stats(a, b):
    """两发抓帧的差集统计：稳健包围盒＋面积＋差集处我方那侧的亮度均值/标准差。
    精度自觉：`> 24` 的均差阈会吃掉低对比边缘（合成自检实测少 2 列）⇒ 报的是"实际画出来的
    那块"的近似边界，两臂同一把尺，比的是相对形状与量级，不背书边界。"""
    d = np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)), axis=2) > 24
    d &= ~panel_hits(b)                          # 面板自身若被重绘，不污染轮廓
    area = int(d.sum())
    if area == 0:
        return {"bbox": None, "area": 0, "luma_mean": None, "luma_std": None}
    x0, x1 = _robust_extent(d.sum(axis=0), area)
    y0, y1 = _robust_extent(d.sum(axis=1), area)
    lum = H12.luma(a)[y0:y1, x0:x1][d[y0:y1, x0:x1]]
    return {"bbox": (x0, y0, x1, y1), "area": area,
            "luma_mean": float(lum.mean()), "luma_std": float(lum.std())}


def ambient_diff(app, tag: str):
    """**基线**：什么都不动，隔 1.5 s 抓两发做差。
    N0 那一臂的差集包围盒比我们的请求矩形**还宽**（实测 x 593..1339 vs 请求 854..1315）⇒
    屏上另有一处在自行变化的东西（壁纸动画？指针？别的窗口？）。不把它量出来，
    `offscreen_diff` 的绝对数就没法归因——这一发就是它的基线。抓帧不可得返回 None。"""
    a = A10.capture(f"{tag}_a")
    H4.pump(app, 1500)
    b = A10.capture(f"{tag}_b")
    if a is None or b is None:
        return None
    return _diff_stats(a, b)


def offscreen_diff(app, host, tag: str, at, out_x: int):
    """N1g 的量具：**只挪、不重挂**。当前位抓一发 → 把 surface 整块挪到屏右之外 →
    再抓一发 → 挪回。两发的**唯一**差别是我方 surface 在不在屏内（面板、壁纸、Qt 窗口
    谁都没被重新映射），所以差集＝**合成器实际画在屏上的我方像素**，一个点都不经模板匹配。
    这跟被删掉的"藏／显做差"不是一回事：那条要 `_set_layer_mode(False)`，它连带把可交互
    的 Qt 窗口也映射回来，差集里混进了窗口与动画。niri 不夹层表面位置（H15 A3 实测），
    所以"挪出屏"是造得出来的。读它必须配 `ambient_diff` 一起读（那臂的数是本臂的底）。
    抓帧或挪位不可得返回 None。"""
    a = A10.capture(f"{tag}_a")
    if a is None or not nudge(host, out_x, at[1]):
        nudge(host, at[0], at[1])
        return None
    H4.pump(app, 1500)
    b = A10.capture(f"{tag}_b")
    nudge(host, at[0], at[1])                    # 复位：本臂之后的读数仍按请求位算
    H4.pump(app, 800)
    if b is None:
        return None
    return _diff_stats(a, b)


def sticky_error() -> str:
    """读产品自己的**黏性错误**：`layer_last_error` 这个符号产品 Python 侧没绑（
    `wayland_layer.py:208` 自己写着"目前没有绑定"），所以探针直接 CDLL 同一个 `.so` 读它。
    为什么值得绕这一步：`state.rs` 的帧闸门在"声称尺寸 ≠ configure 给的逻辑尺寸"时**每帧丢弃**
    并把这个原因写进黏性槽 ⇒ 那是"我方为什么没上屏"的**第一手**读数，不经抓帧、不经人眼。
    它是进程级全局且不清零 ⇒ 必须配一个早先状态的对照读（N0），只有**变化**才是证据。"""
    try:
        lib = ctypes.CDLL(str(REPO_ROOT / "liblayer_shell_shim.so"))
        lib.layer_last_error.restype = ctypes.c_char_p
        return (lib.layer_last_error() or b"").decode("utf-8", "replace") or "（空＝无错误）"
    except Exception as exc:                       # 读不到只登记，不顶掉任何臂
        return f"（读不到：{type(exc).__name__}: {exc}）"


def quiescence(tag: str, amb, quiet_before: bool) -> bool:
    """**桌面静置闸**（N 轮的教训）：底噪超上限时，"什么都不动"的屏就已经在自行变化 ⇒
    `offscreen_diff` 的差集不再可归因，任何"轮廓不同／像素没了"的读数是环境给的，不是面板给的。
    闸口只在**跨状态**时出声（同一状态里连刷两闸是噪声）；返回值＝本状态起是否静。"""
    area = None if amb is None else amb["area"]
    quiet = area is not None and area <= QUIESCENT_MAX_PX
    if quiet != quiet_before:
        print(f"VERDICT-QUIESCENCE : {tag} 底噪 {'—' if area is None else area} px"
              f"（闸 {QUIESCENT_MAX_PX}）⇒ "
              + ("桌面静" if quiet else
                 "**桌面在动**｜做差臂（N0 差集／N1g）本轮**不判**，只有匹配臂（尺子峰／在册"
                 "／N2／N3）仍可读"))
    return quiet


def squash_sweep(shot_rgb, box, ratios):
    """把模板按**只压垂直**的几档比例重采样，各自做一次全局峰。
    目的：把"内容还在、但被非等比塞进变小的矩形"这一形与"内容没了"分开——
    前者会在某个 ratio 上把峰拉回接近 1.0，后者无论怎么缩都上不去。"""
    corr = A10.Correlator(H12.luma(shot_rgb))
    luma_box = H12.luma(box)
    edge = luma_box.shape[0]
    out = []
    for r in ratios:
        e = max(16, int(round(edge * r)))
        img = Image.fromarray(luma_box.astype(np.uint8)).resize((edge, e), Image.LANCZOS)
        peak, x, y = corr.peak(np.asarray(img, dtype=np.float64))
        out.append((r, (edge, e), peak, (x, y)))
    return out


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    fact("锚点", __import__("fidus").__git_commit__)
    fact("面板", f"{PANEL_BIN.name} LAYER=top/overlay BAND={BAND_PX} ns={PANEL_NS}")

    frames, _rinfo, l_host, _l_widget = H7.H3.render_live2d_frames(app, 1)
    if not frames:
        print("VERDICT-H16 : ✗ 活体渲染不可得 ⇒ 量具自身失效，不猜")
        return 1
    l_host.hide()
    H4.pump(app, 400)
    frame = frames[0]
    fh, fw = frame.shape[:2]
    qf = H1.qimage_from(frame)
    box, off = H12.probe_box(frame)
    if box is None:
        print("VERDICT-H16 : ✗ 选不出独立量具盒 ⇒ 量具自身失效，不猜")
        return 1

    from meapet.config.store import load_config
    cfg = load_config()
    cfg.setdefault("fidus", {})["enabled"] = False

    host = H15.WiringHost(cfg)
    label = QLabel(host)
    label.render_offscreen = lambda: qf                     # type: ignore[attr-defined]
    host.sprite_label = label                               # type: ignore[attr-defined]
    host.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    host.setAttribute(Qt.WA_QuitOnClose, False)
    host.show()
    H4.pump(app, 600)

    rc = 0
    proc = None
    try:
        # ───────────────────────────────────────────── N0 无面板对照
        geo_b, avail_b = rects(app)
        fact("面板前", f"geometry={geo_b} available={avail_b} ⇒ "
                       f"{'两矩形重合' if geo_b == avail_b else '本就不重合'}")
        host._init_layer_overlay_mode()      # 建 backend + 开关面板（本件不再动它）
        H4.pump(app, 1200)
        _g0, _ms0, stuck0, m_n, _pl0, _bub0 = run_one_switch(
            app, host, box, off, "h16_n0", fw, fh)
        tool_ok = (not stuck0) and gated(m_n)
        print("VERDICT-N0 : 无面板｜" + reading(_g0, m_n))
        inv0 = layer_inventory()
        fact("N0 在册", f"合成器眼中的非壁纸浮层 = {inv0}")
        se0 = sticky_error()                    # 黏性错误的**对照读**：它不清零，只有变化算证据
        fact("N0 黏性槽", f"帧闸门最后一次拒绝的原因 = {se0}")
        amb0 = ambient_diff(app, "h16_amb0")
        fact("N0 环境底", f"什么都不动、隔 1.5 s 两发做差 = {amb0}")
        quiet = quiescence("N0（无面板）", amb0, True)
        fp0 = None if not quiet else offscreen_diff(app, host, "h16_n0", _g0, geo_b[2])
        fact("N0 差集轮廓", f"挪出屏做差 = {fp0}　请求 = {_g0}　"
                            f"（桌宠全不透明像素 {(frame[..., 3] >= OPAQUE_MIN).sum()} 个）"
                            + ("" if quiet else "｜**桌面不静 ⇒ 本行不可用**"))
        if not tool_ok:
            print("VERDICT-H16 : ✗ 对照臂量具断 ⇒ 全案不产出读数")
            rc = 1

        # ───────────────────────────────────── N1 面板在（Top 层）、fidus 关
        print("\n=== N1 · Top 层带 exclusive-zone 的面板：Qt 看得见吗？落点被挪了吗？ ===")
        panel_ok = False                      # N2 的前提：那种面板**在场**（N4 会撤一次再挂回）
        proc = spawn_panel("top")
        if proc is None or not wait_panel(app, True):
            print("VERDICT-H16 : ✗ 面板挂不上 ⇒ 本机造不出这一格，不猜")
            rc = 1
        else:
            panel_ok = True
            H4.pump(app, 1200)
            se_pre = sticky_error()             # 面板刚挂、**还没重挂**我方：人说"这时桌宠被挤到
            fact("面板后黏性槽", f"未重挂我方，帧闸门最后一次拒绝 = {se_pre}")   # 下面"那一相
            geo_a, avail_a = rects(app)
            rows, shot_h = band_rows_visible()
            fact("面板后", f"geometry={geo_a} available={avail_a} ⇒ "
                           f"{'Qt 看见了保留区' if avail_a != avail_b else 'Qt 没看见（两矩形仍逐位相同）'}")
            fact("面板可见性", f"grim 中整行为面板色的行数 = {rows}（抓帧高 {shot_h}，"
                               f"面板自述厚度 {BAND_PX}）")
            _g1, _ms1, stuck1, m_p, _pl1, _bub1 = run_one_switch(
                app, host, box, off, "h16_n1", fw, fh)
            inv1 = layer_inventory()
            print("VERDICT-N1 : 有面板｜" + reading(_g1, m_p))
            fact("N1 在册", f"合成器眼中的非壁纸浮层 = {inv1}（N0 为 {inv0}）")
            se1 = sticky_error()
            fact("N1 黏性槽", f"面板在场、重挂我方之后 = {se1}")
            # N1i：黏性槽是**原因层**的读数（不经抓帧、不经人眼）。三读对比只认"变化"：
            # 面板前＝静 ⇒ 面板后＝静 ⇒ 重挂后＝有 ⇒ 闸门是被"重挂时 configure 夹过尺寸"触发的。
            if se1 != se0:
                print(f"VERDICT-N1i : 帧闸门开始拒帧｜N0＝{se0}｜面板后未重挂＝{se_pre}｜"
                      f"重挂后＝{se1}")
                if "size mismatch" in se1:
                    print("VERDICT-N1i : ⇒ **机制读到第一手**：我方提交的帧尺寸 ≠ niri 在 "
                          "configure 里给的逻辑尺寸（保留区把可用区夹小之后我方请求的高度装不下）"
                          "⇒ 每一帧被丢弃 ⇒ 既不上屏也不进抓帧。**不是抓帧瞎了，也不是合成器"
                          "抹掉了我们，是我方自己的闸门把不匹配的帧全数退回**")
                    rc = 2
                else:
                    print("VERDICT-N1i : ⇒ 拒帧原因不是尺寸失配，见原文，机制另判")
            else:
                print(f"VERDICT-N1i : 黏性槽未变（仍＝N0 的 {se0}）⇒ 帧闸门这一路没给出原因")
            secs = eyeball_secs()
            if secs:
                eyeball_hold(app, secs,
                             f"【第一窗·挂载位】请看屏幕：顶部该有一条通宽的鲑红横条（grim 数到 {rows} 行、面板自述 "
                             f"{BAND_PX} px），桌宠请求落在 {_g1}（内容实际在屏行 203..703、右下）。"
                             f"两问各答一次——**红条在不在**？**桌宠看得见吗**（要看到它在动，"
                             f"静态贴片不算）")
            if not tool_ok:
                print("VERDICT-N1 : 不判（对照臂量具断）")
                rc = rc or 1
            else:
                # (1) 落点：产品请求矩形是纯 Qt 侧的量，不经尺子 ⇒ 保留带挪没挪它，直接比 N0
                moved = (_g1 != _g0)
                py0, ph = _g1[1], _g1[3]
                overlap = max(0, min(py0 + ph, BAND_PX) - max(py0, 0))
                fact("N1 几何", f"产品请求矩形 y=[{py0},{py0 + ph})，保留带占屏行 [0,{BAND_PX}) "
                                f"⇒ 行重叠 {overlap} px；请求矩形与 N0 "
                                f"{'不同（请求被挪）' if moved else '逐位相同（请求未变）'}")
                # (2) 尺子被带偏了还是桌宠真没了：全局 argmax 与请求点直算，分开看
                d = discriminate(box, off, _g1, "h16_n1_disc", frame)
                if d is None or d["at_peak"] is None:
                    print("VERDICT-N1 : 判别抓帧/直算不可得 ⇒ 该格只报几何，不猜机序")
                else:
                    intact = d["at_peak"] >= H12.PRESENT_PEAK
                    verdict = ("桌宠仍在请求位，是全局搜索被大平面带偏（尺子侧混淆）"
                               if intact else "桌宠在请求位也确实读不到（遮挡／未落对）")
                    fact("N1 判别", f"全局 argmax 峰={d['argmax_peak']:.4f} @左上{d['argmax_tl']}　"
                                    f"请求点{d['req_tl']}直算峰={d['at_peak']:.4f} ⇒ {verdict}")
                    # (3) z 序不推定，直接量：保留带行内我方**全不透明**那些点，屏上是不是面板色
                    edge = box.shape[0]
                    bx, by = d["req_tl"]
                    band_ovl = max(0, min(by + edge, BAND_PX) - max(by, 0))
                    fact("N1 盒行", f"盒边长 {edge} px、请求左上 ({bx},{by}) ⇒ 盒占屏行 "
                                    f"[{by},{by + edge})，与保留带 [{0},{BAND_PX}) 行重叠 "
                                    f"{band_ovl} px")
                    n_occ, frac = d["occ_pixels"], d["occ_panel"]
                    if frac is None:
                        print("VERDICT-N1-Z : 不判（保留带行内没有我方全不透明像素 ⇒ "
                              "该位置下量不到 z 序；把桌宠顶进带里再量，见 N1b）")
                    else:
                        why = ("**我们被 Top 带压在下面**（遮挡成立，读不到有此一因）"
                               if frac > 0.5 else
                               "遮挡**不成立**（我方仍在上面），读不到另有其因")
                        print(f"VERDICT-N1-Z : 保留带行内我方全不透明像素 {n_occ} 个，"
                              f"其上读成面板色的占 {frac:.3f} ⇒ {why}")
                    # (4) 读不到是**常驻**还是**暂态**：同位置再等 1.5 s 重量一次。
                    #     niri 默认给 layer surface 开淡入动画 ⇒ 抓早了读到的是半透明混合，
                    #     与遮挡在两形里长得一样，只能用时间把它们分开。
                    H4.pump(app, 1500)
                    dl = discriminate(box, off, _g1, "h16_n1_late", frame)
                    if dl is None or dl["at_peak"] is None:
                        print("VERDICT-N1-T : 不判（重读抓帧不可得）")
                    else:
                        grew = dl["at_peak"] - d["at_peak"]
                        late_ok = dl["at_peak"] >= H12.PRESENT_PEAK
                        shape = ("暂态（淡入／未提交），等定即可读" if late_ok
                                 else "常驻：不是抓早了，另有其因")
                        print(f"VERDICT-N1-T : 多等 1.5 s 后请求点峰={dl['at_peak']:.4f}"
                              f"（前值 {d['at_peak']:.4f}，Δ{grew:+.4f}）⇒ {shape}")
                    # (4b) N1d：**只压垂直**的几档比例各做一次全局峰。若某一档把峰拉回
                    #     ≈1.0，"内容还在、被非等比塞进变小的矩形"这一形就成立；
                    #     全都上不去 ⇒ 不是拉伸，是内容层面没了（遮挡／没提交／另物）。
                    sq_shot = A10.capture("h16_n1_squash")
                    if sq_shot is None:
                        print("VERDICT-N1d : 不判（抓帧不可得）")
                    else:
                        ratios = (1.0, 0.9, 0.8, 0.725, 0.707, 0.6, 0.5)
                        sweep = squash_sweep(sq_shot, box, ratios)
                        best = max(sweep, key=lambda r: r[2])
                        ctrl = next(r for r in sweep if r[0] == 1.0)
                        for r, shape_hw, pk, tl in sweep:
                            print(f"  N1d 竖压 {r:.3f}→盒 {shape_hw[1]} px：全局峰={pk:.4f} @{tl}")
                        print(f"VERDICT-N1d : 最优 {best[0]:.3f} 峰={best[2]:.4f}"
                              f"（对照 1.0 档 {ctrl[2]:.4f}）⇒ "
                              + ("**拉伸形成立**：内容还在，只是被压进变小的矩形"
                                 if best[2] >= H12.PRESENT_PEAK and best[0] < 1.0 else
                                 ("内容完好、是尺子/搜索侧的事" if best[2] >= H12.PRESENT_PEAK
                                  else "没有任何一档把峰拉回来 ⇒ 不是非等比拉伸")))
                    # (5g) N1g：**我方到底有没有被画进这张图**——只挪、不重挂，两次抓帧做差。
                    #     判别／N1-T／N1d 只能说"模板匹配不上"，说不清像素去了哪儿。
                    #     这一臂完全不经模板匹配：差集＝合成器实际画在屏上的我方轮廓。
                    #     **底先量再挪**：底噪不过闸（桌面在动）时本臂不判——轮廓比对要跨 N0／N1
                    #     两个状态，而中间恰好隔着挂面板那 1.2 s，桌面自己动过就把两态糊成一态。
                    amb1 = ambient_diff(app, "h16_amb1")
                    fact("N1g 环境底", f"什么都不动、隔 1.5 s 两发做差 = {amb1}")
                    quiet = quiescence("N1（挂面板后）", amb1, quiet)
                    fp1 = None if not quiet else offscreen_diff(app, host, "h16_n1g", _g1, geo_a[2])
                    fact("N1g 在册", f"做差收尾时非壁纸浮层 = {layer_inventory()}")
                    n1g_says = "未跑"
                    if not quiet:
                        print("VERDICT-N1g : 不判（桌面不静 ⇒ 做差量具失效，见 QUIESCENCE 行）")
                        n1g_says = "不判：桌面不静"
                    elif fp1 is None:
                        print("VERDICT-N1g : 不判（挪出屏做差不可得）")
                        n1g_says = "不判：做差不可得"
                    elif fp1["bbox"] is None:
                        print("VERDICT-N1g : 差集 0 像素 ⇒ 面板在场时我方**整层不在抓帧里**"
                              "（挪出屏与不挪，屏上一样）⇒ 读不到与落点无关，是合成／抓取"
                              "这一层没了")
                        n1g_says = "N1g：我方像素一个都没进抓帧"
                        rc = 2                     # 预期里的红，见 docstring 退出码
                    elif amb1 is not None and fp1["area"] <= 2 * max(amb1["area"], 1):
                        print(f"VERDICT-N1g : 有面板时挪出屏带来的变化 {fp1['area']} px"
                              f"（轮廓 {fp1['bbox']}）＝同一状态下'什么都不动'的底噪 "
                              f"{amb1['area']} px（轮廓 {amb1['bbox']}）⇒ **我方对这张图的"
                              "贡献实测为零**：合成器在册（N1f）、请求矩形没被挪（N1 几何），"
                              "但像素一个都没落进抓帧")
                        n1g_says = "N1g：我方贡献在底噪内（＝零）"
                        rc = 2                     # 同上：预期里的红
                    elif fp0 and fp0["area"] and fp1["area"] < RESIDUE_FRAC * fp0["area"]:
                        frac = 100.0 * fp1["area"] / fp0["area"]
                        print(f"VERDICT-N1g : 有面板时挪出屏带来的变化 {fp1['area']} px"
                              f"（轮廓 {fp1['bbox']}），而无面板时同一操作的基线是 {fp0['area']} px"
                              f"（轮廓 {fp0['bbox']}）⇒ 只有基线的 {frac:.1f}%"
                              f"（底噪 {'—' if amb1 is None else amb1['area']} px）。"
                              "**不判轮廓**：把整层挪出屏能变出的量只能是我方内容的面积，"
                              "这个量连我方面积的零头都不到 ⇒ 它是散点残余（时钟／指针／他人动画），"
                              "不是'我方轮廓被面板挪了位'。读出来的只有一句："
                              "我方在请求位上对这张图的贡献 ≈ 0")
                        n1g_says = f"N1g：我方贡献≈0（差集 {fp1['area']} px＝基线的 {frac:.1f}%，是残余）"
                        rc = 2                     # 同上：预期里的红
                    else:
                        bx0, by0, bx1, by1 = fp1["bbox"]
                        d2 = -1 if not fp0 or not fp0["bbox"] else max(
                            abs(bx0 - fp0["bbox"][0]), abs(by0 - fp0["bbox"][1]),
                            abs(bx1 - fp0["bbox"][2]), abs(by1 - fp0["bbox"][3]))
                        same = d2 >= 0 and d2 <= 2
                        print(f"VERDICT-N1g : 有面板时我方实际轮廓 ({bx0},{by0})-({bx1},{by1}) "
                              f"面积 {fp1['area']}（请求 {_g1}｜N0 轮廓 "
                              f"{'—' if not fp0 or not fp0['bbox'] else fp0['bbox']} "
                              f"面积 {'—' if not fp0 else fp0['area']}｜底噪 "
                              f"{'—' if amb1 is None else amb1['area']}）")
                        print("VERDICT-N1g : ⇒ " + (
                            "轮廓与无面板时逐位相同 ⇒ 合成器**照请求画了同一块内容**，"
                            f"模板却匹配不上（差集处亮度 std={fp1['luma_std']:.1f}）"
                            "⇒ 变的是**内容**，不是落点／尺寸"
                            if same else
                            f"轮廓与无面板时不同（最大边差 {d2}）⇒ 面板改了落点或尺寸"))
                        n1g_says = ("N1g：轮廓与无面板时相同（内容变了）" if same
                                    else "N1g：轮廓与无面板时不同")
                    # (5) N1e：**可见性**——把桌宠整块挪到保留带**之下**再读一次尺子。
                    #     这一臂只为 N1b 的解读服务：带内读到"全是面板色"有两读（面板压在我们
                    #     之上 ／ 面板在场时我们根本没被合成 ⇒ 带内本就只剩面板色）。只有
                    #     "带外仍能读到"能把后一读排除；带外也读不到，则 z 序这一格无证据。
                    vis = None
                    ex, ey = _g1[0], BAND_PX + 8
                    if not nudge(host, ex, ey):
                        print("VERDICT-N1e : 不判（后端不吃 set_position ⇒ 挪不出去）")
                    else:
                        H4.pump(app, 1800)
                        if secs:                       # 第二窗（P 轮人的读数 B＋1 逼出来的）：
                            # 挂载位上人说"完全看不见"，N1b 位上又说"头压在红条上"——两次之间
                            # 只隔了几发 `set_position`。这一窗问的是**被挪过之后、仍在带外**
                            # 那一态：把"挂载态压根没提交内容"与"面板在场一律不画"分开。
                            eyeball_hold(app, max(15, secs // 3),
                                         f"【第二窗·带外且被挪过】桌宠已挪到 {ex},{ey}（内容整块在鲑红带**下方**、"
                                         f"不与带重叠）——这一相**看得见桌宠吗**？"
                                         f"（看得见答 Y／看不见答 N／一部分答 P）")
                        de = discriminate(box, off, (ex, ey, _g1[2], _g1[3]),
                                          "h16_n1e", frame)
                        if de is None:
                            print("VERDICT-N1e : 不判（带外抓帧不可得）")
                        elif de["at_peak"] is None:
                            print(f"VERDICT-N1e : 不判（挪到 ({ex},{ey}) 后请求盒越出抓帧）")
                        else:
                            vis = de["at_peak"] >= H12.PRESENT_PEAK
                            print(f"VERDICT-N1e : 挪到带外 ({ex},{ey})（请求矩形整块在 "
                                  f"[{BAND_PX},∞)）⇒ 请求点直算峰={de['at_peak']:.4f}"
                                  f"、全局峰={de['argmax_peak']:.4f} @左上{de['argmax_tl']}")
                            print("VERDICT-N1e : ⇒ " + (
                                "**面板在场时我们的内容确实被合成**，只是不在请求位 ⇒ "
                                "带内读到面板色可以当 z 序证据"
                                if vis else
                                "带外也读不到 ⇒ 面板在场时我方内容根本没上屏（或落点与任何"
                                "请求都无关）；此时带内读到什么色都不构成 z 序证据"))
                    # (5b) N1f：把"像素不在"与"表面压根不在册"分开——这一问交给 niri，不交给我方
                    #     任何代码。`niri msg -j layers` 不吐几何，但**在不在、在哪一层**是硬事实。
                    mine_n0 = [t for t in inv0 if t[0] == MINE_NS]
                    mine_n1 = [t for t in inv1 if t[0] == MINE_NS]
                    if not mine_n0:
                        print("VERDICT-N1f : 不判（N0 就不在册 ⇒ 本探针认不出我方 ns，"
                              "面板前后的对比无意义）")
                    elif mine_n1:
                        print(f"VERDICT-N1f : 面板在场时我方**仍在册** {mine_n1}（N0 {mine_n0}）"
                              "⇒ 合成器收下了我们的 surface ⇒ N1e 的读不到发生在**像素**侧、"
                              "不在映射侧")
                    else:
                        print(f"VERDICT-N1f : 面板在场时我方**不在册**（N0 {mine_n0}）"
                              "⇒ 读不到有了完整解释：映射没成立；撤面板后应回到在册，见 N4")
                    # (6) N1b：Top 层的 z 序**直接量**——把桌宠顶进保留带（不透明轮廓覆盖
                    #     屏行 0..180），只看"我方全不透明点上是不是面板色"，不推定层序。
                    by0 = H15.opaque_bbox(frame)[1]
                    nx, ny = _g1[0], -by0
                    if not nudge(host, nx, ny):
                        print("VERDICT-N1b : 不判（后端不吃 set_position ⇒ 造不出带内不透明像素）")
                    else:
                        H4.pump(app, 1800)
                        g1b = (nx, ny, _g1[2], _g1[3])
                        if secs:                       # 第二扇：桌宠头现在**钉在屏行 0**，
                            eyeball_hold(app, max(15, secs // 3),
                                         f"【第三窗·头钉进保留带】桌宠已挪到 {g1b[:2]} ⇒ 不透明轮廓顶端现在贴着屏顶、"
                                         f"整块头部落在鲑红带里（grim 数到整行面板色 {rows} 行）。"
                                         f"一问——**桌宠的头压在红条上，还是红条压住桌宠的头？**"
                                         f"（看不见桌宠也照样说，那是第三答案）")
                        db = discriminate(box, off, g1b, "h16_n1b", frame)
                        fact("N1b 黏性槽", f"挪到带内 {g1b[:2]} 之后（人说这一相**看得见**）= "
                                           f"{sticky_error()}")
                        rows_b, _hb = band_rows_visible()
                        if db is None:
                            print("VERDICT-N1b : 不判（重定位后抓帧不可得）")
                        elif db["occ_panel"] is None:
                            print(f"VERDICT-N1b : 不判（挪到 {g1b[:2]} 后保留带行内仍无我方"
                                  f"全不透明像素）")
                        elif vis is not True:
                            print(f"VERDICT-N1b : 挪到 {g1b[:2]}（整行面板色 {rows_b}）⇒ "
                                  f"保留带行内我方全不透明像素 {db['occ_pixels']} 个，"
                                  f"读成面板色占 {db['occ_panel']:.3f}｜但 N1e 没证成"
                                  f"「面板在场时我方被合成」⇒ **这个占比不能读成 z 序**")
                        else:
                            we_top = db["occ_panel"] <= 0.5
                            who = ("**桌宠压在 Top 带之上**（面板不遮我们）" if we_top
                                   else "Top 带压在桌宠之上（**遮挡成立**）")
                            print(f"VERDICT-N1b : 挪到 {g1b[:2]}（整行面板色 {rows_b}）⇒ "
                                  f"保留带行内我方全不透明像素 {db['occ_pixels']} 个，"
                                  f"读成面板色占 {db['occ_panel']:.3f} ⇒ {who}")
                        # (6h) N1h：**坐标空间被保留区挪了 BAND_PX** 这一读法的直接检验。
                        #     上面每一次匹配都按"margin 即屏坐标"取样（产品、尺子、`occlusion`
                        #     共用这个假设）。若真实落点＝`保留带厚度 + margin`，把同一个矩形整体
                        #     下移 BAND 再量一次，峰应回到在位闸之上、偏移≈0。这一读不回读
                        #     configure、不经黏性槽、不经人眼——它给的就是"我方内容在屏上的坐标"。
                        #     跑在 N1b 那一相（唯一"人说看得见"的相）：帧闸门在那儿不拒帧
                        #     （configure 高 629 ≥ 614），内容真在屏上，量得到才有资格判假设。
                        g1h = (nx, ny + BAND_PX, _g1[2], _g1[3])
                        m1h = measured(box, off, "h16_n1h", g1h)
                        if m1h is None:
                            print("VERDICT-N1h : 不判（抓帧不可得）")
                        else:
                            ap = "—" if db is None or db["at_peak"] is None else f"{db['at_peak']:.4f}"
                            hit = gated(m1h)
                            print(f"VERDICT-N1h : 同一相：按**请求** {g1b[:2]} 直算峰={ap}"
                                  f"｜按**请求+保留带 {BAND_PX}** {g1h[:2]} 量：尺子−请求 = "
                                  f"({m1h[0]:+.2f},{m1h[1]:+.2f}) px 峰={m1h[2]:.4f}")
                            print("VERDICT-N1h : ⇒ " + (
                                "**坐标模型成立**：niri 把 Top 带扣掉的那 180 px 算进了我们的 margin"
                                " ⇒ 真实落点＝保留带厚度＋请求 y。于是 N1b 那个占比 1.000 是"
                                "**尺子按旧假设把取样点整块落在面板上**的必然结果，不是 z 序证据；"
                                "『我方 surface 被合成器分到哪块矩形』本地**可**观测（这一行就是回读）"
                                if hit else
                                "夹移假设**不成立**（挪到请求+BAND 仍不过在位闸）⇒ 落点另有机制，"
                                "N1b 的占比与 N1e 的读不到都还悬着"))
                        nudge(host, _g1[0], _g1[1])            # 复位，别把 N2 的起点搞脏
                        H4.pump(app, 600)
                    # (7) N4：**撤面板再走一次同样的切换**——把"只在面板在场期间坏"与
                    #     "我方表面被弄坏了（不可逆）"分开。N2 用同一次挂载后的 fresh remount
                    #     已证"面板在场时跨重挂仍读不到"，这一臂问反方向。
                    if proc is not None:
                        proc.terminate()
                        proc = None
                    gone = wait_panel(app, False)
                    H4.pump(app, 1500)
                    inv4 = layer_inventory()
                    _g4, _ms4, stuck4, m4, _pl4, _bub4 = run_one_switch(
                        app, host, box, off, "h16_n4", fw, fh)
                    fact("N4 撤销", f"面板已撤={gone}　在册={inv4}　请求={_g4}")
                    print("VERDICT-N4 : 撤面板后｜" + reading(_g4, m4))
                    if not gone or stuck4 or m4 is None:
                        print("VERDICT-N4 : 不判（撤销未确认或本臂无读数）")
                        rc = rc or 1
                    elif gated(m4):
                        print("VERDICT-N4 : ⇒ 尺子回到在位闸之上 ⇒ 干扰只存在于'那种面板在场'期间，"
                              "可逆；我方表面状态没被改坏")
                    else:
                        print("VERDICT-N4 : ⇒ 撤了仍读不到 ⇒ **不可逆**，我方表面状态或合成器侧"
                              "缓存在那一次之后就不对了（这一形比遮挡严重得多）")
                        rc = 2
                    # N2 的前提是"那种面板在场" ⇒ 重新挂回；挂不上就跳过，不拿无面板的状态冒充。
                    proc = spawn_panel("top")
                    panel_ok = proc is not None and wait_panel(app, True)
                    if not panel_ok:
                        print("VERDICT-N2 : 不判（Top 面板重挂失败 ⇒ 本臂前提造不出来）")
                    if moved:
                        print("VERDICT-N1 : RED 保留区挪动了产品**请求**的矩形")
                        rc = 2
                    else:
                        print("VERDICT-N1 : ⇒ 产品**请求**的矩形未被保留区挪动（这是纯 Qt 侧事实）。"
                              f"真实合成落点这一格按 N1g 的出口说：{n1g_says}"
                              "⇒ 不拿请求矩形冒充实际落点；面板对尺子的干扰见 N1-Z／判别／"
                              "N1e／N1f／N1g／N4 各行")

        # ───────────────────────────────────── N2 fidus 开：一次真实切换
        print("\n=== N2 · 面板在（Top）、fidus 开：走一次真实切换 ===")
        if tool_ok and panel_ok:
            host._toggle_fidus_enabled()
            fact("N2 开关", f"enabled={host._fidus_enabled()}、`_save_config` 次数={host.saves}")
            g2, ms2, stuck2, m2, places2, bub2 = run_one_switch(
                app, host, box, off, "h16_n2", fw, fh)
            fact("N2 收尾", f"挂载 {ms2:.0f} ms　摆位 {places2}　气泡 {bub2}")
            if stuck2:
                print("VERDICT-N2 : ✗ 超时卡住 ⇒ 本臂无读数")
                rc = 1
            else:
                refused = any("没能量准" in b for b in bub2)
                last = places2[-1] if places2 else None
                # 一次摆位都没有 ⇒ fidus 在探针位移之前就退了（候选全拒／获取定不住）：
                # 桌宠原地未动，本就不需要回摆（#71 的回摆只在"被移动过"时才该发）。
                if not places2:
                    at_mount = True
                else:
                    at_mount = last[:2] == (g2[0], g2[1])
                fact("N2 出口", f"退回={refused}　摆位次数={len(places2)}　最后摆位={last}　"
                                f"挂载位={(g2[0], g2[1])} ⇒ 桌宠在挂载位={at_mount}")
                if refused:
                    if at_mount:
                        why = "获取阶段就退回、从未移动" if not places2 else "移动后回摆到挂载位"
                        print(f"VERDICT-N2 : PASS fidus 在保留带干扰下诚实退回（{why}），无静默错数")
                    else:
                        print("VERDICT-N2 : RED 退回却没把挂载位要回来（桌宠停在探针位移处）")
                        rc = 2
                else:
                    print("VERDICT-N2 : fidus 未退回｜" + reading(g2, m2))
                    if gated(m2):
                        ok = max(abs(m2[0]), abs(m2[1])) <= TOL_PX
                        print(f"VERDICT-N2 : {'PASS' if ok else 'RED'} 校正后判据 ≤ {TOL_PX} px")
                        if not ok:
                            rc = 2
                    else:
                        print("VERDICT-N2 : 未退回且尺子峰值＜在位闸 ⇒ 不判（无法独立确认落点）")
                        rc = rc or 1

        # ─────────────────────── N3 面板换成 OVERLAY（真遮挡相，前提须自证）
        print("\n=== N3 · 遮挡相：面板压在我们之上时，校正是退回还是报错数 ===")
        if tool_ok and host._fidus_enabled():
            host._set_layer_mode(False)
            H4.pump(app, 500)
            if proc is not None:
                proc.terminate()
                proc = None
                wait_panel(app, False)
            proc = spawn_panel("overlay", zone=0)
            if proc is None or not wait_panel(app, True):
                print("VERDICT-N3 : 不判（OVERLAY 面板挂不上）")
            else:
                H4.pump(app, 1000)
                # 面板先挂、我们的 surface 后挂（run_one_switch 里重挂）⇒ 谁在上是待验事实
                g3, ms3, stuck3, m3, places3, bub3 = run_one_switch(
                    app, host, box, off, "h16_n3", fw, fh)
                rows3, _h3 = band_rows_visible()
                # fidus 若动过桌宠，请求矩形不再是它此刻的落点 ⇒ 遮挡量按**最后摆位**算，
                # 否则会拿"应该在的地方"去问"实际在不在上面"，量的是空气。
                g3_now = g3 if not places3 else (places3[-1][0], places3[-1][1], g3[2], g3[3])
                d3 = discriminate(box, off, g3_now, "h16_n3_disc", frame)
                n_occ3 = -1 if d3 is None else d3["occ_pixels"]
                frac3 = None if d3 is None else d3["occ_panel"]
                fact("N3 遮挡前提", f"按最后摆位 {g3_now[:2]}：保留带行内我方全不透明像素 "
                                    f"{n_occ3} 个，读成面板色占 "
                                    f"{'—' if frac3 is None else f'{frac3:.3f}'}"
                                    f"　（整行面板色 {rows3} / 自述厚度 {BAND_PX}）")
                covered = frac3 is not None and frac3 > 0.5
                if d3 is None or frac3 is None:
                    print("VERDICT-N3 : 不判（保留带行内量不到我方全不透明像素 ⇒ z 序这一相"
                          "本机造不出来，无遮挡可量）")
                elif not covered:
                    print("VERDICT-N3 : 不判（面板在我们之下 ⇒ 本机造不出'面板压在桌宠之上'这一相）")
                elif stuck3:
                    print("VERDICT-N3 : ✗ 超时卡住 ⇒ 本臂无读数")
                    rc = 1
                else:
                    refused = any("没能量准" in b for b in bub3)
                    print("VERDICT-N3 : 出口=" + ("退回" if refused else "有值") + "｜" + reading(g3, m3))
                    if not gated(m3):
                        print("VERDICT-N3 : 不判（本臂量具断）")
                        rc = rc or 1
                    elif not refused and max(abs(m3[0]), abs(m3[1])) > TOL_PX:
                        print("VERDICT-N3 : RED 遮挡下报了值却与真值不重合（最坏那一形）")
                        rc = 2
                    else:
                        print("VERDICT-N3 : ⇒ 遮挡下要么响亮退回、要么落点仍与真值重合")
    finally:
        if proc is not None:
            proc.terminate()
        H15.teardown(host, app)
        gone = wait_panel(app, False, timeout=6.0)
        print(f"\nVERDICT-CLEANUP : 面板已撤={gone}｜矩形回到 {rects(app)[1]}（面板前 {avail_b}）")
        if not gone:
            rc = rc or 1

    print("=== 汇总 ===")
    print(f"VERDICT-H16-END : rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
