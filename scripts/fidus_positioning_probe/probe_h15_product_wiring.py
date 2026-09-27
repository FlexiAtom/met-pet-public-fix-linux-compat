#!/usr/bin/env python3
"""H15 · 产品接线的真机跑：被测对象从 `FP.locate` 换成**真 widget 的那半条路**。

H12 证的是"我方那段测量代码在真屏上量得准"；§2 反证表第 1 行还缺另一半——
**菜单点下去之后**那条链：`_set_layer_mode` → `_maybe_start_fidus_locate`
→ `request_locate`(fidus 线程) → `move` 回调经 GUI 泵 → `_fidus_on_result`
→ `_fidus_service` 套用 → `_proxy_rect` 与摆位吃同一个数。这一段单元测不了：
`tests/test_fidus_position.py` 的 `FakeEngine.move` 只是记录器、不碰真桌面，
所以"退回时桌宠停在哪"这种问题它在结构上就看不见。

五臂，各自判一件事（对表一律是 grim + 宿主 NCC，不经 fidus）：

* **A0 默认关**：`config["fidus"]["enabled"]` 缺省值走一次完整挂载 ⇒ 不该有任何测量发生，
  顺带量到"请求 (x,y) 与屏幕真值差多少"（本机信念误差基线）。
* **A1 打开后切一次**：①切换是否**立刻**发生（不等测量）②校正后请求矩形与真值是否重合
  ③`_proxy_rect` 与被摆下的矩形是否同一个数 ④跳位量与耗时。
  ①的**证据形态**在 #78 之后变了：那时 `_set_layer_mode(True)` 直接放 fidus，所以"返回时
  `_fidus_busy` 为真"就是"没同步等测量"的证据；#78 在挂载与 fidus 之间插了一轮回读，
  fidus 还没启动 ⇒ 证据改成"返回时仍有活没干完（回读轮开着 **或** fidus 在跑）"，
  两个原始事实分别印出、不合并。300 ms 那条上限不变。
* **A2 退回（位移之前）**：给一帧只有轮廓、没有纹理的图 ⇒ 引擎结构门不过、候选全拒
  ⇒ 一次 `set_position` 都不该发生，`_proxy_rect` 不该被 fidus 动过。
  2026-09-27 判据修正：零点从"挂载请求"改成"**回读轮落定那一刻的信念**"
  （`WiringHost.fit_baseline`）。#78 的回读轮在分数缩放档会合法夹移（本机 1.5 档
  (212,0)→(212,-102)），旧写法把那一发读成"测不到还乱动"⇒ 1.5 档结构性假红。
  夹移由 `_layer_fit_move` / `_layer_fit_proxy_rect` 两个只记不改的探针侧覆写拿到，
  保留带厚度仍由产品自己算，探针不重推。同一修正适用于 A0 的信念误差与 A1 的跳位。
* **A3 位移撞屏缘**：挂到屏幕右缘，让 +48 px 探针位移越出屏外。
  原意是触发"`locate` 位移后退回却不回摆"：`locate()` 在 `move()` 之后的三条出口
  （重注册失败、定不住、闭环超容差）都不回摆，产品侧 `_fidus_service` 的 None 分支
  原本也只弹气泡——于是"没能量准位置，沿用原来的位置"这句在位移之后就与屏幕不符。
  **实测结论（本机）：niri 不夹 layer surface**，905→953 照单执行，闭环诚实通过、
  校正回到挂载位（净漂移 0.00 px）⇒ 这条出口在 niri 上触发不了，不是路径安全。
  产品侧已补回摆（`_fidus_mount` + None 分支重请求挂载位），A4 就是验它。
* **A4 位移中途换画面**：第一次摆位落地时把 `render_offscreen` 换成同轮廓、
  纹理平移 200 px 的一帧（生产里最常见的等价情形：测量途中桌宠自己动了）。
  闭环该判失败 ⇒ `None` ⇒ 产品该把挂载位**要回来**。这条是 A3 在本机没能触发的
  那条出口的唯一物理触发法：独立尺子改吃换过的那帧，所以真值照样成立。

副作用（真机）：挂/拆 layer 浮层若干次、一次 `calibrate_once`（约 2 s）、若干次整屏抓取、
每次切换的 +48 px 探针位移。`_save_config` 与 `_show_bubble` 换成记录器：前者不写你的
`config.json`，后者不再弹气泡（文案逐条收下来当判据）。其余方法是产品原文。

## 分数缩放（#75）：为什么必须先换算，以及换算之后这把尺子还准多少

三套单位在 scale≠1 下互不相等（实测见
`~/.Athena/projects/meapet/working/fidus-self-window-positioning.md` §12v.1）：
**niri 逻辑**（合成器落位的口径，也是我方填进 `set_position` 的那个数被理解成的口径）、
**Qt 逻辑**（`_layer_geometry()` 给的那个数；Qt5 的 `libqwayland-generic` 不接 fractional-scale，
dpr 会跳到整数 2）、**grim 物理**（独立尺子的像素）。本探针的换算只有一条：
`物理 = niri 逻辑 × SC`。残差**按 niri 逻辑判**——与产品的请求同口径、且与 scale=1 那批可比；
÷SC 最多引入 0.33 px 舍入，反方向 ×SC 会把整型逻辑位量化成 1.5 px 台阶、把量具自己的误差
算到产品头上。物理读数照样印出来。

换算带来的新耦合（**打印成一条 fact，不藏**）：尺子给的是**中心**，而中心 = 拟合左上 +
edge×f/2，所以**屏幕上的框宽**必须知道 ⇒ 落点要经过一个拟合因子 f。对本模板，f 每偏 0.5%
中心就偏约 1 px 量级（数字在跑的时候按当轮模板边长现算）。⇒ **scale≠1 时这把尺子的下限
不再是 0**：残差在 2 px 线内只能读成"未见超出现行判据"，不能读成"逐位重合"。

用法：

    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h15_product_wiring.py

退出码：0 = 四臂都跑到出口且三判据全绿；1 = 量具/环境不可用（不产出任何主张）；
2 = 有臂判红。scale≠1 且**产品的回读轮没放行**（桌宠尺寸装不进合成器配出的逻辑屏）也算 2——
那是产品在这一档下的事实，不是量具断，故单列 `VERDICT-DIM` 一条，其余各臂标"不判（未放行）"。
"""
from __future__ import annotations

