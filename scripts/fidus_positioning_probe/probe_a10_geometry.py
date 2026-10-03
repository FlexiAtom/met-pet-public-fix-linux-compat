#!/usr/bin/env python3
"""A10 实机几何探针：**请求坐标 ↔ 屏幕实际落点**在分数缩放下差多少。

出处：meapet 定位线 `~/.Athena/projects/meapet/working/fidus-self-window-positioning.md`
§3 假设表 A10 ＋ §11.2 拆分（A10-a 分数缩放 / A10-b 多输出）。该线的全部可感知收益挂在
"映射不确定"这一前提上，而此前只有 fidus 自己文档里的 L3 数字（对 meapet 只算 L0 转述）。
本探针把 meapet 自己这条路径（Qt 宿主 + 桥接层 + wheel）的数取出来。

为什么必须自带基准（本探针存在的技术理由）
----------------------------------------
`niri msg layers` **不回报** surface 几何（实测：只有 namespace/layer/output/键盘交互），
所以合成器侧拿不到独立落点。若用 fidus 的 `estimate()` 读数当 ground truth，就是在用
被测量者校准测量者——**循环论证**。故本脚本另建一条基准链：

    grim 抓整幅输出（物理像素）→ 我自己的多尺度零均值归一化互相关（FFT + 前缀和精确 NCC）
    → 独立得到 (实测左上角, 实测缩放因子, 峰值相似度)

这条链只依赖 PIL 的缩放与 numpy 的 FFT，与 fidus 的匹配器/标定**无共享代码**。
两链在 scale=1.0 上互相印证（残差≈0）才是本探针可用的前提，不是结论。

三处坐标口径（A10 争的就是它们是否相等）
--------------------------------------
| 口径 | 谁持有 | 含义 |
|---|---|---|
| 请求 (margin) | 桥接 `set_position(x,y)` → `set_margin(top=y,0,0,left=x)` | 按协议是**合成器逻辑像素** |
| Qt 信念 | `QScreen.geometry()/devicePixelRatio()` | Qt5 `libqwayland-generic` 只见整数 scale |
| 实测 | grim 抓帧里的物理像素位置 | 唯一独立真值 |

探针因此同时摆放两类角点：`niri-corner-br`（按合成器逻辑算的右下内缩，应当正确）与
`qt-corner-br`（按 Qt 信念算的右下内缩，正是产品代码现在会算出的值）。scale=1 时两者重合
（控制组）；scale>1 时若 A10 成立，后者会跑到屏幕外——**这就是本机制收益前提的判据本身**。

判据（四条独立 VERDICT，不合并、不互相顶替）
------------------------------------------
* `CAPABLE`：该输出配置下 fidus 能否完成 `calibrate_once` 并给出非异常 `estimate`。
* `SCALE-APPLIED`：我实测的放大因子 vs 合成器标称 scale（桥接上传的是 200px 缓冲，
  若合成器按 scale 拉伸，实测因子≈scale；若不拉伸，≈1.0）。
* `MAP`：实测落点更接近"请求=逻辑像素"还是"请求=物理像素"的预测，以及最大残差。
* `FIDUS-ACCURACY`：fidus 读数 vs 我的独立实测中心之残差（顺带完成"摆放位置几何正确性"
  验收对象里 fidus 侧那一半；不据此评价 fidus 精度宣称）。

`--qt-only`：另一问，零 fidus 零桥接层
-------------------------------------
量具同上（grim + 逐像素），但被测对象换成 **Qt 自己的 xdg-toplevel 色块**，两问：
* `QT-BELIEF`：Qt 的每一个自述接口（`mapToGlobal`/`windowHandle().position()`/
  `geometry`/`frameGeometry`）报的落点，与独立实测差多少。
* `QT-SIZE-*`：请求 W×H 逻辑像素在屏幕上实际占多少物理像素（判"桌宠在分数屏上会不会过大"）。
外加一条 `QT-CURSOR`（自述 `QCursor.pos()` vs `grim -c` 两帧差集），它在本机**常不可判**
——屏幕上有闪烁项与光标争同一区域，加了"无光标两帧"作禁判区对照后仍常归零；不可判不记成 0。

跑法（**不改输出配置**，配置由外层脚本用 `niri msg output … scale …` 设定并还原）
    WAYLAND_DISPLAY=wayland-1 .venv/bin/python \
        scripts/fidus_positioning_probe/probe_a10_geometry.py [--tag baseline]
    WAYLAND_DISPLAY=wayland-1 .venv/bin/python \
        scripts/fidus_positioning_probe/probe_a10_geometry.py --qt-only

副作用：真实桌面上贴一个不透明图案层、fidus 投射标定标记并整幅截屏数秒；外层还会临时改
eDP-1 的 scale（niri 该命令是**临时**的，不写配置文件，且由外层 trap 还原）。
退出码：0 = 测完（**残差再大也是 0**——这是量具不是门禁）；1 = 量具自身不可用。
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]

# 与 H1 探针同一条硬约束：Qt 平台必须在 import PyQt5 之前定死。
os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QCursor, QColor, QImage, QPainter  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

try:
    from PIL import Image
except Exception as exc:  # pragma: no cover
    print(f"[harness] ✗ PIL 不可用（独立基准链需要它）: {exc}", flush=True)
    sys.exit(1)

try:
    import fidus as fidus_mod
except Exception as exc:  # pragma: no cover
    print(f"[harness] ✗ fidus wheel 不可导入: {type(exc).__name__}: {exc}", flush=True)
    sys.exit(1)


def _load_facade():
    """按文件路径直载桥接门面，绕开 `meapet.desktop.__init__` 的 import 链（同 H1 探针）。"""
    import importlib.util

    path = REPO_ROOT / "meapet" / "desktop" / "wayland_layer.py"
    spec = importlib.util.spec_from_file_location("wl_facade_a10", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FACADE = _load_facade()

PATTERN_W = 200
PATTERN_H = 200
CORNER = 36          # 模板角块边长
CORNER_OFF = 10      # 角块距模板边缘
INSET = 24           # 摆放时距屏边内缩
SETTLE_MS = 1200     # 位移后稳定等待（H1 实测 400ms 仍在过渡态 ⇒ 这里放大）


def fact(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


# ---------------------------------------------------------------- 图案（与 H1 探针逐字相同）
def make_pattern() -> np.ndarray:
    h, w = PATTERN_H, PATTERN_W
    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[..., :3] = 235
    a[..., 3] = 255
    a[:, w // 2 - 6 : w // 2 + 6, :3] = 15
    a[h // 2 - 6 : h // 2 + 6, :, :3] = 15
    for (y0, x0), col in (
        ((10, 10), (220, 30, 30)),
        ((10, w - 46), (30, 200, 40)),
        ((h - 46, 10), (40, 60, 220)),
        ((h - 46, w - 46), (240, 200, 20)),
    ):
        a[y0 : y0 + 36, x0 : x0 + 36, :3] = col
    for i in range(0, w, 24):
        a[h - 70 : h - 54, max(0, i - 6) : min(w, i + 6), :3] = 90
    return np.ascontiguousarray(a)


def qimage_from(arr: np.ndarray) -> QImage:
    h, w = arr.shape[0], arr.shape[1]
    return QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy()


class HostWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.repaints = 0
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnBottomHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(48, 48)

    def paintEvent(self, ev):  # noqa: N802
        self.repaints += 1
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 20, 24))
        p.end()


# ---------------------------------------------------------------- 独立基准：grim + 我自己的 NCC
def capture(tag: str, extra: tuple[str, ...] = ()) -> np.ndarray | None:
    """grim 抓**整幅输出**（物理像素）。失败返回 None，不抛（抓屏属于环境的边界）。"""
    out = Path("/tmp") / f"a10_{tag}_{int(time.time() * 1000)}.png"
    try:
        r = subprocess.run(["grim", *extra, "-t", "png", str(out)],
                           capture_output=True, timeout=15)
        if r.returncode != 0 or not out.exists():
            print(f"    [capture] ✗ grim rc={r.returncode} {r.stderr.decode(errors='replace')[:160]}",
                  flush=True)
            return None
        img = Image.open(out).convert("RGB")
        arr = np.asarray(img, dtype=np.uint8)
        out.unlink(missing_ok=True)
        return np.ascontiguousarray(arr)
    except Exception as exc:
        print(f"    [capture] ✗ {type(exc).__name__}: {exc}", flush=True)
        return None


def _luma(rgb: np.ndarray) -> np.ndarray:
    f = rgb.astype(np.float32)
    return f[..., 0] * 0.299 + f[..., 1] * 0.587 + f[..., 2] * 0.114


class Correlator:
    """同一帧上做多尺度模板匹配：图的那一份 FFT 与两套前缀和只算一次。

    零均值归一化互相关，分母用 2D 前缀和精确给出每个窗口的局部均值/方差——不是"减全局
    均值再相关"那种近似，否则壁纸亮度梯度会把峰值推偏，量具自己就先不可信。
    前缀和走 float64：1366×768 的平方和累积到 1e9 量级，float32 的有效位不够。
    """

    def __init__(self, luma: np.ndarray):
        self.img = luma.astype(np.float64)
        self.H, self.W = self.img.shape
        c1 = np.pad(self.img, ((1, 0), (1, 0)))
        c2 = np.pad(self.img * self.img, ((1, 0), (1, 0)))
        self.S1 = c1.cumsum(0).cumsum(1)
        self.S2 = c2.cumsum(0).cumsum(1)
        self._F: dict[tuple[int, int], np.ndarray] = {}

    def peak(self, tmpl: np.ndarray) -> tuple[float, int, int]:
        th, tw = tmpl.shape
        if th > self.H or tw > self.W:
            return -2.0, -1, -1
        t = tmpl.astype(np.float64)
        tm = float(t.mean())
        tc = t - tm
        energy = float((tc * tc).sum())
        if energy <= 0:
            return -2.0, -1, -1
        # 补零到 (H+th−1, W+tw−1) 的 2 的幂 ⇒ 环绕不落在有效区内
        fh = 1 << (self.H + th - 1).bit_length()
        fw = 1 << (self.W + tw - 1).bit_length()
        F = self._F.get((fh, fw))
        if F is None:
            F = np.fft.rfft2(self.img, (fh, fw))
            self._F[(fh, fw)] = F
        G = np.fft.rfft2(tc, (fh, fw))
        hs, ws = self.H - th + 1, self.W - tw + 1
        corr = np.fft.irfft2(F * np.conj(G), (fh, fw))[:hs, :ws]
        S1, S2 = self.S1, self.S2
        s = (S1[th : th + hs, tw : tw + ws] - S1[:hs, tw : tw + ws]
             - S1[th : th + hs, :ws] + S1[:hs, :ws])
        ss = (S2[th : th + hs, tw : tw + ws] - S2[:hs, tw : tw + ws]
              - S2[th : th + hs, :ws] + S2[:hs, :ws])
        n = float(th * tw)
        var = np.maximum(ss / n - (s / n) ** 2, 0.0)
        # NCC = Σ I·(T−T̄) / sqrt( Σ(I−Ī)² · Σ(T−T̄)² ) = corr / sqrt(n·var·energy)
        # 分母若写成 n·σ_w·σ_T 就多除了 √n ⇒ 峰值恒为 1/√n（合成图自证第一步就是这么发现的：
        # 位置误差 0.00px 但峰值 0.005 = 1/√40000，定位对、刻度错）。
        denom = np.sqrt(n * var * energy)
        out = np.full((hs, ws), -2.0)
        ok = denom > 1e-9
        out[ok] = corr[ok] / denom[ok]
        idx = int(np.argmax(out))
        y, x = divmod(idx, ws)
        return float(out[y, x]), int(x), int(y)


def local_ncc(rgb: np.ndarray, pattern: np.ndarray, x: int, y: int, scale: float) -> float:
    """在**指定**左上角、指定缩放处直接算一次 NCC（不做搜索）。

    用途是把两件事分开：'图案在不在请求点'与'图案跑到哪儿去了'。只有全局搜索峰值会
    把二者混成一个数。
    """
    tw, th = int(round(PATTERN_W * scale)), int(round(PATTERN_H * scale))
    if x < 0 or y < 0 or x + tw > rgb.shape[1] or y + th > rgb.shape[0]:
        return -3.0  # 请求点已经在帧外：这本身就是结论，不是错误
    tmpl = _luma(np.asarray(
        Image.fromarray(np.ascontiguousarray(pattern[..., :3]), "RGB").resize(
            (tw, th), Image.BILINEAR), dtype=np.uint8))
    win = _luma(rgb[y : y + th, x : x + tw])
    a = win - win.mean()
    b = tmpl - tmpl.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 1e-9 else -2.0


def niri_layers() -> list:
    try:
        raw = subprocess.run(
            ["niri", "msg", "--json", "layers"], capture_output=True, text=True, timeout=8
        ).stdout
        return json.loads(raw)
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]


def scale_grid(nominal: float) -> list[float]:
    return sorted({1.0, round(float(nominal), 2)}
                  | {round(v, 2) for v in np.arange(0.9, 2.26, 0.05)})


def measure(rgb: np.ndarray, pattern: np.ndarray, scales) -> dict:
    """多尺度搜索：对每个候选 scale 缩放模板取 NCC 峰，返回最优者。

    给出**独立**的 (实测放大因子, 实测左上角物理坐标, 峰值) —— 这就是本探针的真值来源。
    """
    gray = _luma(rgb)
    corr = Correlator(gray)
    best = {"scale": None, "x": None, "y": None, "peak": -2.0, "w": None, "h": None}
    per = []
    src = Image.fromarray(np.ascontiguousarray(pattern[..., :3]), "RGB")
    for s in scales:
        tw, th = int(round(PATTERN_W * s)), int(round(PATTERN_H * s))
        if tw < 8 or th < 8 or th > gray.shape[0] or tw > gray.shape[1]:
            continue
        tmpl = _luma(np.asarray(src.resize((tw, th), Image.BILINEAR), dtype=np.uint8))
        peak, x, y = corr.peak(tmpl)
        per.append((round(s, 3), round(peak, 4), x, y))
        if peak > best["peak"]:
            best = {"scale": s, "x": x, "y": y, "peak": peak, "w": tw, "h": th}
    best["per_scale"] = per
    if best["x"] is not None:
        best["cx"] = best["x"] + best["w"] / 2.0
        best["cy"] = best["y"] + best["h"] / 2.0
    return best


def selftest() -> int:
    """量具自证（**零桌面副作用**）：把已知位置/已知缩放的图案嵌进合成壁纸再找回来。

    没有这一步，后面所有"实测落点"都只是另一个未经校准的读数——本探针的立身之本恰恰是
    "基准独立于 fidus"，所以**基准自己**也要先过一次校准。
    """
    rng = np.random.default_rng(7)
    pattern = make_pattern()
    bg = (rng.integers(0, 256, (768, 1366, 3), dtype=np.uint8) // 8 * 8).astype(np.uint8)
    bg[::3, :, :] = 12  # 人造亮度梯度，防止"只在纯噪声上能用"
    scales = scale_grid(1.0)
    truth = {"topleft": (10, 20), "mid": (411, 133), "br": (900, 400)}
    worst = 0.0
    worst_peak = 1.0
    print("=" * 78)
    print("SELFTEST：合成图上的位置/缩放回收（不碰真实桌面）")
    print("=" * 78)
    for s in (1.0, 1.25, 1.5, 2.0):
        tw, th = int(round(PATTERN_W * s)), int(round(PATTERN_H * s))
        small = np.asarray(
            Image.fromarray(np.ascontiguousarray(pattern[..., :3]), "RGB").resize(
                (tw, th), Image.BILINEAR
            ),
            dtype=np.uint8,
        )
        for name, (tx, ty) in truth.items():
            if tx + tw > 1366 or ty + th > 768:
                continue
            canvas = bg.copy()
            canvas[ty : ty + th, tx : tx + tw] = small
            cx, cy = tx + tw / 2.0, ty + th / 2.0
            m = measure(canvas, pattern, scales)
            err = max(abs(m["cx"] - cx), abs(m["cy"] - cy)) if m["x"] is not None else 9e9
            worst = max(worst, err)
            worst_peak = min(worst_peak, m["peak"])
            worst_peak = min(worst_peak, m["peak"])
            fact(f"s={s} {name}", f"scale={m['scale']} 峰={m['peak']:.4f} "
                 f"左上=({m['x']},{m['y']}) 真值=({tx},{ty}) 中心误差={err:.2f}px")
    m_absent = measure(bg.copy(), pattern, scales)
    fact("空白图最高峰（假阳性对照）", f"{m_absent['peak']:.4f}")
    # 三条各管一头：定位精度（误差）、**刻度**（真位置峰值须≈1）、假阳性（空白图峰值）
    ok = worst < 2.0 and worst_peak > 0.9 and m_absent["peak"] < 0.35
    print("VERDICT-TOOL-SELFTEST:", "PASS" if ok else "FAIL",
          f"(最大中心误差={worst:.2f}px 真位置最低峰={worst_peak:.4f} 空白峰="
          f"{m_absent['peak']:.4f}；判据 误差<2px 且 真峰>0.9 且 空白峰<0.35)")
    print("=" * 78)
    return 0 if ok else 3


# ---------------------------------------------------------------- 合成器 / Qt 事实


# ---------------------------------------------------------------- 合成器 / Qt 事实
def niri_outputs() -> dict:
    raw = subprocess.run(
        ["niri", "msg", "--json", "outputs"], capture_output=True, text=True, timeout=8
    ).stdout
    return json.loads(raw)  # {输出名: {...}}——不是列表（H1 探针踩过）


def triples(vals: list) -> list[tuple[float, float, float]]:
    """从 estimate 的 [(值, 耗时), ...] 里取出成功的 (x, y, confidence)。

    两层都要拆开：外层是 `(返回值, 耗时)` 对，内层才是 fidus 的三元组，而且**内层是 list
    不是 tuple**。按 `isinstance(外层, tuple) and len(外层)==3` 过滤会把所有有效读数丢掉
    并静默返回 None——本探针第一版的 CAPABLE=FAIL 就是这个判据错，不是 fidus 失败。
    """
    out = []
    for v in vals or []:
        head = v[0]
        if isinstance(head, (tuple, list)) and len(head) == 3:
            try:
                out.append((float(head[0]), float(head[1]), float(head[2])))
            except (TypeError, ValueError):
                pass
    return out


def median_est(vals: list) -> tuple | None:
    t = triples(vals)
    if not t:
        return None
    a = np.array([[x, y] for x, y, _c in t])
    return float(np.median(a[:, 0])), float(np.median(a[:, 1])), len(t)


# ---------------------------------------------------------------- fidus 线程
def fidus_worker(box, pattern, positions, ev_pos, ev_done):
    """`Fidus` 是 unsendable：建、用、释放都在本线程（H1 探针同一条约束）。

    每轮：主线程摆好位并抓完帧后置位 ev_pos，本线程只做 estimate×3 后置 ev_done。
    把"摆位/抓帧"留在主线程，是为了让 Qt 事件循环在两次操作之间继续被泵。
    """
    try:
        eng = fidus_mod.Fidus.build_wayland()
    except BaseException as exc:
        box["fatal"] = f"build_wayland: {type(exc).__name__}: {exc}"
        return
    try:
        box["gate_status"] = eng.gate_status()
        try:
            eng.register_target(pattern, ambiguous=False)
        except BaseException as exc:
            box["fatal"] = f"register_target: {type(exc).__name__}: {exc}"
            return
        try:
            t0 = time.perf_counter()
            box["calibrate"] = (repr(eng.calibrate_once()), time.perf_counter() - t0)
        except BaseException as exc:
            box["fatal"] = f"calibrate_once: {type(exc).__name__}: {exc}"
            return
        for name, _xy in positions:
            ev_pos.wait(timeout=90)
            ev_pos.clear()
            vals = []
            for _ in range(3):
                t = time.perf_counter()
                try:
                    vals.append((eng.estimate(), time.perf_counter() - t))
                except BaseException as exc:
                    vals.append((f"RAISED {type(exc).__name__}: {exc}", time.perf_counter() - t))
                time.sleep(0.1)
            box.setdefault("est", {})[name] = vals
            ev_done.set()
    finally:
        del eng


# ---------------------------------------------------------------- 纯 Qt 色块（不碰桥接层、不碰 fidus）
QT_W = 160      # 请求尺寸，Qt 逻辑像素
QT_H = 100
QT_X = 220      # 请求落点，Qt 逻辑像素
QT_Y = 180


class SolidWindow(QWidget):
    """一个纯洋红不透明矩形：唯一用途是在抓到的帧里能被**逐像素**认出来。

    色块比图案好：边界是 1 物理像素的硬跳变，盒子的四条边由 `np.nonzero` 的极值直接给出，
    不需要互相关那套估计量 ⇒ 这一问的基准链比主探针更短、更不容易自我印证。
    """

    def __init__(self, w: int, h: int):
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(w, h)

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 0, 255))
        p.end()


def _largest_component(mask: np.ndarray):
    """4-连通取最大块，返回 (左, 上, 宽, 高, 像素数)。

    为什么不能直接对全图 `np.nonzero` 取极值：壁纸里散落的近似色会各自成为外接盒的角点，
    把一个 160×100 的块量成 256×328（本机实测踩过）。先取最大连通块，外接盒才属于那个块。
    本机无 scipy/cv2，且色块只有几万像素 ⇒ Python 层 BFS 足够（<50 ms）。
    """
    H, W = mask.shape
    todo = {(int(y), int(x)) for y, x in zip(*np.nonzero(mask), strict=True)}
    best: list[tuple[int, int]] = []
    while todo:
        comp = [start := todo.pop()]
        stack = [start]
        while stack:
            y, x = stack.pop()
            for nb in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if nb in todo:
                    todo.discard(nb)
                    comp.append(nb)
                    stack.append(nb)
        if len(comp) > len(best):
            best = comp
    if not best:
        return None
    ys = np.array([p[0] for p in best])
    xs = np.array([p[1] for p in best])
    x0, y0 = int(xs.min()), int(ys.min())
    return x0, y0, int(xs.max()) - x0 + 1, int(ys.max()) - y0 + 1, len(best)


def solid_bbox(rgb: np.ndarray):
    """返回 (左, 上, 宽, 高, 命中数, 填充率)；没找到返回 None。

    阈值只用于"与壁纸区分"，不用于亚像素定位：不透明纯色填充经合成器后仍是同一个 RGB，
    因此用近精确匹配（±5）而不是"足够红且足够蓝"——后者会把壁纸的品红调一并捞进来。
    填充率是自检：矩形的连通块面积≈外接盒面积，明显小于 1 说明量的不是那个块。
    """
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    mask = (r > 250) & (g < 5) & (b > 250)
    hit = int(mask.sum())
    if hit < 200:
        return None, hit
    box = _largest_component(mask)
    if box is None:
        return None, hit
    x0, y0, w, h, n = box
    return (x0, y0, w, h, n, n / (w * h)), hit


def _dilate(mask: np.ndarray, iters: int = 4) -> np.ndarray:
    """方波膨胀（每轮四方向各移 1 px 再取或）：没有 scipy，手写。"""
    m = mask.copy()
    for _ in range(iters):
        m[1:, :] |= m[:-1, :]
        m[:-1, :] |= m[1:, :]
        m[:, 1:] |= m[:, :-1]
        m[:, :-1] |= m[:, 1:]
    return m


def cursor_bbox(tag: str):
    """独立量**硬件光标的物理位置**：本机 grim 默认**不**画光标，`grim -c` 才画。

    两帧之差 = 光标 ∪ 任何在这 ~100 ms 里变化的像素。故每轮抓**三张**：`-c`、无、无。
    第三张用来把"屏幕上本来就在动的东西"标成禁判区（膨胀 4 px 后从差集里挖掉）。
    不加这个对照会被假阳性骗到：本机实测第一次就得上"光标在 (13,612)"的结论，
    而 无 vs 无 的对照显示同一区域本来就在变（终端闪烁项）⇒ 那条结论当时**不成立**。
    返回 (盒, 说明)；盒为 None 时说明里写为什么不可判——**不可判不记成 0**。
    """
    hits = []
    notes = []
    for k in range(2):
        c = capture(f"{tag}_curc{k}", extra=("-c",))
        p = capture(f"{tag}_curnull{k}")
        p2 = capture(f"{tag}_curnullb{k}")
        if c is None or p is None or p2 is None:
            return None, "grim 不可用"
        if not (c.shape == p.shape == p2.shape):
            return None, f"三帧尺寸不一致 {c.shape}/{p.shape}/{p2.shape}"
        d = np.abs(c.astype(np.int16) - p.astype(np.int16)).max(axis=2) > 12
        anim = _dilate(np.abs(p.astype(np.int16) - p2.astype(np.int16)).max(axis=2) > 12)
        raw = int(d.sum())
        keep = int((d & ~anim).sum())
        notes.append(f"第{k}轮：差集 {raw} px，扣掉动画区后 {keep} px")
        if keep < 6:
            continue
        box = _largest_component(d & ~anim)
        if box is not None:
            hits.append(box)
    if not hits:
        return None, "；".join(notes) + " ⇒ 不可判（光标未绘制，或光标与动画区重叠而被扣除）"
    if len(hits) == 1:
        # 单轮不复现**不能**升格成结论：本机就出现过第 0 轮扣完剩 0 px、第 1 轮剩 77 px 的情形。
        return None, "；".join(notes) + " ⇒ 不可判（只一轮拿到，位置未复现）"
    (x0, y0), (x1, y1) = hits[0][:2], hits[1][:2]
    if max(abs(x0 - x1), abs(y0 - y1)) > 6:
        return None, f"两轮热区不一致 {hits[0][:2]} vs {hits[1][:2]} ⇒ 差集来自动画而非光标"
    return hits[0], "；".join(notes) + f" ⇒ 两轮一致（偏移 {max(abs(x0 - x1), abs(y0 - y1))} px）"


def qt_only(tag: str) -> int:
    """量两件事：**Qt 能不能知道自己窗口的落点**，以及**它请求的尺寸在屏幕上实际是多大**。

    第一问是本模式的主要产出，且它是整条 fidus 定位线的前提命题：Wayland 协议里客户端
    拿不到自己在屏幕上的全局坐标（不像 X11 有根窗口坐标系），所以"桌宠想知道自己在哪"
    只有**一个**正规途径——由合成器/视觉反推。若 Qt 真能自述，fidus 就没有存在理由。
    故这里把 Qt 的每一个自述接口都打一遍，再用抓帧的**独立实测**对照它报了什么：
    报不出来 / 报 0 就是结论本身，不是量具坏了。

    第二问与主探针正交：桥接层走 layer-shell、上传**固定 200px 缓冲**，主探针 `SCALE-APPLIED`
    量到"合成器按 scale 拉伸了我方缓冲"。桌宠**本体**是 Qt 的 xdg-toplevel——Qt 自己按 dpr
    把缓冲放大到 W×dpr 并带 buffer scale 提交，是另一条尺寸协商路径。两种假设差一倍：
      H-逻辑：物理宽 = W × scale        （buffer scale 协商正确，视觉尺寸不变）
      H-缓冲：物理宽 = W × dpr × scale  （视觉尺寸翻倍 ⇒ 桌宠在分数屏上过大）
    scale=1 时两者重合 ⇒ 该配置只是控制组，判据在 1.25 / 1.5 上。

    副作用：桌面上贴一个纯色块数秒 + 一次整幅抓帧。**不改配置、不投射标记、不建桥接层连接、
    不建 fidus 连接。**退出码 0=测完（残差再大也是 0），1=量具不可用。
    """
    print("=" * 78)
    print(f"纯 Qt 色块实测 · tag={tag}（零 fidus、零桥接层）")
    print("=" * 78)
    try:
        outs = niri_outputs()
    except Exception as exc:
        print(f"[harness] ✗ 读不到 niri outputs: {exc}", flush=True)
        return 1
    (oname, oinfo), = outs.items()
    L = oinfo["logical"]
    LX, LY, SC = L["width"], L["height"], float(L["scale"])
    fact("输出", f"{oname} 逻辑 {LX}x{LY} scale={SC} origin=({L.get('x')},{L.get('y')})")
    if len(outs) != 1:
        print("[harness] ⚠ 输出数≠1，单输出口径不再适用", flush=True)

    app = QApplication(sys.argv[:1])
    scr = app.primaryScreen()
    geo = scr.geometry()
    dpr = float(scr.devicePixelRatio())
    fact("Qt 信念", f"platform={app.platformName()} screen={scr.name()} "
                    f"geo={geo.width()}x{geo.height()}@({geo.x()},{geo.y()}) dpr={dpr}")

    win = SolidWindow(QT_W, QT_H)
    win.move(QT_X, QT_Y)
    win.show()
    repaints = {"n": 0}
    from PyQt5.QtCore import QPoint, QTimer

    tmr = QTimer()
    tmr.setInterval(50)
    tmr.timeout.connect(lambda: (repaints.__setitem__("n", repaints["n"] + 1), win.update()))
    tmr.start()
    end = time.perf_counter() + 0.8
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)
    wh = win.windowHandle()

    shot = capture(f"qt{tag}")
    for _ in range(3):
        if shot is not None:
            break
        t = time.perf_counter() + 0.3
        while time.perf_counter() < t:
            app.processEvents()
            time.sleep(0.002)
        shot = capture(f"qt{tag}")
    if shot is None:
        print("[harness] ✗ 抓帧失败（grim 不可用？），量具不可用", flush=True)
        return 1
    fact("抓帧", f"{shot.shape[1]}x{shot.shape[0]} 物理像素")

    # [C] 先量光标：色块挡不挡它都行（它由合成器叠在最上层），且必须在色块自述之前拿到，
    # 才能把"QCursor.pos() 报 0"解释成"它真的在左上角"还是"它根本不知道"。
    cur_self = QCursor.pos()
    cbox, cnote = cursor_bbox(tag)
    cur_line = "不可判" if cbox is None else (
        "QCursor.pos()=(0,0) 而独立实测光标热区非 0 ⇒ **Qt 连光标的全局位置也拿不到**"
        if (cur_self.x() == 0 and cur_self.y() == 0 and (cbox[0], cbox[1]) != (0, 0))
        else f"自述 ({cur_self.x()},{cur_self.y()}) vs 实测 ({cbox[0]},{cbox[1]}) ⇒ 一致，可用")
    fact("光标·自述 vs 实测", f"{cur_line}  （{cnote}）")

    # [A] Qt 的每一个自述接口逐个打一遍——这是"拿不到自己"这条前提命题的取证方式
    bb, hit = solid_bbox(shot)
    fact("自述·exposed", wh is not None and wh.isExposed())
    fact("自述·mapToGlobal(0,0)", f"{win.mapToGlobal(QPoint(0, 0))}")
    fact("自述·windowHandle().position()", f"{wh.position() if wh else None}")
    fact("自述·windowHandle().geometry()", f"{wh.geometry() if wh else None}")
    fact("自述·widget geometry/frameGeometry", f"{win.geometry()} / {win.frameGeometry()}")
    fact("自述·QCursor.pos()", f"{cur_self}  （产品代码里读光标的那条，与 [C] 同一次采样）")
    fact("实况·色块近精确匹配命中像素", hit)
    if bb is None:
        fact("实况", "帧里没有色块 ⇒ Qt 这条连接没把像素提交到屏幕（或被合成器丢弃）")
        print("VERDICT-QT-ONSCREEN      : FAIL（帧里没有色块）")
        print("VERDICT-QT-BELIEF        : 无法对照（没有独立实测可参照）")
        return 0
    bx, by, bw, bh, npx, fill = bb
    fact("实况·色块物理外接盒", f"({bx},{by}) {bw}x{bh}  连通块={npx} 填充率={fill:.3f}")
    fact("实况·反推合成器逻辑落点", f"({bx / SC:.1f},{by / SC:.1f})   "
                                   f"（我们请求的是 ({QT_X},{QT_Y})）")
    if fill < 0.9:
        fact("⚠", f"填充率={fill:.3f}<0.9：块内不全是色块（圆角/阴影/被遮挡），尺寸读数需人工看")

    # [B] 尺寸假设
    pred_logical = QT_W * SC
    pred_buffer = QT_W * dpr * SC
    sep = abs(pred_buffer - pred_logical)
    fact("尺寸·H-逻辑", f"预测物理宽={pred_logical:.1f} 实测={bw} 残差={bw - pred_logical:+.1f}")
    fact("尺寸·H-缓冲", f"预测物理宽={pred_buffer:.1f} 实测={bw} 残差={bw - pred_buffer:+.1f}")

    print()
    pos_self = wh.position() if wh is not None else None
    if pos_self is not None and pos_self.x() == 0 and pos_self.y() == 0 and (bx, by) != (0, 0):
        belief_line = (f"Qt 自述落点恒为 (0,0)，而独立实测色块在 ({bx},{by}) "
                       "⇒ Qt 拿不到自己的屏幕落点（Wayland 协议没有这条信息，不是 Qt 的 bug）")
    else:
        belief_line = f"需人工比对：自述 position={pos_self} vs 实测 ({bx},{by})"
    print(f"VERDICT-QT-BELIEF          : {belief_line}")
    if sep < 8:
        size_line = f"不可判（H-逻辑 与 H-缓冲 重合，间距={sep:.1f}px ⇒ 控制组，只验量具可用）"
    else:
        winner = "H-逻辑" if abs(bw - pred_logical) < abs(bw - pred_buffer) else "H-缓冲"
        size_line = (f"{winner}（实测宽={bw}；H-逻辑={pred_logical:.1f} H-缓冲={pred_buffer:.1f}，"
                     f"两假设差 {sep:.0f}px）")
    print(f"VERDICT-QT-SIZE-HYPOTHESIS : {size_line}")
    print(f"VERDICT-QT-SIZE-VS-REQUEST : 请求 {QT_W}x{QT_H} 逻辑像素 ⇒ 实测物理 {bw}x{bh} "
          f"= {bw / QT_W:.3f}x{bh / QT_H:.3f} 倍；合成器 scale={SC} ⇒ 视觉尺寸误差 "
          f"{bw - pred_logical:+.1f}px（0 ⇒ 桌宠视觉尺寸正确）")
    cpos = f"({cbox[0]},{cbox[1]})" if cbox is not None else "不可判"
    print(f"VERDICT-QT-CURSOR          : 自述 QCursor.pos()=({cur_self.x()},{cur_self.y()}) "
          f"vs 实测热区 {cpos} ⇒ {cur_line}")
    return 0


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="run")
    ap.add_argument("--selftest", action="store_true",
                    help="只跑合成图量具校准（零桌面副作用），不碰真实屏幕")
    ap.add_argument("--qt-only", action="store_true",
                    help="只量 Qt 色块的物理落点/尺寸（零 fidus、零桥接层、零标记投射）")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.qt_only:
        return qt_only(args.tag)
    tag = args.tag

    print("=" * 78)
    print(f"A10 几何实测 · tag={tag} · 独立基准 = grim + 自带多尺度 NCC")
    print("=" * 78)

    print("\n[0] 环境事实")
    try:
        outs = niri_outputs()
    except Exception as exc:
        print(f"[harness] ✗ 读不到 niri outputs: {exc}", flush=True)
        return 1
    for name, o in outs.items():
        lg = o.get("logical", {})
        fact(
            f"输出 {name}",
            f"physical_mode={o['modes'][o['current_mode']]['width']}x"
            f"{o['modes'][o['current_mode']]['height']} logical={lg.get('width')}x{lg.get('height')} "
            f"scale={lg.get('scale')} transform={lg.get('transform')} origin=({lg.get('x')},{lg.get('y')})",
        )
    if len(outs) != 1:
        print("[harness] ⚠ 输出数≠1：本探针的单输出判据不再适用，请人工看输出", flush=True)
    (oname, oinfo), = outs.items()
    L = oinfo["logical"]
    LX, LY, SC = L["width"], L["height"], float(L["scale"])
    mode = oinfo["modes"][oinfo["current_mode"]]
    fact("合成器输出数", len(outs))
    fact("合成器物理模式", f"{mode['width']}x{mode['height']}@{mode['refresh_rate']/1000:.3f}")
    du = json.loads(importlib.metadata.distribution("fidus").read_text("direct_url.json"))
    fact("wheel sha256", du["archive_info"]["hashes"]["sha256"][:16] + "…")
    fact("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM"))

    print("\n[1] Qt 的信念（产品代码现在据以算坐标的那套数）")
    app = QApplication(sys.argv[:1])
    scr = app.primaryScreen()
    geo = scr.geometry()
    avail = scr.availableGeometry()
    fact("platformName", app.platformName())
    fact("screens", [s.name() for s in app.screens()])
    fact(
        "primaryScreen",
        f"{scr.name()} geo={geo.width()}x{geo.height()}@({geo.x()},{geo.y()}) "
        f"dpr={scr.devicePixelRatio()} avail={avail.width()}x{avail.height()}",
    )
    vg = app.desktop().screenGeometry()
    fact("desktop.screenGeometry", f"{vg.width()}x{vg.height()}")
    qt_sees_scale = abs(float(scr.devicePixelRatio()) - SC) > 1e-6 or abs(geo.width() - LX) > 1
    fact(
        "Qt 是否感知合成器 scale",
        f"{'否（geo/DPR 与标称 scale 不一致）' if qt_sees_scale else '是（与标称一致）'} "
        f"→ Qt 信念 {geo.width()}x{geo.height()} vs 合成器逻辑 {LX}x{LY}",
    )

    # 摆放点：两类口径各算一遍右下内缩，外加中心/左上作对照。
    pos: list[tuple[str, tuple[int, int]]] = [
        ("topleft", (INSET, INSET)),
        ("niri-center", (max(0, (LX - PATTERN_W) // 2), max(0, (LY - PATTERN_H) // 2))),
        ("niri-corner-br", (max(0, LX - PATTERN_W - INSET), max(0, LY - PATTERN_H - INSET))),
        ("qt-corner-br", (max(0, geo.right() + 1 - PATTERN_W - INSET),
                          max(0, geo.bottom() + 1 - PATTERN_H - INSET))),
    ]
    print("\n[2] 摆位计划（请求值＝交给桥接层的 margin，单位按协议＝合成器逻辑像素）")
    for n, (x, y) in pos:
        print(f"  {n:<14} 请求=({x},{y})  期望逻辑中心=({x + PATTERN_W / 2:.0f},{y + PATTERN_H / 2:.0f})"
              f"  期望物理中心=({(x + PATTERN_W / 2) * SC:.1f},{(y + PATTERN_H / 2) * SC:.1f})",
              flush=True)
    same = pos[2][1] == pos[3][1]
    fact("niri-corner-br 与 qt-corner-br 是否重合", "重合（scale=1 控制组）" if same
         else f"不重合，差 ({pos[3][1][0] - pos[2][1][0]}, {pos[3][1][1] - pos[2][1][1]}) 逻辑像素"
              " ⇒ 这一格就是 A10 争的东西")

    print("\n[3] 桥接层显示图案")
    host = HostWindow()
    host.move(max(0, LX // 2 - 24), max(0, LY // 2 - 24))
    host.show()
    ticks = {"n": 0}
    from PyQt5.QtCore import QTimer

    tmr = QTimer()
    tmr.setInterval(10)
    tmr.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
    tmr.start()

    def pump(ms):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.002)

    pump(300)
    backend = FACADE.get_backend()
    pattern = make_pattern()
    px0, py0 = pos[0][1]
    try:
        backend.enable(host, PATTERN_W, PATTERN_H, px0, py0)
    except BaseException as exc:
        print(f"[harness] ✗ 桥接 enable 失败: {type(exc).__name__}: {exc}", flush=True)
        return 1
    shim = backend._shim
    shim.layer_last_error.restype = ctypes.c_char_p
    backend.update_pixels(qimage_from(pattern))
    pump(700)
    fact("enable+update_pixels", "OK")
    fact("layer_last_error", shim.layer_last_error())
    fact("niri layers", [(l.get("namespace"), l.get("layer")) for l in niri_layers()])

    scales = scale_grid(SC)
    # [4a] 存在性：先在**请求点**就地量一次，把"没显示出来"与"显示在了别处"分开。
    # 只看全局搜索峰值会把这两件事混成一个数（第一轮控制跑就是这么误判的）。
    # 两个假设各量一遍：合成器可能把 200px 缓冲按 scale 拉伸（期望 scale=SC），也可能原样贴
    # （期望 scale=1.0）——哪个高就是哪个，这本身即 A10-a 的直接读数。
    presence = -9.9
    presence_where = None
    for k in range(5):
        shot0 = capture(f"{tag}_presence{k}")
        if shot0 is None:
            fact("抓帧", "失败：无法判定存在性")
            break
        ex, ey = round(px0 * SC), round(py0 * SC)
        n_stretch = local_ncc(shot0, pattern, ex, ey, SC)
        n_native = local_ncc(shot0, pattern, ex, ey, 1.0)
        fact(f"presence#{k}", f"请求点({ex},{ey}) 处：按 scale={SC} 假设 NCC={n_stretch:.4f} / "
             f"按未缩放假设 NCC={n_native:.4f}")
        if max(n_stretch, n_native) > presence:
            presence = max(n_stretch, n_native)
            presence_where = "合成器按 scale 拉伸" if n_stretch >= n_native else "缓冲未缩放原样贴"
        if presence > 0.5:
            break
        backend.update_pixels(qimage_from(pattern))  # 未显示 ⇒ 重提交一次再看（门面竞态观察点）
        pump(700)
    fact("存在性判定", f"{'图案已按请求显示' if presence > 0.5 else '图案未出现在请求点'} "
         f"(最高局部 NCC={presence:.4f}，形态={presence_where})")
    if presence <= 0.5:
        # 图案根本没上屏 ⇒ 后面所有落点/精度读数都会是噪声，直接判为量具不可用而不是继续跑。
        print("[harness] ✗ 桥接层图案未能建立（重提交 5 次仍不可见）：本轮不产出几何结论", flush=True)
        backend.destroy_context()
        pump(200)
        host.close()
        return 1

    # [4b] 量具自证：在"已显示"的前提下，全局搜索必须能把它找回来（不依赖 fidus）。
    print("\n[4] 量具自证（不依赖 fidus：grim + NCC 能否在当前 scale 下找到图案）")
    shot = capture(f"{tag}_tool")
    if shot is None:
        print("[harness] ✗ 抓不到帧：量具不可用", flush=True)
        return 1
    fact("抓帧尺寸(物理)", f"{shot.shape[1]}x{shot.shape[0]}")
    m = measure(shot, pattern, scales)
    fact("自证峰值 NCC", f"{m['peak']:.4f} @ scale={m['scale']} 左上=({m['x']},{m['y']}) "
         f"尺寸={m['w']}x{m['h']}")
    tool_ok = m["peak"] > 0.5
    fact("量具可用", "是" if tool_ok else "否（峰值过低 ⇒ 本轮所有落点读数只能标为不可用）")

    print("\n[5] 逐位测量（摆位 → 稳定 → 抓帧独立测 → fidus estimate×3）")
    box: dict = {}
    ev_pos = threading.Event()
    ev_done = threading.Event()
    th = threading.Thread(target=fidus_worker,
                          args=(box, pattern, pos, ev_pos, ev_done), daemon=True)
    th.start()
    # **必须**等标定结束再抓第一帧：`calibrate_once` 会在屏幕上投射标记，混进基准帧就等于
    # 让被测量者污染测量者。闸门是"标定结果已写入 box"，不是睡固定时长。
    t_wait = time.perf_counter()
    while "calibrate" not in box and "fatal" not in box and time.perf_counter() - t_wait < 150:
        pump(100)
    fact("标定闸门", f"{time.perf_counter() - t_wait:.2f}s 后 "
         f"{'calibrate 完成' if 'calibrate' in box else '未完成/致命：' + str(box.get('fatal'))}")
    results: dict[str, dict] = {}
    last_req = (px0, py0)
    for name, (x, y) in pos:
        entry: dict = {"req": (x, y)}
        if (x, y) != last_req:
            backend.set_position(x, y)
            last_req = (x, y)
        pump(SETTLE_MS)
        entry["layer_err"] = shim.layer_last_error()
        shot = capture(f"{tag}_{name}")
        entry["shot_ok"] = shot is not None
        if shot is not None:
            mm = measure(shot, pattern, scales)
            entry["meas"] = {k: mm[k] for k in ("scale", "x", "y", "w", "h", "peak", "cx", "cy")}
            entry["top_scales"] = sorted(mm["per_scale"], key=lambda r: -r[1])[:4]
        ev_pos.set()
        ev_done.wait(timeout=150)
        ev_done.clear()
        vals = (box.get("est") or {}).get(name)
        entry["est_raw"] = vals
        tr = triples(vals or [])
        med = median_est(vals or [])
        entry["est_med"] = med
        locked_at = [i for i, t in enumerate(tr) if t[2] > 1e-9]
        entry["conf_nz"] = len(locked_at)
        if tr:
            # `settled` 只从 **conf>0** 的样本里取最后一条。conf==0 时 fidus 报的**不是**它看到的位置：
            # 本机实测两种失效形态——把上一帧读数原样重复（scale=1 右下位），以及按最后速度外推
            # 漂移（scale=1.5 下 1223→1240、1679→1693→1710，每步 ~17 px、耗时 0.06–0.12 s）。
            # 取 tr[-1] 会把"没锁定"读成"位置在 1710 px（屏幕宽 910）"，那是选择错误不是残差。
            settled = tr[locked_at[-1]] if locked_at else None
            entry["settled"] = settled
            entry["lag_n"] = (sum(1 for x, y, _c in tr[:locked_at[-1]]
                                  if max(abs(x - settled[0]), abs(y - settled[1])) > 2.0)
                              if settled else None)
        results[name] = entry
        d = entry.get("meas") or {}
        print(f"  [{name}] 请求=({x},{y}) 实测左上=({d.get('x')},{d.get('y')}) "
              f"实测中心=({d.get('cx')},{d.get('cy')}) 实测scale={d.get('scale')} "
              f"峰={d.get('peak')} fidus稳定={entry.get('settled')} "
              f"滞后条数={entry.get('lag_n')} conf>0条数={entry.get('conf_nz')}", flush=True)
        for i, v in enumerate(vals or []):
            head = v[0] if isinstance(v[0], str) else tuple(round(float(z), 3) for z in v[0])
            print(f"      est[{i}] {v[1]:.2f}s {head}", flush=True)
    if "fatal" in box:
        fact("fidus 致命点", box["fatal"])
    if "calibrate" in box:
        fact("calibrate_once", f"{box['calibrate'][1]:.2f}s {box['calibrate'][0][:200]}")
    th.join(timeout=5)

    print("\n[6] 清理")
    t0 = time.perf_counter()
    backend.destroy_context()
    pump(200)
    fact("destroy_context", f"OK {time.perf_counter() - t0:.3f}s")
    host.close()
    pump(200)

    # ------------------------------------------------------------- 判定
    print("\n" + "=" * 78)
    cap = bool(box.get("calibrate")) and all(
        triples((results.get(n) or {}).get("est_raw") or []) for n, _ in pos
    )
    lags = [(n, (results.get(n) or {}).get("lag_n"), (results.get(n) or {}).get("conf_nz"))
            for n, _ in pos]
    print("VERDICT-PRESENT      :", "PASS" if presence > 0.5 else "FAIL",
          f"(请求点最高局部 NCC={presence:.4f} 形态={presence_where})")
    print("VERDICT-CAPABLE      :", "PASS" if cap else "FAIL",
          f"(标定完成={bool(box.get('calibrate'))} 四位 estimate 全部非异常={cap})")
    ms = [ (results.get(n) or {}).get("meas") or {} for n, _ in pos ]
    applied = [d.get("scale") for d in ms if d.get("peak", -1) > 0.5]
    print("VERDICT-EST-LAG      :", f"各位'与稳定值差>2px'的条数/conf>0 条数={lags} "
          "（滞后≠0 ⇒ 消费方须取稳定值，不能拿首帧读数用）")
    # confidence 的经验分布：若它只在"某个常数"与"0"上取值，那它就是**命中标志位**而不是连续
    # 置信度——这一条是给 fidus 回执第 2 问的现象学答案，不需要读 fidus 源码即可判。
    conf_seen: dict[float, int] = {}
    for n, _ in pos:
        for t in triples((results.get(n) or {}).get("est_raw") or []):
            key = round(float(t[2]), 6)
            conf_seen[key] = conf_seen.get(key, 0) + 1
    print("VERDICT-CONF-SPLIT   :", f"全部 estimate 的 confidence 取值分布={conf_seen}"
          f"（不同值种数={len(conf_seen)}）⇒ "
          f"{'二值/少值，非连续置信度：0 表示未锁定（读数不可用作位置）' if len(conf_seen) <= 3 else '连续取值'}")
    if applied:
        amean = float(np.mean(applied))
        near_nominal = abs(amean - SC) < 0.12
        near_unity = abs(amean - 1.0) < 0.12
        print("VERDICT-SCALE-APPLIED:", f"实测放大因子={amean:.3f} 标称 scale={SC} → "
              f"{'≈标称（合成器按 scale 拉伸了我方缓冲）' if near_nominal and not near_unity else ('≈1.0（我方缓冲未被拉伸）' if near_unity else '两者都不像')}",
              f"(各位置={[round(v,2) for v in applied]})")
    else:
        print("VERDICT-SCALE-APPLIED: NO-DATA (所有位置峰值过低，量具在该配置下不可用)")
    res_log = []
    for (n, (x, y)), d in zip(pos, ms):
        if not d or d.get("peak", -1) <= 0.5:
            res_log.append((n, None, None))
            continue
        pred_logical = ((x + PATTERN_W / 2) * SC, (y + PATTERN_H / 2) * SC)
        pred_physical = (x + PATTERN_W / 2, y + PATTERN_H / 2)
        res_log.append((n, d["cx"] - pred_logical[0], d["cy"] - pred_logical[1]))
        res_log.append((n + "(vs物理假设)", d["cx"] - pred_physical[0], d["cy"] - pred_physical[1]))
    have = [r for r in res_log if r[1] is not None]
    if have:
        lg = [abs(r[1]) + abs(r[2]) for r in have if "vs物理假设" not in r[0]]
        ph = [abs(r[1]) + abs(r[2]) for r in have if "vs物理假设" in r[0]]
        print("VERDICT-MAP        :",
              f"逻辑假设中位绝对残差={np.median(lg):.1f}px max={max(lg):.1f}px" if lg else "逻辑假设=NO-DATA",
              f"{' 物理假设中位=' + format(np.median(ph), '.1f') + 'px max=' + format(max(ph), '.1f') + 'px' if ph else ''}",
              sep="")
        for n, rx, ry in have:
            print(f"    {n:<26} 残差=({rx:+.1f},{ry:+.1f})")
    else:
        print("VERDICT-MAP        : NO-DATA")
    fac = []
    # **单位**：`estimate()` 返回的是**标定后的逻辑像素**（`crates/fidus-py/src/lib.rs:233`
    # 原文 "Returns (x, y, confidence) in calibrated logical"），而本探针的 cx/cy 是 grim 帧里的
    # **物理像素**。直接相减在 scale≠1 时会得到 (scale−1)×中心 的假残差（1.25 下 400 px 量级），
    # 那会被读成"fidus 不准"。故先把 fidus 读数乘 SC 换算到物理口径再比。
    for (n, _), d in zip(pos, ms):
        settled = (results.get(n) or {}).get("settled")
        if settled and d and d.get("peak", -1) > 0.5:
            fac.append((n, settled[0] * SC - d["cx"], settled[1] * SC - d["cy"]))
    if fac:
        print("VERDICT-FIDUS-ACC  :", f"fidus 中位读数 − 独立实测：最大 |Δ|="
              f"{max(max(abs(a), abs(b)) for _, a, b in fac):.1f}px"
              f"（已把 fidus 的逻辑读数 ×{SC} 换算到物理像素）")
        for n, a, b in fac:
            print(f"    {n:<20} Δ=({a:+.1f},{b:+.1f})")
    else:
        print("VERDICT-FIDUS-ACC  : NO-DATA")
    print(f"VERDICT-TOOL       : {'PASS' if tool_ok else 'FAIL'} (自证峰值 NCC={m['peak']:.4f})")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
