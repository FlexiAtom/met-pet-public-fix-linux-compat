"""Wayland 上『窗口透明』到底成不成立——光栅对照组 + 产品属性满配的 Live2D 窗口。

出处：H3 真机段发现探针的 shim 窗口（按产品设置了 alpha+stencil 缓冲 +
WA_TranslucentBackground）在屏上是一整块不透明浅蓝矩形。要么这是 Qt/Wayland 的通用
限制，要么是「有 QOpenGLWidget 时失效」，要么是探针自己的属性缺项——三种结论的动作
完全不同，所以先分开量，不靠猜。

为什么一次 Live2D 就够：把**产品 PetWindow 的全套属性**（含 `WA_AlwaysStackOnTop` 与
`_NET_WM_WINDOW_TYPE_UTILITY`，app.py:250-265）搬到探针窗口上，透明即"探针少设了属性"，
不透明即"产品自己就这样"——一次上屏就能把两种读法分开，不必再做属性 A/B。

为什么不用合成的 QOpenGLWidget 做对照：本机实测三种画法都走不通（裸 libGL 在
paintGL 里 SIGABRT、`QOpenGLWidget.painter()` 与 `QOpenGLFunctions` 在本轮子里都没
暴露）⇒ 与其造一个不像产品的 GL 窗口，不如直接用**产品自己的** `Live2DWidget`。
分辨力由光栅对照组给：它透明而 Live2D 不透明，才能说"失效专属 GL 那条路"。

量法（每一步都对应一条本机证伪掉的岔路）：
1. 窗口必须**浮动**——平铺下 show() 会重排整个桌面，差值铺满全屏。
2. 背景参照**不能靠 hide()**——实测 hide→show 会让终端重新排版一次，51 万像素变化
   当场冲垮判读。改成"窗口全程在屏上，只挪位置"。
3. 挪位置不能靠 Qt 的 top-level `move()`——Wayland 上不采纳（实测挪 800px 屏幕零
   变化），只有合成器自己的 `move-floating-window` 有效。
4. 取帧时窗口必须**失焦**——niri 默认把焦点环画成"窗口底下的实心矩形"
   （config.kdl:170-178 自述："they will show up through semitransparent windows"），
   于是透明区显示的是 `active-color #7fc8ff` 而不是桌面。聚焦态量出来的"不透明"
   是环，不是客户端不给 alpha。
⇒ 于是三帧各司其职：F(聚焦@P) 与 A(失焦@P) 之差 = 焦点环本体；A 与 B(失焦@P+dx) 之差
   = 窗口自己画在屏幕上的形状。这个形状是整块矩形 ⇒ 不透明；只有内容 ⇒ 透明成立。
   窗口在哪、背景是什么，都不进判据。

只读量具：开两个小窗、grim 截屏、挪两次窗口、切两次焦点（结束时交还原先聚焦的窗口），
不写配置、不 commit。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))           # 复用 A10 / H3 两个已自证的量具
sys.path.insert(0, str(REPO_ROOT))      # 直载 meapet 包（用产品真身，不造替身）

os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

import probe_a10_geometry as A10  # noqa: E402
import probe_h3_targetability as H3  # noqa: E402  复用 _niri/_focused_is_pet/焦点与环样式那套

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QBrush, QColor, QPainter, QSurfaceFormat  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

TITLE = "probe_wd_translucency"
DELTA = 12        # 两帧之间差这么多才算"被窗口改过"（抗 dither 与动画残影）
DUMP = Path(os.environ.get("WD_DUMP", "/tmp/wddump"))


def fact(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


class RasterDot(QWidget):
    """对照组：只有 Qt 光栅路径，画一个不透明红圆，其余不画（红圆占窗口 28%）。"""

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QBrush(QColor(255, 0, 0)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(40, 40, 120, 120)


def pump(app, ms: int) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.002)


def float_self(app, host, S: int) -> tuple[bool, dict]:
    """把**自己的**窗口浮动起来；先确认聚焦的就是自己，否则不去动别的窗口。"""
    host.activateWindow()
    host.raise_()
    pump(app, 600)
    before = H3._focused_is_pet(S, S)
    if TITLE not in str(before.get("title") or ""):
        return False, {"浮动前聚焦": before,
                       "原因": "聚焦的不是本探针窗口，不敢对别的窗口请求浮动"}
    fact("请求浮动窗口", H3._niri("toggle-window-floating"))
    pump(app, 500)
    host.resize(S, S)        # 浮动只解除参与平铺，不还原被平铺强加的尺寸
    pump(app, 900)           # niri 默认开窗口动画，不等够会把动画当成"窗口长这样"
    after = H3._focused_is_pet(S, S)
    return (bool(after.get("floating"))
            and tuple(after.get("window_size") or ()) == (S, S)), {"浮动前": before, "浮动后": after}


def float_win_id() -> tuple[object, dict]:
    """本探针那个**浮动中**的窗口 id（`move-floating-window` 按 id 点名，不赌聚焦是谁）。"""
    try:
        raw = subprocess.run(["niri", "msg", "--json", "windows"],
                             capture_output=True, text=True, timeout=6).stdout
        wins = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"{type(exc).__name__}: {exc}"}
    mine = [{k: w.get(k) for k in ("id", "title", "is_floating")}
            for w in wins if TITLE in str(w.get("title") or "")]
    for w in wins:
        if TITLE in str(w.get("title") or "") and w.get("is_floating"):
            return w["id"], {"命中": w["id"]}
    return None, {"命中": None, "候选": mine}


def niri_move(win_id, dx: int, dy: int) -> str:
    try:
        r = subprocess.run(
            ["niri", "msg", "action", "move-floating-window", "--id", str(win_id),
             "--x", f"{dx:+d}", "--y", f"{dy:+d}"],
            capture_output=True, text=True, timeout=6)
        return (r.stdout + r.stderr).strip()[:120] or "ok"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


# 聚焦/取焦点/读焦点环样式都复用 H3 里那份（同一个合成器、同一套 niri 调用，不重复实现）：
#   H3._focused_win_id()  H3._focus_window(id)  H3._is_focused(id)  H3._ring_style()


def ring_readout(focused, defocused, S: int, rgb: tuple[int, int, int]) -> dict:
    """聚焦帧 vs 失焦帧里"环底色像素"的数量与范围。

    不看两帧之差：窗口失焦的同时**别的窗口拿到了焦点环**，差值法量到的是别人。
    只数"这个颜色"在两帧里各有多少像素——它在配置里就叫 active-color，
    而 config.kdl:170-178 自述这条环是"画在窗口底下的实心矩形、会透过半透明窗口显示"。
    """
    def cnt(arr):
        m = (np.abs(arr[..., :3].astype(int) - np.array(rgb)) <= 6).all(axis=2)
        n = int(m.sum())
        if not n:
            return {"px": 0}
        ys, xs = np.nonzero(m)
        return {"px": n, "bbox": [int(xs.min()), int(ys.min()),
                                  int(xs.max()) - int(xs.min()) + 1,
                                  int(ys.max()) - int(ys.min()) + 1]}
    return {"环底色": list(rgb), "聚焦帧": cnt(focused), "失焦帧": cnt(defocused),
            "窗口面积": S * S}


def rect_from_ring(ring: dict | None, S: int, ring_w: int | None) -> tuple[int, int, int, int] | None:
    """把焦点环的外接框还原成窗口矩形——这是本机唯一能给浮窗定绝对坐标的途径。

    niri `msg --json windows` 对浮窗不给绝对坐标（只有 tile_pos + window_offset），
    但环是**贴着窗口**画的实心矩形：环框 = 窗口外扩 width。于是窗口 = 环框内缩 width，
    且 (环框边长 − S)/2 必须等于配置的 width，对不上就放弃这条推断，不硬用。
    """
    bbox = (ring or {}).get("聚焦帧", {}).get("bbox")
    if not bbox:
        return None
    x, y, w, h = bbox
    inset_x, inset_y = (w - S) // 2, (h - S) // 2
    if inset_x != inset_y or (ring_w is not None and inset_x != ring_w):
        fact("环框→窗口矩形", f"放弃：内缩量 x/y = {inset_x}/{inset_y}，配置 width = {ring_w}，不自洽")
        return None
    rect = (x + inset_x, y + inset_y, w - 2 * inset_x, h - 2 * inset_y)
    fact("环框→窗口矩形", f"{list(rect)}（环框 {bbox} 内缩 {inset_x}px）")
    return rect


def _boxmean(m: np.ndarray, k: int = 5) -> np.ndarray:
    """k×k 邻域内 True 的比例（积分图实现，只为去孤立噪点，不做形态学）。"""
    c = m.astype(np.int32).cumsum(0).cumsum(1)
    p = np.pad(c, ((1, 0), (1, 0)))
    h, w = m.shape
    y1 = np.minimum(np.arange(h) + k, h)
    y0 = np.maximum(np.arange(h) - k + 1, 0)
    x1 = np.minimum(np.arange(w) + k, w)
    x0 = np.maximum(np.arange(w) - k + 1, 0)
    tot = p[np.ix_(y1, x1)] - p[np.ix_(y0, x1)] - p[np.ix_(y1, x0)] + p[np.ix_(y0, x0)]
    area = (y1 - y0)[:, None] * (x1 - x0)[None, :]
    return tot / np.maximum(area, 1)


def _diff_mask(a, b, box) -> np.ndarray:
    """在 box 内做 |A-B|>DELTA，并按 5×5 邻域密度剔孤立噪点。"""
    x, y, w, h = (max(box[0], 0), max(box[1], 0), box[2], box[3])
    H, W = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    w, h = min(w, W - x), min(h, H - y)
    if w <= 0 or h <= 0:
        return np.zeros((0, 0), dtype=bool)
    m = (np.abs(a[y:y + h, x:x + w, :3].astype(int)
                - b[y:y + h, x:x + w, :3].astype(int)).max(axis=2) > DELTA)
    return m & (_boxmean(m) >= 0.5)


def _col_runs(m: np.ndarray) -> int:
    """有内容的列段数：数 False→True 的跳变，比 count_nonzero(diff)//2+1 少一个 off-by-one
    （首列为空时那个 +1 会凭空多算一段）。"""
    c = np.concatenate(([False], m.any(axis=0)))
    return int(np.count_nonzero(np.diff(c.astype(np.int8)) == 1))


def _footprint(m: np.ndarray, rw: int, rh: int) -> dict:
    """窗口那一格里的足迹：画了多少、外接框多大、列分几段。"""
    n = int(m.sum())
    if not n:
        return {"px": 0}
    ys, xs = np.nonzero(m)
    return {"px": n,
            "占比": round(n / (rw * rh), 3),        # 1.0 = 整格自填 = 不透明
            "content_w": int(xs.max() - xs.min() + 1),
            "content_h": int(ys.max() - ys.min() + 1),
            "col_runs": _col_runs(m),
            "top_gap": int(np.argmax(m.any(axis=1))),
            "bottom_gap": int(np.argmax(m[::-1].any(axis=1)))}


def _noise_floor(a, b, box) -> dict | None:
    """同一尺寸、窗口从没到过的一格 —— 量"背景自己重绘"能给差值法贡献多少假阳性。

    背景是活着的终端（本 CLI 的 TUI），没有这块底，"足迹占比 0.1" 这句话
    分不清是"窗口透明"还是"终端刚好在那一格刷了屏"。
    """
    x, y, w, h = box
    for ny in (y + h + 8, y - h - 8):
        if ny >= 0 and ny + h <= min(a.shape[0], b.shape[0]):
            m = _diff_mask(a, b, (x, ny, w, h))
            if m.size:
                return _footprint(m, w, h)
    return None


def _rect_shift_diff(a, b, dx: int, rect) -> dict | None:
    """已知窗口矩形时的读法：只在这一格里问"窗口挪走之后谁留下了痕迹"。

    A 帧窗口在 R、B 帧窗口在 R+dx ⇒ R 内的两帧之差 = A 里窗口**真正画出来**的那些像素
    （B 在 R 已经是纯桌面）。不靠全局外接框：全局框会被终端重绘撑到全屏（本机实测过）。
    """
    rx, ry, rw, rh = rect
    if dx < rw:
        fact("窗口矩形读数", f"跳过：只挪了 {dx}px < 窗口宽 {rw}px，重叠列两帧都有窗口 ⇒ 必然零差")
        return None
    box = (rx, ry, rw + dx, rh)
    m = _diff_mask(a, b, box)
    if m.size == 0:
        fact("窗口矩形读数", "跳过：窗口矩形（含挪位后）越出屏幕")
        return None
    return {"读法": "限定在窗口矩形内", "窗口矩形": [int(v) for v in rect],
            "原位足迹": _footprint(m[:, :rw], rw, rh),
            "挪位足迹": _footprint(m[:, dx:dx + rw], rw, rh),
            "桌面噪声底": _noise_floor(a, b, box),
            "changed_px": int(m.sum()), "bbox": list(box), "_box": list(box)}


def shift_diff(a, b, S: int, dx: int, rect=None) -> dict | None:
    """两帧之差 = 窗口在 A 位的足迹 ∪ 窗口在 B 位（A 沿 x 挪了 dx）的足迹。

    不透明 ⇒ 足迹是整块 S×S，并集是 |dx|+S 宽、S 高的**实心**矩形、列不分段。
    透明   ⇒ 足迹只有画了东西的部分，并集在 y 轴上明显矮于 S，列间还有空档。
    读到窗口矩形（从焦点环反推）就走 _rect_shift_diff；退化成全局外接框只在环不可用时发生，
    那条路对背景的活体重绘没有抵抗力，数值只能当参考。
    """
    if a is None or b is None:
        return None
    if rect:
        r = _rect_shift_diff(a, b, dx, rect)
        if r:
            return r
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    m = (np.abs(a[:h, :w, :3].astype(int) - b[:h, :w, :3].astype(int)).max(axis=2) > DELTA)
    m = m & (_boxmean(m) >= 0.5)
    n = int(m.sum())
    if n == 0:
        return {"changed_px": 0, "bbox": None}
    ys, xs = np.nonzero(m)
    x0, y0 = int(xs.min()), int(ys.min())
    bw, bh = int(xs.max()) - x0 + 1, int(ys.max()) - y0 + 1
    runs = _col_runs(m)                        # 断开处就是"挪开后露出的桌面"
    return {
        "changed_px": n,
        "bbox": [x0, y0, bw, bh],
        "content_w": bw - abs(dx), "content_h": bh,
        "solidity": round(n / (2 * S * S), 3),   # 两块实心矩形 = 1.0
        "col_runs": runs,
        "top_gap": int(np.argmax(m.any(axis=1))),      # 足迹上方空几行 ⇒ 顶边有没有被盖住
        "bottom_gap": int(np.argmax(m[::-1].any(axis=1))),
    }


def dump(a, b, r: dict | None, tag: str) -> None:
    """把两帧的足迹区上下拼一张：形状判读有时只能靠眼睛。"""
    if a is None or b is None or r is None or not r["bbox"]:
        return
    DUMP.mkdir(parents=True, exist_ok=True)
    x, y, w, h = r["bbox"]
    comb = np.concatenate([a[y:y + h, x:x + w, :3], b[y:y + h, x:x + w, :3]], axis=0)
    path = DUMP / f"{tag}_AB.png"
    Image.fromarray(comb).save(path)
    fact("落盘", str(path))


def verdict(r: dict | None, S: int) -> str:
    if r is None:
        return "抓帧失败 ⇒ 不可判"
    if r["changed_px"] == 0:
        return "两帧零变化 ⇒ 窗口没上屏或没被挪动，不可判"
    if "原位足迹" in r:
        return _rect_verdict(r)
    if r["bbox"][2] < 1.2 * S:
        return (f"并集外接框只有 {r['bbox'][2]} 宽（挪动 {S}px 后应长出 1.2 倍以上）"
                " ⇒ 窗口实际没被挪动（被屏幕边缘夹住？），不可判")
    if r["content_w"] >= 0.97 * S and r["content_h"] >= 0.97 * S and r["col_runs"] == 1:
        return (f"**不透明**：足迹是整块 {S}×{S}，两帧实心度 {r['solidity']}"
                f"（1.0 = 两块完整矩形）、列分段 {r['col_runs']}")
    return (f"**透明成立**：窗口只盖住 {r['content_w']}×{r['content_h']}（窗口 {S}×{S}），"
            f"实心度 {r['solidity']}、列分段 {r['col_runs']}（≥2 = 中间露出桌面）"
            f"、上下空隙 {r['top_gap']}/{r['bottom_gap']}px ⇒ 其余像素透出了桌面")


def _rect_verdict(r: dict) -> str:
    """按窗口那一格的占比判：整格自填 = 不透明，明显小于 1 = 其余像素透出了桌面。

    必须先看噪声底：桌面噪声底占比和足迹同量级 ⇒ 这台机器的背景在刷自己，
    差值法给不出结论，直说不可判（数值好看也不算）。
    """
    a, b, floor = r["原位足迹"], r["挪位足迹"], r.get("桌面噪声底") or {}
    if a.get("px", 0) == 0 or b.get("px", 0) == 0:
        return "窗口位内零足迹 ⇒ 窗口没画东西或没挪动，不可判"
    noise = float(floor.get("占比", 0.0))
    frac = min(a["占比"], b["占比"])
    if noise > 0.5 * frac:
        return (f"不可判：窗口位内足迹占比 {frac}，但同尺寸纯桌面格的噪声底就有 {noise}"
                " ⇒ 背景自己在重绘，这个量具此刻分不开"
                "「窗口透明」和「终端刷了屏」")
    if a["占比"] >= 0.97 and b["占比"] >= 0.97 \
            and a["col_runs"] == 1 and b["col_runs"] == 1:
        return (f"**不透明**：窗口位内足迹占满整格（{a['占比']}/{b['占比']}、列不分段）"
                f"，噪声底仅 {noise}")
    return (f"**透明成立**：窗口位 {r['窗口矩形'][2]}×{r['窗口矩形'][3]} 内只画了 "
            f"{a['占比']}/{b['占比']}（原位/挪位），外接 {a['content_w']}×{a['content_h']}，"
            f"列分段 {a['col_runs']}/{b['col_runs']}，上下空隙 "
            f"{a['top_gap']}/{a['bottom_gap']}px；同尺寸桌面格噪声底 {noise} ⇒ "
            "其余像素透出了桌面，客户端 alpha 生效")


def measure(app, host, S: int, tag: str, give_focus_back: object,
            ring: tuple[tuple[int, int, int] | None, int | None]) -> dict | None:
    ring_rgb, ring_w, _ = ring
    host.setWindowTitle(TITLE)
    host.resize(S, S)
    host.show()
    ok, info = float_self(app, host, S)
    print(f"\n== {tag} ==", flush=True)
    fact("浮动", json.dumps(info, ensure_ascii=False))
    if not ok:
        print(f"  [{tag}] ✗ 未能浮动 ⇒ 平铺会重排桌面、差值法失效，不猜", flush=True)
        host.hide()
        return None
    wid, winfo = float_win_id()
    fact("浮动窗口 id", json.dumps(winfo, ensure_ascii=False))
    if wid is None:
        host.hide()
        return None
    pump(app, 600)
    f = A10.capture(f"wd_{tag}_focused")     # 聚焦态：niri 的焦点环实心底在这帧里
    if give_focus_back is None:
        fact("焦点交还", "开局没读到别的聚焦窗口 ⇒ 不敢乱切焦点，本段按聚焦态读（会含环底色）")
        a = f
    else:
        fact("焦点交还给原窗口", H3._focus_window(give_focus_back))
        pump(app, 900)
        if H3._is_focused(wid):
            print(f"  [{tag}] ✗ 切焦点没生效，窗口仍聚焦 ⇒ 焦点环会盖住透明判读，不猜", flush=True)
            host.hide()
            return None
        a = A10.capture(f"wd_{tag}_A")       # 失焦、原位：这一帧才是"没有环"的背景可比帧
    ring_reading = ring_readout(f, a, S, ring_rgb) if (ring_rgb and a is not f) else None
    if ring_reading:
        fact("焦点环读数", json.dumps(ring_reading, ensure_ascii=False))
    rect = rect_from_ring(ring_reading, S, ring_w)
    fact("挪动窗口", niri_move(wid, S, 0))
    pump(app, 1100)
    b = A10.capture(f"wd_{tag}_B")
    fact("挪回去", niri_move(wid, -S, 0))    # 别把用户的桌面留在偏移态
    pump(app, 400)
    host.hide()
    pump(app, 300)
    r = shift_diff(a, b, S, S, rect)
    fact("两帧之差(失焦、同内容挪一格)", json.dumps(r, ensure_ascii=False))
    dump(a, b, r, tag)
    print(f"  ⇒ {verdict(r, S)}", flush=True)
    return r


def l2d_read(app, S: int, give_focus_back: object, ring) -> None:
    """产品自己的 Live2D 窗口 + 产品 PetWindow 的全套属性（一次上屏即可分开两种读法）。"""
    from meapet.config.store import load_config, resolve_resource_path
    from meapet.desktop.live2d_widget import Live2DModel, init_live2d

    print("\n== 产品 Live2D 窗口（PetWindow 全套属性） ==", flush=True)
    cfg = load_config()
    model_dir = resolve_resource_path((cfg.get("live2d") or {}).get("model_dir", ""))
    if not model_dir or not os.path.isdir(model_dir):
        fact("模型目录", f"不存在({model_dir!r}) ⇒ 本段不可判")
        return
    init_live2d()  # 缺它 native LAppModel() 直接段错误
    model = Live2DModel(model_dir)
    host = QWidget()
    host.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    host.setAttribute(Qt.WA_TranslucentBackground, True)
    host.setAttribute(Qt.WA_AlwaysStackOnTop, True)                      # app.py:259
    host.setProperty("_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_UTILITY")  # app.py:265
    widget = model.create_widget(host)
    widget.resize(S, S)   # 产品由 render_host 的视口算法做这一步；探针里手动等效补齐
    widget.move(0, 0)
    widget.show()
    measure(app, host, S, "产品Live2D窗口", give_focus_back, ring)


def main() -> int:
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    fmt.setStencilBufferSize(8)
    fmt.setRenderableType(QSurfaceFormat.OpenGL)
    QSurfaceFormat.setDefaultFormat(fmt)      # 与 app.py:821-827 同一前置
    app = QApplication(sys.argv[:1])
    S = 200
    if A10.capture("wd_probe") is None:
        fact("grim", "抓不到 ⇒ 不可判")
        return 2
    back = H3._focused_win_id()      # 开窗前先记住用户原本聚焦的是谁，量完还回去
    fact("原聚焦窗口", f"id={back}")
    ring = H3._ring_style()
    ring_rgb, ring_w, src = ring
    fact("niri 焦点环", f"active-color={ring_rgb} width={ring_w} ← {src}")

    r = RasterDot()
    r.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    r.setAttribute(Qt.WA_TranslucentBackground, True)
    measure(app, r, S, "光栅对照组", back, ring)
    l2d_read(app, S, back, ring)

    print("\n== 读法 ==", flush=True)
    print("  失焦后实心度 ≪ 1 ⇒ **Wayland 透明成立**，H3 看到的浅蓝底是 niri 焦点环的"
          "实心背景（config.kdl:170-178 自述会透过半透明窗口显示），不是客户端没给 alpha", flush=True)
    print("  失焦后仍是实心矩形 ⇒ 客户端真的没提交 alpha，产品窗口在 Wayland 上带一块底色，"
          "要单独立项", flush=True)
    print("  对 H3 的后果：之前所有'屏上模板'都含环底色 ⇒ 真机段要在失焦（或用户配 "
          "`draw-border-with-background false`）后重跑才算数", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