import collections
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import probe_a10_geometry as A10  # noqa: E402
import probe_h12_product_path as H12  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402
from meapet.desktop import fidus_position as FP  # noqa: E402
from meapet.desktop.render_host import PetRenderHostMixin  # noqa: E402

TOL_PX = 2.0            # 与 H12 同一条判据
MOUNT_MS_MAX = 300      # 切换必须立刻发生：`_set_layer_mode(True)` 不许等测量
LOCATE_TIMEOUT = 90.0   # 单臂最长等待（实测 2.6–6.3 s，留足余量）
ROLL_PX = 200           # A4 换帧的纹理平移量：远大于闭环容差 24 px，又不会把内容推出轮廓
WALLPAPER_NS = "swww-daemon"
DIM_BUBBLE = "屏幕放不下"   # 产品 `_layer_fit_report` 那条**出声**出口的文案前缀
fact = H12.fact

# ── 单位口径（跑之前由 `read_units()` 填；未填 = 1.0，等价于本探针改动前的行为）
SC = 1.0        # 合成器标称 scale（niri 逻辑 → grim 物理）
DPR = 1.0       # Qt 的 devicePixelRatio
NIRI_LOGICAL = (0, 0)


def read_units(app) -> tuple[float, str]:
    """读三套单位里的前两套，返回 (niri scale, 一句话读数)。只读，不改配置。

    `niri msg output <名> scale` 是**设置**命令，探针没有改分辨率的授权 ⇒ 走 JSON 只读
    （scale 在 `logical.scale`）。读不到返回 nan，由调用方决定停不停。
    """
    global SC, DPR, NIRI_LOGICAL
    try:
        out = json.loads(subprocess.run(["niri", "msg", "-j", "outputs"],
                                        capture_output=True, timeout=8).stdout.decode())
        name, lg = next(iter([(k, v.get("logical", {})) for k, v in out.items()]))
        SC = float(lg["scale"])
        NIRI_LOGICAL = (int(lg["width"]), int(lg["height"]))
    except Exception as exc:  # noqa: BLE001 - 读不到就是不能换算，交给调用方
        SC = float("nan")
        fact("scale 读数", f"✗ 读不到 niri 输出配置（{type(exc).__name__}: {exc}）"
                           "⇒ 换算无从谈起")
        return SC, "读不到"
    scr = app.primaryScreen()
    DPR = float(scr.devicePixelRatio())
    geo = scr.geometry()
    line = (f"niri 逻辑 {NIRI_LOGICAL[0]}×{NIRI_LOGICAL[1]} @scale {SC}　"
            f"Qt 信念 {geo.width()}×{geo.height()} dpr={DPR}")
    fact("三套单位", line + "　（第三套＝grim 物理，见下面每条落点读数）")
    if abs(SC - 1.0) > 1e-9:
        fact("换算", f"物理 = niri 逻辑 × {SC}；残差按 **niri 逻辑 px** 判，同印物理。"
                     "Qt 与 niri 两套逻辑在分数缩放下不是同一个数 ⇒ 这一档分叉"
                     "会计入残差，不是产品的错（见头注）")
    return SC, line


Truth = collections.namedtuple("Truth", "cx cy peak factor w h")
"""独立尺子的落点：`(cx, cy)` 在 **grim 物理 px**；`factor` = 屏幕上框宽/模板框宽。"""


def box_truth(box, frame_offset, tag: str):
    """scale=1 时**逐字**走 H12 那一把尺子（旧读数可比）；scale≠1 时做多尺度搜索。

    为什么不能像改动前那样直接相减：`H12.box_truth` 按模板原尺寸相关，分数缩放下屏幕上
    的框是 `edge×SC` 宽 ⇒ 峰值掉到在位闸以下，报出来的是"量具断"这种假红（#75 登记的
    正是这件事：先加换算再跑）。
    """
    if not (SC > 1.0 + 1e-9):
        t = H12.box_truth(box, frame_offset, tag)
        if t is None:
            return None
        return Truth(t[0], t[1], t[2], 1.0, box.shape[0], box.shape[0])
    shot = A10.capture(tag)
    if shot is None:
        return None
    corr = A10.Correlator(H12.luma(shot))
    src = Image.fromarray(np.ascontiguousarray(box[..., :3]), "RGB")
    edge0 = int(box.shape[0])
    limit = min(shot.shape[0], shot.shape[1])

    def probe(f: float):
        edge = int(round(edge0 * f))
        if edge < 8 or edge > limit:
            return None
        tmpl = A10._luma(np.asarray(src.resize((edge, edge), Image.BILINEAR), dtype=np.uint8))
        peak, x, y = corr.peak(tmpl)
        # 中心换算：屏幕框心 − 盒心在**缩放后帧内**的偏移（不乘 f 就还是 scale=1 的算法）
        return (peak, x + edge / 2.0 - frame_offset[0] * f,
                y + edge / 2.0 - frame_offset[1] * f, edge)

    grid = sorted({round(v, 2) for v in np.arange(0.6, 3.21, 0.1)}
                  | {1.0, round(SC, 2), round(DPR, 2), round(SC * DPR, 2)})
    best = None
    for f in grid:
        r = probe(f)
        if r is not None and (best is None or r[0] > best[1][0]):
            best = (f, r)
    if best is None:
        return None
    f0 = best[0]
    for f in sorted({round(v, 3) for v in np.arange(f0 - 0.06, f0 + 0.061, 0.005)}):
        if f in grid or f < 0.2:
            continue
        r = probe(f)
        if r is not None and r[0] > best[1][0]:
            best = (f, r)
    f, (peak, cx, cy, edge) = best
    return Truth(cx, cy, peak, f, edge, edge)


def ruler_floor_px(box_edge: float) -> float:
    """拟合因子每错一个细化步长（0.005）时中心的漂移量 —— **本档下这把尺子的下限**。"""
    if not (SC > 1.0 + 1e-9):
        return 0.5                       # 单尺度相关：argmax 是整像素，±0.5 px
    return box_edge * 0.005 / 2.0 / max(SC, 1e-9)


def resid(t: Truth, rect) -> tuple[float, float, float, float]:
    """(逻辑残差 dx,dy, 物理残差 dx,dy)。rect = 产品自认的 `(x,y,w,h)`（niri 逻辑口径）。"""
    want_log = (rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0)
    got_log = (t.cx / SC, t.cy / SC)
    d = (got_log[0] - want_log[0], got_log[1] - want_log[1])
    return (d[0], d[1], d[0] * SC, d[1] * SC)


def resid_txt(t: Truth, rect) -> str:
    dx, dy, px, py = resid(t, rect)
    return (f"信念 {tuple(int(v) for v in rect)} ⇒ 逻辑中心 ({rect[0] + rect[2] / 2.0:.2f},"
            f"{rect[1] + rect[3] / 2.0:.2f}) vs 真值逻辑 ({t.cx / SC:.2f},{t.cy / SC:.2f})"
            f"　残差 ({dx:+.2f},{dy:+.2f}) 逻辑 px = ({px:+.2f},{py:+.2f}) 物理 px"
            f"｜峰={t.peak:.4f} 因子={t.factor:g}")


def fit_blocked(host) -> bool:
    """产品这一档**没放行**：回读轮救不回来 ⇒ 气泡是"屏幕放不下"，fidus 整场没启动。"""
    return any(DIM_BUBBLE in b for b in getattr(host, "bubbles", []))


def why_blocked(host) -> str:
    backend = getattr(host, "_layer_backend", None)
    cfg = None if backend is None else backend.logical_size()
    err = ""
    if backend is not None and callable(getattr(backend, "last_error", None)):
        err = backend.last_error() or ""
    return (f"configure={cfg}｜桥接层诊断={err or '（空）'}｜气泡 {host.bubbles}")



class WiringHost(QWidget, PetRenderHostMixin):
    """被测对象：`PetRenderHostMixin` 的挂载/定位/套用三件全是产品原文，
    这里只补它外围依赖（config、气泡、落盘）并把两个动作变成可数的记录器。
    """

    def __init__(self, cfg):
        super().__init__()
        self.config = cfg
        self.bubbles: list[str] = []
        self.saves = 0
        self.places: list[tuple[int, int, bool]] = []
        self.on_place = None            # A4 用：第一次摆位时换帧
        # #78 的回读轮自己会挪一发（`_layer_fit_move`）并改写信念（`_layer_fit_proxy_rect`），
        # 那既不是 fidus 动的、也不进 `places`。A0/A2 的零点必须知道它，否则"产品没乱动"
        # 会被判成"产品动了却没记账"。只记，不改行为。
        self.fit_moves: list[tuple[int, int]] = []
        self.fit_proxy: tuple[int, int, int, int] | None = None
        self.fit_moves_all: list[tuple[int, int]] = []   # 不被 clear_moves() 清掉的全场账

    def _show_bubble(self, text, duration_ms=None, mood=None):
        self.bubbles.append(str(text))

    def _save_config(self):
        self.saves += 1

    def _fidus_place(self, x: int, y: int) -> bool:
        ok = super()._fidus_place(x, y)
        self.places.append((int(x), int(y), bool(ok)))
        if self.on_place is not None:
            hook, self.on_place = self.on_place, None
            hook()
        return ok

    def _layer_fit_move(self, x: int, y: int) -> bool:
        ok = super()._layer_fit_move(x, y)
        if ok:
            self.fit_moves.append((int(x), int(y)))
            self.fit_moves_all.append((int(x), int(y)))
        return ok

    def _layer_fit_proxy_rect(self, x, y, w, h, usable_h) -> None:
        super()._layer_fit_proxy_rect(x, y, w, h, usable_h)
        widget = getattr(self, "sprite_label", None)
        self.fit_proxy = None if widget is None else rect_of(widget._proxy_rect)

    def clear_moves(self) -> None:
        """一臂开跑前把两本账清空（`places` 与回读轮的账本必须同批清，否则差一轮）。"""
        self.places.clear()
        self.bubbles.clear()
        self.fit_moves.clear()
        self.fit_proxy = None

    def fit_baseline(self, mount_rect) -> tuple[int, int, int, int]:
        """本臂的**零点**：产品自己最后认下、且发生在 fidus 之前的那个矩形。

        回读轮写过信念就以它为准（含保留带厚度，由产品算，探针不重推）；没写过就是
        挂载请求本身 —— scale=1 那一档回读一轮都不用挪，于是这条与 #78 之前的旧判据
        （"`_proxy_rect` 该等于挂载矩形"）逐字同值，旧读数照样可比。
        """
        return self.fit_proxy if self.fit_proxy is not None else tuple(mount_rect)


def niri_layers() -> list[dict]:
    """`niri msg -j layers`：合成器眼中的浮层清单（不经我方任何代码）。"""
    try:
        r = subprocess.run(["niri", "msg", "-j", "layers"],
                           capture_output=True, timeout=10, text=True)
        return json.loads(r.stdout) if r.returncode == 0 else []
    except Exception:
        return []


def pet_layers() -> list[dict]:
    return [d for d in niri_layers() if d.get("namespace") != WALLPAPER_NS]


def rect_of(qrect) -> tuple[int, int, int, int]:
    return (qrect.x(), qrect.y(), qrect.width(), qrect.height())


def opaque_bbox(frame: np.ndarray) -> tuple[int, int, int, int]:
    mask = frame[..., 3] > FP.ALPHA_MIN
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def mount_rect(geo_w: int, geo_h: int, fw: int, bbox, hug_right: bool) -> tuple[int, int]:
    """让不透明轮廓整体落进屏内；`hug_right` 把 surface 右缘顶到屏沿（A3 用）。"""
    bx0, by0, bx1, by1 = bbox
    y = int(round((geo_h - (by1 - by0)) / 2.0)) - by0
    if hug_right:
        return max(0, geo_w - fw), max(0, y)
    x = int(round((geo_w - (bx1 - bx0)) / 2.0)) - bx0
    return max(0, x), max(0, y)


def clip_txt(rect, bbox) -> str:
    """把"摆在这一位，桌宠轮廓哪一端在屏外"讲成数字（口径＝niri 逻辑 px）。

    存在的原因：分数缩放下"请求高"可以大于合成器配出的逻辑屏高，产品的回读轮于是把
    surface **整块往上挪**去换底部 —— 头有没有被屏顶切掉，只看轮廓顶端在帧内的内缩量
    `by0` 够不够抵掉那个负 y。不看它就只能说"这一位装不下"，而装不下与切了头是两件事。
    帧内 px 与逻辑 px 之间差的是 Qt/niri 那 1 px 分叉，本函数不承担那一份。
    """
    _x, y, _w, _h = rect
    bx0, by0, bx1, by1 = bbox
    top = max(0, -(y + by0))                       # 轮廓顶端被屏顶切掉的行数
    screen_h = NIRI_LOGICAL[1] or (_h + max(0, y))
    bottom = max(0, (y + by1) - screen_h)          # 轮廓底端被屏底切掉的行数
    if not top and not bottom:
        return "轮廓完整在屏内"
    which = []
    if top:
        which.append(f"头顶被屏上沿切掉 {top} px")
    if bottom:
        which.append(f"脚被屏下沿切掉 {bottom} px")
    return "｜".join(which) + f"（轮廓纵向 {by0}..{by1}，屏高 {screen_h}）"


def place_host(app, host, x: int, y: int, fw: int, fh: int):
    """摆 Qt 侧宿主，返回 `_layer_geometry()` 实际读到的矩形（产品的请求值就是它）。"""
    host.resize(fw, fh)
    host.sprite_label.setGeometry(0, 0, fw, fh)
    host.move(x, y)
    H4.pump(app, 350)
    return host._layer_geometry()


def run_until_settled(app, host, timeout: float = LOCATE_TIMEOUT) -> bool:
    """泵到"回读轮落定 **且** fidus 收口"两件事都完成；返回 True = 仍卡着。

    #78 之前只看 `_fidus_busy` 就够；#78 在挂载与 fidus 之间插了一段"回读 configure 尺寸
    → 必要时夹移"，fidus 排在它落定之后才启动 ⇒ 只看 `_fidus_busy` 会立刻返回 False
    （那时它还没启动），量到的是"轮还开着"的中间态。那条自戒在
    `working/fidus-switch-positioning.md` 的 #78 决策日志补，本函数是它的落实。
    """
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if getattr(host, "_fit_rect", None) is None and not getattr(host, "_fidus_busy", False):
            return False
        H4.pump(app, 200)
    return bool(getattr(host, "_fit_rect", None) is not None
                or getattr(host, "_fidus_busy", False))


def teardown(host, app) -> None:
    """每条出口（含判红的那几条）都必须走到：桌上不留浮层、不留开关面板。"""
    try:
        host._set_layer_mode(False)
        H4.pump(app, 400)
        backend = getattr(host, "_layer_backend", None)
        if backend is not None:
            backend.destroy_context()
    except Exception as exc:  # noqa: BLE001 - 收尾失败只报告，不掩掉原判据
        print(f"[teardown] layer: {type(exc).__name__}: {exc}")
    panel = getattr(host, "_layer_panel", None)
    if panel is not None:
        panel.close()
    # `host.close()` 在本机 abort（SIGABRT，栈停在 Qt 关窗里；Live2D 的 Cubism core
    # 会在那一刻重打印一遍初始化 banner）。这臂测的是定位接线，不追这条 ⇒ 先 hide()。
    # 挂账：退出路径上「用过 layer-shell + Live2D 之后关顶层窗口」是否真会崩，
    # 属 linux-compat 那条线，另做最小量具（见本轮记录）。
    host.hide()
    H4.pump(app, 300)
    FP.shutdown()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    fact("锚点", __import__("fidus").__git_commit__)
    fact("被测代码", f"{PetRenderHostMixin.__module__}.render_host + {FP.__file__}")
    sc, _line = read_units(app)
    if sc != sc:                                   # nan ⇒ 不能换算，也不许假装 scale=1
        print("VERDICT-WIRING : ✗ 读不到合成器 scale ⇒ 换算无从谈起，本件不产出主张")
        return 1
    if abs(sc - 1.0) > 1e-9:
        fact("档位", f"scale={sc} ⇒ 本跑是 **分数缩放臂**（#75）：尺子改多尺度搜索，"
                     "残差按 niri 逻辑 px 判并同印物理")

    frames, _rinfo, l_host, _l_widget = H7.H3.render_live2d_frames(app, 1)
    if not frames:
        print("VERDICT-WIRING : ✗ 活体渲染不可得 ⇒ 量具自身失效，不猜")
        return 1
    l_host.hide()
    H4.pump(app, 400)
    frame = frames[0]
    fh, fw = frame.shape[:2]
    qf = H1.qimage_from(frame)
    fact("被测帧", f"{fw}×{fh} 指纹={H7.frames_digest([frame])}")
    fact("静态化", "冻在帧 0（`render_offscreen` 反复给同一张）⇒ 只谈接线与几何；"
                   "动画相已在 H8/H9 量过，这里不重复主张")

    geo = app.primaryScreen().geometry()
    avail = app.primaryScreen().availableGeometry()
    fact("屏幕", f"geometry=({geo.x()},{geo.y()},{geo.width()}×{geo.height()}) "
                 f"availableGeometry=({avail.x()},{avail.y()},{avail.width()}×{avail.height()})")
    fact("两矩形", "本机若无 panel 则两者重合 ⇒ 本件量不到『可用区原点偏移』那一格（另见 #74）。"
                   "此处只记事实，不主张偏移为零")

    bbox = opaque_bbox(frame)
    fact("不透明轮廓", f"帧内 ({bbox[0]},{bbox[1]})-({bbox[2]},{bbox[3]}) ⇒ 顶端内缩 {bbox[1]} px、"
                       f"纵向占 {bbox[3] - bbox[1]} px（帧高 {fh}）。这一份决定"
                       f"『y 为负的夹移会不会削到头顶』，下面各臂的落点都按它换算")
    box, off = H12.probe_box(frame)
    if box is None:
        print("VERDICT-WIRING : ✗ 选不出独立量具盒 ⇒ 量具自身失效，不猜")
        return 1
    fact("量具盒", f"边长 {box.shape[0]} px（帧内）⇒ 本档落点下限约 "
                   f"{ruler_floor_px(box.shape[0]):.2f} 逻辑 px"
                   + ("（多尺度拟合因子的杠杆，不是产品的误差）" if SC > 1.0 + 1e-9 else ""))

    from meapet.config.store import load_config
    cfg = load_config()
    cfg.setdefault("fidus", {})["enabled"] = False      # 产品默认值，不靠运气

    host = WiringHost(cfg)
    label = QLabel(host)
    label.render_offscreen = lambda: qf                 # type: ignore[attr-defined]
    host.sprite_label = label                           # type: ignore[attr-defined]
    host.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    host.setAttribute(Qt.WA_QuitOnClose, False)
    host.show()
    H4.pump(app, 600)

    x0, y0 = mount_rect(geo.width(), geo.height(), fw, bbox, hug_right=False)
    geom0 = place_host(app, host, x0, y0, fw, fh)
    fact("A0 挂载", f"请求位置 ({x0},{y0}) ⇒ `_layer_geometry()` 读到 {geom0}"
                    f"（Qt 侧实际位 ({host.x()},{host.y()}) size ({host.width()},{host.height()}))")

    # ───────────────────────────────────────────────────────── A0 默认关
    print("\n=== A0 · fidus 默认关：走一次完整挂载 ===")
    t0 = time.perf_counter()
    host._init_layer_overlay_mode()
    mount_secs_a0 = time.perf_counter() - t0
    H4.pump(app, 1500)
    layers_a0 = pet_layers()
    if fit_blocked(host):
        # 产品自己已经出声了：这一档根本挂不出桌宠。这不是量具断，也不许继续往下
        # 假装四臂都跑过 —— 后面每一臂都标"不判（未放行）"，红只记在这一条上。
        print(f"VERDICT-DIM : RED scale={SC} 下请求 {fw}×{fh}（Qt 逻辑）"
              f"合成器不放行 ⇒ {why_blocked(host)}")
        print("VERDICT-WIRING : 不判（未放行）——A0..A4 全停：fidus 整场没启动，"
              "落点这件事在这一档上无从量。合成器配出的逻辑屏尺寸见上")
        teardown(host, app)
        return 2
    t_mount = box_truth(box, off, "h15_a0")
    if t_mount is None or t_mount[2] < H12.PRESENT_PEAK:
        peak = "-" if t_mount is None else f"{t_mount[2]:.4f}"
        print(f"VERDICT-VISIBLE : ✗ 独立尺子峰 {peak} < {H12.PRESENT_PEAK} ⇒ 我方内容不在屏上，"
              "全案不产出读数"
              + (f"｜{why_blocked(host)}" if t_mount is not None else ""))
        teardown(host, app)
        return 1
    base_a0 = host.fit_baseline(geom0)
    dx0, dy0, px0, py0 = resid(t_mount, base_a0)
    fact("A0 真值", f"surface 中心 物理 ({t_mount[0]:.2f},{t_mount[1]:.2f}) "
                    f"峰={t_mount[2]:.4f} 因子={t_mount.factor:g}　{resid_txt(t_mount, base_a0)}"
                    f" ⇒ 信念误差 ({dx0:+.2f},{dy0:+.2f}) 逻辑 px")
    fit_a0 = host.fit_moves[-1] if host.fit_moves else None      # 汇总行还要读
    if fit_a0 is not None:
        # 2026-09-27 补：#78 的回读轮在挂载与 fidus 之间挪了桌面，"挂载请求"不再是零点。
        # 拿它相减会得到一个恰好等于夹移量的 y 残差（本档 −102 px），那是产品按模型的正常
        # 动作，不是信念误差 —— 旧版这里印的就是这个数，读数口径改了，值没改。
        dy_move = fit_a0[1] - geom0[1]
        fact("A0 回读轮", f"夹移 {geom0[:2]}→{fit_a0}（y {dy_move:+d} px）"
                          f"⇒ 若仍按挂载请求 {tuple(geom0)} 相减，y 残差会平白多出这 {dy_move:+d} px"
                          f"｜摆位后 {clip_txt(base_a0, bbox)}")
    a0_ok = (not getattr(host, "_fidus_busy", False) and host.places == []
             and not any("定位" in b for b in host.bubbles))
    print(f"VERDICT-A0 : {'PASS' if a0_ok else 'FAIL'} 默认关 ⇒ 摆位 {len(host.places)} 次、"
          f"气泡 {host.bubbles or '无'}、挂载 {mount_secs_a0 * 1000:.0f} ms"
          f"｜合成器侧非壁纸浮层 {len(layers_a0)} 只")

    # ───────────────────────────────────────────────────────── A1 打开后切一次
    print("\n=== A1 · 打开 fidus 后走一次切换 ===")
    host._toggle_fidus_enabled()
    fact("A1 开关", f"`_toggle_fidus_enabled()` ⇒ enabled="
                    f"{bool((host.config.get('fidus') or {}).get('enabled'))}"
                    f"、`_save_config` 调用 {host.saves} 次（探针里换成计数器，不落盘）")

    host._set_layer_mode(False)
    H4.pump(app, 800)
    geom_pre = place_host(app, host, x0, y0, fw, fh)
    same = tuple(geom_pre) == tuple(geom0)
    fact("A1 重挂", f"交互→穿透来回一次后 `_layer_geometry()` = {geom_pre} ⇒ "
                    f"{'与 A0 同一矩形' if same else '矩形变了：A0 真值只作参考，本臂改判事后重合'}")

    host.clear_moves()
    t_call = time.perf_counter()
    host._set_layer_mode(True)
    mount_secs = time.perf_counter() - t_call
    mid_layers = pet_layers()
    busy_mid = bool(getattr(host, "_fidus_busy", False))
    # #78 之后 `_set_layer_mode(True)` 不再直接放 fidus，而是先开一轮回读；fidus 排在它
    # 落定之后才启动 ⇒ "返回时 fidus 在跑"这条**证据**失效了（原判据想说的不是"fidus 一定
    # 在跑"，而是"挂载没有同步等测量做完"）。改读成"返回时仍有活没干完"：回读轮开着也算。
    # 两个原始事实都印出来，不合并 —— 见 working/fidus-switch-positioning.md 的 #78 决策日志补。
    pending_mid = getattr(host, "_fit_rect", None) is not None
    fact("A1 切换即时性", f"`_set_layer_mode(True)` 返回用 {mount_secs * 1000:.0f} ms，"
                          f"返回时 fidus 在跑={busy_mid}、回读轮开着={pending_mid}、"
                          f"合成器已有 {len(mid_layers)} 只浮层（挂载不等测量）")
    stuck = run_until_settled(app, host)
    secs = time.perf_counter() - t_call
    if stuck:
        print(f"VERDICT-A1 : ✗ {LOCATE_TIMEOUT:.0f} s 仍未收口 ⇒ 接线卡死，本臂不判精度")
        teardown(host, app)
        return 2
    if fit_blocked(host):
        print(f"VERDICT-DIM : RED scale={SC} 下这一次切换没放行 ⇒ {why_blocked(host)}")
        print("VERDICT-WIRING : 不判（未放行）——fidus 整场没启动，落点这件事在这一档上无从量")
        teardown(host, app)
        return 2
    applied = tuple(getattr(host, "_fidus_surface", geom_pre))
    proxy_a1 = rect_of(host.sprite_label._proxy_rect)
    H4.pump(app, 500)
    t_final = box_truth(box, off, "h15_a1")
    print(f"\n--- A1 收尾：全程 {secs:.1f} s　摆位序列 {host.places}　气泡 {host.bubbles} ---")
    if t_final is None or t_final[2] < H12.PRESENT_PEAK:
        peak = "-" if t_final is None else f"{t_final[2]:.4f}"
        print(f"VERDICT-A1 : ✗ 事后独立尺子看不见（峰 {peak}）⇒ 量具断，不判精度"
              f"｜{why_blocked(host)}")
        teardown(host, app)
        return 1
    errf = resid(t_final, applied)[:2]
    # 2026-09-27 补：跳位的零点是**回读轮落定后的挂载矩形**，不是 #78 之前的原始请求。
    # 夹移是产品在测量开始前就发出的那一发，拿它当"fidus 把桌宠挪走了"会把 102 px 记到
    # 定位头上（本档实测 A1-JUMP 曾因此印 (0,-102)，而校正本身只动了 0 px）。
    base_a1 = host.fit_baseline(geom_pre)
    jump = (applied[0] - base_a1[0], applied[1] - base_a1[1])
    proxy_same = proxy_a1 == applied
    immediate = mount_secs * 1000 < MOUNT_MS_MAX and (busy_mid or pending_mid)
    a1_ok = immediate and max(abs(errf[0]), abs(errf[1])) <= TOL_PX and proxy_same
    if host.fit_moves:
        fact("A1 回读轮", f"夹移 {geom_pre[:2]}→{host.fit_moves[-1]} ⇒ 跳位按夹移后的 "
                          f"{tuple(base_a1)} 算　｜校正落点 {clip_txt(applied, bbox)}")
    print(f"VERDICT-A1 : {'PASS' if a1_ok else 'FAIL'} 校正后 {resid_txt(t_final, applied)}"
          f" ⇒ 逻辑残差 ≤ {TOL_PX}"
          f"｜全程 {secs:.1f} s（含每会话一次的校准）")
    print(f"VERDICT-A1-JUMP : 跳位 ({jump[0]:+.0f},{jump[1]:+.0f}) px（零点=回读轮落定后的 "
          f"{base_a1[:2]}，夹移 {host.fit_moves or '无'} 已扣除）—— 本机 niri 照请求摆位"
          " ⇒ 期望 ≈0；几十像素即接线吃了错坐标")
    print(f"VERDICT-A1-PROXY : `_proxy_rect`={proxy_a1} vs 实际摆下 {applied} ⇒ "
          f"{'同一个数' if proxy_same else '分叉'}")
    print(f"VERDICT-A1-MOUNT : 切换 {mount_secs * 1000:.0f} ms < {MOUNT_MS_MAX} ms 且返回时"
          f"有活没干完（fidus 在跑={busy_mid}／回读轮开着={pending_mid}）"
          f" ⇒ 立即性{'成立' if immediate else '不成立'}")

    # ───────────────────────────────────────────────────────── A2 位移之前就退
    print("\n=== A2 · 退回（候选全拒，发生在探针位移之前）===")
    blank = np.zeros((fh, fw, 4), dtype=np.uint8)
    blank[int(bbox[1]):int(bbox[3]), int(bbox[0]):int(bbox[2]), 3] = 255   # 只留轮廓，不留纹理
    blank[120:200, 120:200, :3] = 7                                  # 一块纯色：相关有能量，结构无
    qb = H1.qimage_from(np.ascontiguousarray(blank))
    label.render_offscreen = lambda: qb                               # type: ignore[attr-defined]
    host._set_layer_mode(False)
    H4.pump(app, 600)
    geom_a2 = place_host(app, host, x0, y0, fw, fh)
    H4.pump(app, 900)
    host.clear_moves()
    host._set_layer_mode(True)
    stuck = run_until_settled(app, host)
    proxy_a2 = rect_of(host.sprite_label._proxy_rect)
    base_a2 = host.fit_baseline(geom_a2)
    fell_back = any("没能量准" in b for b in host.bubbles)
    if fit_blocked(host):
        a2_ok = None
        print(f"VERDICT-A2 : 不判（未放行）⇒ {why_blocked(host)}")
    else:
        a2_ok = (not stuck) and host.places == [] and fell_back and proxy_a2 == base_a2
        print(f"VERDICT-A2 : {'PASS' if a2_ok else 'FAIL'} 摆位 {len(host.places)} 次、"
              f"气泡 {host.bubbles}、`_proxy_rect`={proxy_a2} vs 零点 {base_a2}")
    # 2026-09-27 判据过期修正：旧版比的是 `proxy_a2 == geom_a2`（挂载请求）。#78 之后
    # 回读轮合法地动过桌面**并**改写过信念（本档 (212,0)→(212,-102)），那条比法把产品的
    # 正常夹移读成"fidus 测不到还乱动"，在 1.5 档必然报红。改比"fidus 收口后的信念 ==
    # 回读轮落定那一刻的信念"——夹移算进零点， fidus 那一半仍然是零位移。
    fact("A2 零点", f"挂载请求 {tuple(geom_a2)}｜回读轮夹移 {host.fit_moves or '无'}"
                    f"（信念写成 {host.fit_proxy}）⇒ 期望 `_proxy_rect`={base_a2}"
                    f"　本臂不给独立尺子读数：画面被换成只有轮廓、无纹理的一帧，"
                    f"量具盒本就找不到")
    fact("A2 说明", "位移之前就没候选 ⇒ 一次 `set_position` 都不该有。这条若红，"
                    "就是产品在'测不到'时仍然动了桌宠")

    # ───────────────────────────────────────────────────────── A3 位移之后才退
    print("\n=== A3 · 探针位移撞上屏右缘（`locate` 位移后的出口都不回摆）===")
    label.render_offscreen = lambda: qf                                # type: ignore[attr-defined]
    x3, y3 = mount_rect(geo.width(), geo.height(), fw, bbox, hug_right=True)
    # 阶段一：同一几何、fidus 关着挂一次 ⇒ 拿到**未被探针扰动**的挂载真值。
    # 第二稿曾直接在没有浮层的时候量这一步（`_set_layer_mode(False)` 之后 surface 已销毁），
    # 独立尺子当然看不见 ⇒ 那一版这条臂没产出任何数字。
    host._toggle_fidus_enabled()          # 关
    host._set_layer_mode(False)
    H4.pump(app, 600)
    geom_ref = place_host(app, host, x3, y3, fw, fh)
    host._set_layer_mode(True)
    H4.pump(app, 1500)
    t_ref = box_truth(box, off, "h15_a3_ref")
    if t_ref is None or t_ref[2] < H12.PRESENT_PEAK:
        # 这一位与 A0 同尺寸、不同 margin ⇒ 看不见更可能是"这一位被夹了没放行"，
        # 把 configure 与气泡一起端出来给人判，不改判据（气泡此时可能带前面的旧文案）。
        print(f"VERDICT-A3 : ✗ 该位独立尺子看不见 ⇒ 量具断，本臂不判｜{why_blocked(host)}")
        teardown(host, app)
        return 1
    margin = geo.width() - (geom_ref[0] + fw)
    will_hit = margin < FP.MOVE_PX
    fact("A3 阶段一", f"关着挂载 请求 {geom_ref} ⇒ {resid_txt(t_ref, geom_ref)}")
    fact("A3 余量", f"右缘余量 {margin:.0f} px，探针位移 {FP.MOVE_PX:.0f} px ⇒ "
                    f"{'位移会越出屏右缘' if will_hit else '位移仍在屏内（本臂前提不成立）'}")

    # 阶段二：同几何、fidus 开着再切一次；Qt 给的位置可能抖 1 px，用位移量补掉。
    host._toggle_fidus_enabled()          # 开
    host._set_layer_mode(False)
    H4.pump(app, 600)
    geom_a3 = place_host(app, host, x3, y3, fw, fh)
    shift = (geom_a3[0] - geom_ref[0], geom_a3[1] - geom_ref[1])
    fact("A3 阶段二", f"开着挂载 请求 {geom_a3}（与阶段一差 ({shift[0]:+.0f},{shift[1]:+.0f}) px，"
                      f"已从漂移里扣除）")
    host.places.clear()
    host.bubbles.clear()
    host._set_layer_mode(True)
    stuck = run_until_settled(app, host)
    applied_a3 = tuple(getattr(host, "_fidus_surface", geom_a3))
    proxy_a3 = rect_of(host.sprite_label._proxy_rect)
    H4.pump(app, 500)
    t_end = box_truth(box, off, "h15_a3_end")
    if stuck or t_end is None or t_end[2] < H12.PRESENT_PEAK:
        print(f"VERDICT-A3 : ✗ {'卡死' if stuck else '事后看不见'} ⇒ 量具断")
        teardown(host, app)
        return 1
    # `shift` 是 Qt 逻辑口径的请求差，`t_ref` 是物理读数 ⇒ 分数缩放下两者不能直接相加，
    # 统一换到**逻辑**口径再比（÷SC 只引入 0.33 px 舍入，见头注）。
    expect = (t_ref[0] / SC + shift[0], t_ref[1] / SC + shift[1])   # 合成器照请求摆 ⇒ 该停在这
    drift = (t_end[0] / SC - expect[0], t_end[1] / SC - expect[1])
    worst = max(abs(drift[0]), abs(drift[1]))
    fell_back_a3 = any("没能量准" in b for b in host.bubbles)
    a3_stranded = worst > TOL_PX
    stranded_txt = f"桌宠停在离挂载位 {worst:.2f} 逻辑 px 处" if a3_stranded else "回到挂载位"
    print(f"VERDICT-A3 : 出口={'退回' if fell_back_a3 else '有值'}　摆位序列 {host.places}")
    print(f"VERDICT-A3-STRAND : 收尾真值逻辑中心 ({t_end[0] / SC:.2f},{t_end[1] / SC:.2f}) vs "
          f"应停位 ({expect[0]:.2f},{expect[1]:.2f}) ⇒ 净漂移 "
          f"({drift[0]:+.2f},{drift[1]:+.2f}) 逻辑 px"
          f"（=({drift[0] * SC:+.2f},{drift[1] * SC:+.2f}) 物理 px）⇒ {stranded_txt}")
    print(f"VERDICT-A3-CLAIM : 气泡 {host.bubbles}｜`_proxy_rect`={proxy_a3} vs "
          f"产品自认的位置 {applied_a3}")
    if fell_back_a3 and a3_stranded:
        a3_note = "退回且漂走 ⇒『沿用原来的位置』这句与屏幕不符"
    elif fell_back_a3:
        a3_note = "退回且仍在挂载位 ⇒ 退回路径没有副作用"
    elif a3_stranded:
        a3_note = "有值但收尾漂走 ⇒ 校正本身落在了错位置"
    else:
        a3_note = "有值且与请求重合 ⇒ 正常闭合"
    print(f"VERDICT-A3-OUTCOME : {a3_note}")

    # ───────────────────────────────────────────────────── A4 位移中途换画面
    print("\n=== A4 · 第一次摆位落地时换掉画面（逼出 `locate` 位移后的退回出口）===")
    label.render_offscreen = lambda: qf                      # type: ignore[attr-defined]
    rolled = frame.copy()
    sub = rolled[bbox[1]:bbox[3], bbox[0]:bbox[2], :3]
    rolled[bbox[1]:bbox[3], bbox[0]:bbox[2], :3] = np.roll(sub, ROLL_PX, axis=1)
    r_box, r_off = H12.probe_box(rolled)
    a4_ok = None
    if r_box is None:
        print("VERDICT-A4 : ✗ 换帧后选不出独立盒 ⇒ 量具断，本臂不判")
    else:
        host._set_layer_mode(False)
        H4.pump(app, 600)
        place_host(app, host, x0, y0, fw, fh)
        qrolled = H1.qimage_from(np.ascontiguousarray(rolled))
        host.on_place = lambda: setattr(                       # type: ignore[attr-defined]
            label, "render_offscreen", lambda: qrolled)        # type: ignore[attr-defined]
        host.places.clear()
        host.bubbles.clear()
        host._set_layer_mode(True)
        stuck = run_until_settled(app, host)
        H4.pump(app, 500)
        mount_a4 = tuple(getattr(host, "_fidus_mount", ()))
        t_a4 = None if fit_blocked(host) else box_truth(r_box, r_off, "h15_a4_end")
        if fit_blocked(host):
            print(f"VERDICT-A4 : 不判（未放行）⇒ {why_blocked(host)}")
        elif stuck or t_a4 is None or t_a4[2] < H12.PRESENT_PEAK or not mount_a4:
            print(f"VERDICT-A4 : ✗ {'卡死' if stuck else '独立尺子看不见换帧后的内容'} ⇒ 量具断")
        else:
            fell_a4 = any("没能量准" in b for b in host.bubbles)
            d = resid(t_a4, mount_a4)[:2]
            restored = max(abs(d[0]), abs(d[1])) <= TOL_PX
            proxy_a4 = rect_of(host.sprite_label._proxy_rect)
            last = host.places[-1][:2] if host.places else None
            fact("A4 换帧", f"轮廓不变、纹理在 bbox 内平移 {ROLL_PX} px ⇒ 期望闭环判失败"
                            f"　摆位序列 {host.places}")
            print(f"VERDICT-A4 : 出口={'退回' if fell_a4 else '有值'}｜{resid_txt(t_a4, mount_a4)}"
                  f" ⇒ 净漂移 ({d[0]:+.2f},{d[1]:+.2f}) 逻辑 px")
            print(f"VERDICT-A4-RESTORE : 最后一发摆位请求 {last} vs 挂载 "
                  f"{mount_a4[:2]}｜`_proxy_rect`={proxy_a4} vs {mount_a4}")
            if not fell_a4:
                a4_ok = None
                print("VERDICT-A4 : 前提没成立 —— 换画面没能判退闭环 ⇒ 本臂只出事实，"
                      "不回摆这件事没被触发（不等于路径安全）")
            else:
                a4_ok = restored and last == mount_a4[:2] and proxy_a4 == mount_a4
                print(f"VERDICT-A4 : {'PASS' if a4_ok else 'FAIL'} 退回路径把探针位移"
                      f"（含换帧带来的 {ROLL_PX} px 假位移）要回了挂载位，"
                      f"且 `_proxy_rect` 与屏幕同一个数")

    teardown(host, app)

    print("\n=== 汇总 ===")
    a2_txt = "不判（未放行）" if a2_ok is None else ("PASS" if a2_ok else "FAIL")
    a4_txt = "不判（前提未成立或未放行）" if a4_ok is None else ("PASS" if a4_ok else "FAIL")
    print(f"VERDICT-WIRING : A0={'PASS' if a0_ok else 'FAIL'} A1={'PASS' if a1_ok else 'FAIL'} "
          f"A2={a2_txt} A4={a4_txt}｜A3 净漂移 {worst:.2f} 逻辑 px、出口="
          f"{'退回' if fell_back_a3 else '有值'}（{a3_note}）—— A3 只出事实，不计入红绿")
    print(f"VERDICT-UNITS : scale={SC} dpr={DPR} niri 逻辑 {NIRI_LOGICAL[0]}×{NIRI_LOGICAL[1]}　"
          f"残差口径＝niri 逻辑 px；本档量具下限 ≈{ruler_floor_px(box.shape[0]):.2f} px")
    # 这一档"桌宠到底装不装得下"是 #75 顺带量到的产品事实，与精度无关，单独一条不埋在臂里。
    print(f"VERDICT-FIT : scale={SC} 请求 {fw}×{fh}（Qt 逻辑） vs niri 逻辑 "
          f"{NIRI_LOGICAL[0]}×{NIRI_LOGICAL[1]} ⇒ 全场回读轮夹移 {len(host.fit_moves_all)} 次"
          + (f"（A0 那一次 {geom0[:2]}→{fit_a0}）" if fit_a0 else "")
          + f"｜挂载位 {base_a0} 处 {clip_txt(base_a0, bbox)}")
    reds = [not a0_ok, not a1_ok, a2_ok is False, a4_ok is False]
    return 2 if any(reds) else 0


if __name__ == "__main__":
    sys.exit(main())
