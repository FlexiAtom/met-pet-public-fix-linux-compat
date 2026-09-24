#!/usr/bin/env python3
"""H3 探针：**meapet 真实渲染帧**在 fidus 里到底能不能当模板用。

出处：meapet 定位线 `~/.Athena/projects/meapet/working/fidus-self-window-positioning.md`
§12 门槛清单第三项。fidus 回执（`projects/meapet/pool/fidus-a10-disposition.md`）把它写成
"**H3**（meapet Live2D 帧是否 fidus 可定位 + 逐动画帧重注册成本）——**未跑**"。本探针跑它。

为什么不能只问"register 成不成功"（本探针存在的技术理由）
----------------------------------------------------------
`register_target` 的判决是**二值**的（接受 / `FidusUntrackable`）。二值判决在这道题上不够用：
桌宠帧的自相似度可能落在 0.979（通过，但离悬崖一步）或 0.02（非常好），二者对"这条路能不能
做成产品"是完全不同的结论，而 `register_target` 给的是同一个"成功"。故本探针：

1. **复刻** fidus 的 `template::localizability`（半径 [2,4,8,16]、四个方向、重叠区 NCC、
   Rec.709 亮度、`MAX_SELF_SIMILARITY=0.98`、`near>0.9 且 near-far<0.1` 的衰减规则），
   得到**连续数值**；
2. 用 fidus **自己的** `register_target` 判决**校验这条复刻链**（合成样例 + 真实帧，
   复刻判决必须与 fidus 判决一致，否则第 1 步的数字不作数）；
3. 再量 fidus 侧**结构性看不见**的东西：`luma_at()` 丢弃 alpha
   （`0.2126R+0.7152G+0.0722B`，第 4 通道解构后直接 `_` 忽略）⇒ 透明像素按 RGB 计亮度。
   桌宠帧大部分是透明的，所以"模板说什么"与"屏幕上有什么"在透明区**必然不一致**——
   这不是精度问题，是口径问题，只有把**同一帧**分别按四种喂法投到真机截屏上比峰值才量得出。

四条独立 VERDICT（不合并、不互相顶替）
-------------------------------------
* `REPLICA`  复刻链是否可信：合成样例 + 真实帧上，复刻判决 == fidus 判决。
* `TARGET`   真实桌宠帧是否可注册（四种喂法各自的自相似度数值 + 距离 0.98 悬崖多远）。
* `FALSELOCK` 桌面上**没有**桌宠时，该模板在全图搜索的峰值（对照 fidus 文档里
  "~0.35 即可用"的门槛）——误锁风险不由"看起来像不像"决定，由这个数决定。
* `RR-COST`  逐动画帧重注册的成本：墙钟时间 + **机制代价**（`FusedEstimator::register_target`
  会 `has_fix=false`、清 `differ`/`last_bbox`/`lost_streak` ⇒ 每次重注册都退回重新捕获）。

跑法
----
    # 快速档（零 fidus、零合成器、零桌面副作用）：几何 + 复刻数值
    WAYLAND_DISPLAY=wayland-1 .venv/bin/python \
        scripts/fidus_positioning_probe/probe_h3_targetability.py --no-fidus
    # 完整档（真桌面：grim 截屏、fidus build_wayland 的 4 次分类捕获、贴一个真实桌宠窗口）
    WAYLAND_DISPLAY=wayland-1 .venv/bin/python \
        scripts/fidus_positioning_probe/probe_h3_targetability.py --on-screen

副作用（完整档）：连真实合成器、在屏幕上**显示一个真实桌宠窗口**（探针退出即关闭）、
若干次整幅 grim 截屏（只读，落 /tmp 且随即删除）。**不做 calibrate_once / estimate**
（那才会在桌面上投标定标记），所以本探针不需要、也不申请那一级桌面改动。
退出码：0 = 测完（数值再难看也是 0，这是量具不是门禁）；1 = 量具自身不可用。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
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
sys.path.insert(0, str(HERE))          # 复用 A10 量具
sys.path.insert(0, str(REPO_ROOT))     # 直载 meapet 包（不复制它的几何算法，用真的那份）

os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

import probe_a10_geometry as A10  # noqa: E402  复用已自证的量具（grim + 多尺度 NCC）

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QImage, QSurfaceFormat  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

# ─────────────────────────────────────────────  fidus 判决的复刻常数
# 全部取自 /home/flexiatom/work/fidus@8684b72 crates/fidus-estimate/src/template.rs。
# 复刻的目的不是替代 fidus 判决（第 2 步用它校验这条链），而是把二值判决变成连续读数。
RADII = (2, 4, 8, 16)
MAX_SELF_SIMILARITY = 0.98
MIN_SELF_SIMILARITY_DECAY = 0.1
MIN_VARIANCE_PER_SAMPLE = 0.25
LOCK_THRESHOLD = 0.35  # template.rs:22 注释："Above ~0.35 is a usable measurement"


def fact(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


def luma709(rgb: np.ndarray) -> np.ndarray:
    """fidus `RgbaImage::luma_at` 的口径：Rec.709 于**原始 RGB**，alpha 不参与。

    用 float32 是为了和 Rust 侧 `0.2126 * r as f32 + ...` 逐位同量级；复刻链的
    可信性靠 `REPLICA` 那条判决一致性证明，不靠"看起来一样"。
    """
    f = rgb.astype(np.float32)
    return (0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]).astype(np.float32)


def _self_ncc(plane: np.ndarray, dx: int, dy: int):
    """`self_ncc` 的复刻：重叠区两半的 NCC；任一侧太扁则 None（拒绝而非制造分数）。"""
    h, w = plane.shape
    x0, x1 = max(-dx, 0), min(w, w - dx)
    y0, y1 = max(-dy, 0), min(h, h - dy)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    a = plane[y0:y1, x0:x1].astype(np.float64)
    b = plane[y0 + dy:y1 + dy, x0 + dx:x1 + dx].astype(np.float64)
    n = float(a.size)
    ma, mb = a.mean(), b.mean()
    ac, bc = a - ma, b - mb
    num = float((ac * bc).sum())
    da = float((ac * ac).sum())
    db = float((bc * bc).sum())
    if da < MIN_VARIANCE_PER_SAMPLE * n or db < MIN_VARIANCE_PER_SAMPLE * n:
        return None
    return num / (da ** 0.5 * db ** 0.5)


def localizability(rgba: np.ndarray) -> tuple[str, float | None, list[float]]:
    """复刻 `template::localizability`。返回 (verdict, worst, per_radius)。

    verdict ∈ {"ok", "too_small", "featureless", "self_similar"}——字符串与
    `EstimateError::UntrackableTarget{reason}` 的三个 reason 一一对应，
    好让 `REPLICA` 那条能逐字比对，而不是比对一个我随手起的名字。
    """
    h, w = rgba.shape[:2]
    if w <= RADII[0] * 2 or h <= RADII[0] * 2:
        return "too_small", None, []
    plane = luma709(rgba[..., :3])
    samp = plane.reshape(-1).astype(np.float64)
    if samp.size < 4:
        return "featureless", None, []
    norm2 = float(((samp - samp.mean()) ** 2).sum())
    if norm2 < MIN_VARIANCE_PER_SAMPLE * float(samp.size):
        return "featureless", None, []

    by_radius: list[float] = []
    for r in RADII:
        vals = [v for (dx, dy) in ((r, 0), (0, r), (r, r), (r, -r))
                if (v := _self_ncc(plane, dx, dy)) is not None]
        if vals:
            by_radius.append(max(vals))
    if not by_radius:
        return "too_small", None, []
    worst = max(by_radius)
    if worst >= MAX_SELF_SIMILARITY:
        return "self_similar", worst, by_radius
    near, far = by_radius[0], by_radius[-1]
    if near > 0.9 and near - far < MIN_SELF_SIMILARITY_DECAY:
        return "self_similar", near, by_radius
    return "ok", min(max(worst, 0.0), 1.0), by_radius


# ─────────────────────────────────────────────  五种喂法（透明区怎么处理 + 换成小块）
FEEDS = ("raw", "over_black", "over_wallpaper", "alpha_crop", "patch")
# 整窗被拒后第一个要问的问题："模板换成一小块有没有救"。尺寸必须**事先定死**，
# 不能在帧上事后挑"最像的那块"——那是挑结论，不是量结论。
PATCH_SIZES = (96, 128, 160, 200, 240)
PATCH_FEED_SIZE = 160


def patch_box(rgba: np.ndarray, size: int):
    """以**不透明像素质心**为中心的内接 size×size 盒（越界则内推）→ (x0, y0, size)。

    取块规则只依赖 alpha 通道（不依赖 RGB、不依赖 fidus 任何读数），因而可独立复算；
    质心随动画漂移量在下面单独报，因为它就是"补丁中心 → 窗口中心用常量换算"这条
    补救路线的**误差项本身**。
    """
    h, w = rgba.shape[:2]
    if size > min(h, w):
        return None
    mask = rgba[..., 3] > 8
    if mask.any():
        ys, xs = np.where(mask)
        cx, cy = int(xs.mean()), int(ys.mean())
    else:
        cx, cy = w // 2, h // 2
    return max(0, min(cx - size // 2, w - size)), max(0, min(cy - size // 2, h - size)), size


def center_patch(rgba: np.ndarray, size: int) -> np.ndarray:
    box = patch_box(rgba, size)
    if box is None:
        raise ValueError(f"补丁 {size}×{size} 放不下 {rgba.shape[1]}x{rgba.shape[0]}")
    x0, y0, s = box
    return np.ascontiguousarray(rgba[y0:y0 + s, x0:x0 + s])


def patch_offset(rgba: np.ndarray, size: int):
    """补丁中心相对**整窗中心**的偏移（fidus 报的是补丁中心，换算回窗口中心要减它）。"""
    box = patch_box(rgba, size)
    if box is None:
        return None
    x0, y0, s = box
    h, w = rgba.shape[:2]
    return (x0 + s / 2 - w / 2, y0 + s / 2 - h / 2)


def make_feed(rgba: np.ndarray, bg: np.ndarray | None, variant: str) -> np.ndarray:
    """把一帧 RGBA 变成将交给 `register_target` 的模板缓冲。

    `raw`        原样（= fidus 实际会看到的：透明区保留其 RGB，GL 清屏后通常是全 0）
    `over_black` 显式合成到黑（与 raw 在 GL 帧上应当几乎一致——这条是 raw 的对照）
    `over_wallpaper` 合成到**该窗口当时屏幕背后真实那块壁纸**（把透明区的亮度口径修对）
    `alpha_crop` 先裁到 alpha>0 的外接框再原样保留（把透明边距整块去掉）
    `patch`      取质心内接 `PATCH_FEED_SIZE`² 小块（透明区根本不在模板里）
    """
    if variant == "raw":
        return np.ascontiguousarray(rgba)
    if variant == "patch":
        return center_patch(rgba, PATCH_FEED_SIZE)
    if variant == "over_black":
        out = rgba.copy()
        out[out[..., 3] < 8] = 0
        return np.ascontiguousarray(out)
    if variant == "over_wallpaper":
        if bg is None or bg.shape[:2] != rgba.shape[:2]:
            raise ValueError(f"over_wallpaper 需要同尺寸背景，得到 {None if bg is None else bg.shape}")
        a = (rgba[..., 3:4].astype(np.float32) / 255.0)
        out = (rgba[..., :3].astype(np.float32) * a + bg[..., :3].astype(np.float32) * (1 - a))
        merged = np.dstack([out.round().astype(np.uint8), np.full(rgba.shape[:2], 255, np.uint8)])
        return np.ascontiguousarray(merged)
    if variant == "alpha_crop":
        mask = rgba[..., 3] > 8
        if not mask.any():
            return np.ascontiguousarray(rgba)
        ys, xs = np.where(mask)
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        return np.ascontiguousarray(rgba[y0:y1, x0:x1])
    raise ValueError(variant)


def alpha_stats(rgba: np.ndarray) -> dict:
    a = rgba[..., 3]
    mask = a > 8
    out = {"coverage": round(float(mask.mean()), 4), "opaque_px": int(mask.sum())}
    if mask.any():
        ys, xs = np.where(mask)
        out["bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    return out


# ─────────────────────────────────────────────  平滑度控制（诊断"为什么必然 0.98"）
def box_blur(plane: np.ndarray, r: int) -> np.ndarray:
    """二维盒式模糊（前缀和，无 scipy）。只用于造"更平滑 / 更锐"的对照样本。

    边界按 edge 复制处理：这是对照样本发生器，不是产品代码，内部像素才是它要量的。
    """
    if r <= 0:
        return plane.copy()
    k = 2 * r + 1
    out = plane.astype(np.float32)
    for axis in (0, 1):
        pad = [[0, 0], [0, 0]]
        pad[axis] = [k - 1, 0]
        b = np.pad(out, pad, mode="edge")
        z = np.pad(b, [[1, 0], [0, 0]] if axis == 0 else [[0, 0], [1, 0]], constant_values=0)
        c = np.cumsum(z, axis=axis)
        hi = [slice(None), slice(None)]
        lo = [slice(None), slice(None)]
        hi[axis] = slice(k, k + out.shape[axis])
        lo[axis] = slice(0, out.shape[axis])
        out = ((c[tuple(hi)] - c[tuple(lo)]) / float(k)).astype(np.float32)
    return out


def unsharp(rgba: np.ndarray, amount: float, r: int = 2) -> np.ndarray:
    """非锐化掩模：只改亮度高频，不改结构位置。模板是**离屏**喂给 fidus 的，
    屏幕上看不到它 ⇒ 这一类处理若能过门，产品侧零视觉代价。"""
    out = rgba.copy()
    for ch in range(3):
        plane = rgba[..., ch].astype(np.float32)
        hi = plane - box_blur(plane, r)
        out[..., ch] = np.clip(plane + amount * hi, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(out)


def blur_rgba(rgba: np.ndarray, amount: float, r: int = 2) -> np.ndarray:
    """与 `unsharp` 反向的对照：把高频抹掉一点，看判决往哪边走。"""
    out = rgba.copy()
    for ch in range(3):
        p = rgba[..., ch].astype(np.float32)
        out[..., ch] = np.clip(p * (1 - amount) + box_blur(p, r) * amount, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(out)


# ─────────────────────────────────────────────  fidus 真判决
def fidus_register(fobj, rgba: np.ndarray, ambiguous: bool = False) -> tuple[str, float]:
    """跑一次 `register_target`，返回 (判决串, 墙钟秒)。判决串**不翻译**，直接是异常类名或 ok。"""
    t0 = time.perf_counter()
    try:
        fobj.register_target(np.ascontiguousarray(rgba), ambiguous)
        return "ok", time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001  这里要的就是"到底抛哪一个"
        msg = str(exc).replace("\n", " ")[:140]
        return f"{type(exc).__name__}: {msg}", time.perf_counter() - t0


def wheel_identity() -> str:
    try:
        import fidus
    except Exception as exc:  # noqa: BLE001
        return f"import-fail {type(exc).__name__}"
    so = getattr(getattr(fidus, "fidus", None), "__file__", "")
    digest = "n/a"
    try:
        digest = hashlib.sha256(Path(so).read_bytes()).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        pass
    return f"{getattr(fidus, '__version__', '?')} so={Path(so).name}@{digest}"


# ─────────────────────────────────────────────  真实桌宠帧
def render_live2d_frames(app: QApplication, n_frames: int) -> tuple[list[np.ndarray], dict, object, object]:
    """按**产品自己的几何算法**挂一个真实 Live2D 窗口，取 n_frames 帧 RGBA。

    刻意不复制一份"差不多"的渲染：画布尺寸走 `PetRenderHostMixin._live2d_base_size`
    （本模型上它落到 1024×1024 默认值——`GetCanvasSize()` 实测返回占位 (1,2)，
    产品自己的范围校验把它判为无效），视口走 `calculate_live2d_viewport_layout`
    （与 `_live2d_viewport_layout` 同一个函数），帧走控件自带的 `render_offscreen()`
    （与 `paintGL` 同一个 `_draw_model`）。
    """
    from meapet.config.store import load_config, resolve_resource_path
    from meapet.desktop.live2d_widget import Live2DModel, init_live2d
    from meapet.desktop.render_host import (
        PetRenderHostMixin, calculate_live2d_viewport_layout)

    cfg = load_config()
    l2d_cfg = cfg.get("live2d", {}) or {}
    model_dir = resolve_resource_path(l2d_cfg.get("model_dir", ""))
    info: dict = {"model_dir": model_dir, "renderer": "live2d"}
    if not model_dir or not os.path.isdir(model_dir):
        info["renderer"] = f"live2d:模型目录不存在({model_dir!r})"
        return [], info, None, None

    init_live2d()  # 产品里由 render_host 在建模型前调用；缺它 native LAppModel() 直接段错误
    model = Live2DModel(model_dir)
    host = QWidget()
    host.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    host.setAttribute(Qt.WA_TranslucentBackground, True)
    widget = model.create_widget(host)

    shim = object.__new__(PetRenderHostMixin)  # 只要 config 与 _l2d_model 两个字段
    shim.config = cfg
    shim._l2d_model = model
    factor = (cfg.get("display", {}) or {}).get("size_factor", 1.0)
    crop_box: list[int] = [0, 0, 0, 0]

    def apply_layout(stage: str) -> None:
        """按产品算法算一次几何并摆好父子窗口；`stage` 只用于把两次结果分开报出来。

        产品会算**两次**：启动时模型未加载（`get_suggested_size()` 给占位 525×735），
        首帧后再算一次（模型已加载，走它自己的回退链 ⇒ 1024×1024）。几何因此不同，
        模板尺寸也不同——只报其中一次就会把"产品最终显示的那张"认错，故两段都留痕。
        """
        nonlocal crop_box
        canvas = PetRenderHostMixin._live2d_base_size(shim)
        layout = calculate_live2d_viewport_layout(
            canvas[0], canvas[1], factor, l2d_cfg.get("window_mask"))
        widget.resize(layout.widget_width, layout.widget_height)
        widget.move(layout.widget_x, layout.widget_y)
        host.resize(layout.window_width, layout.window_height)
        crop_box = [max(0, -layout.widget_x), max(0, -layout.widget_y),
                    layout.window_width, layout.window_height]
        info[f"layout_{stage}"] = {
            "canvas": list(canvas), "factor": factor,
            "widget": [layout.widget_width, layout.widget_height],
            "window": [layout.window_width, layout.window_height],
            "offset": [layout.widget_x, layout.widget_y],
        }

    apply_layout("startup")
    host.show()
    deadline = time.time() + 25
    while time.time() < deadline:
        app.processEvents()
        if getattr(widget, "_ready", False):
            break
        time.sleep(0.02)
    info["ready"] = bool(getattr(widget, "_ready", False))
    if not info["ready"]:
        return [], info, host, widget
    apply_layout("first_frame")

    def pump(ms: int) -> None:
        end = time.time() + ms / 1000.0
        while time.time() < end:
            app.processEvents()
            time.sleep(0.002)

    def window_frame() -> np.ndarray | None:
        """顶层窗口的合成帧 = 画布 FBO 帧按产品视口裁出的那块（与合成器显示的一致）。"""
        img = widget.render_offscreen()
        if img is None or img.isNull():
            return None
        if img.format() != QImage.Format_RGBA8888:
            img = img.convertToFormat(QImage.Format_RGBA8888)
        ratio = img.devicePixelRatio()
        if ratio > 1.0:
            img = img.scaled(int(img.width() / ratio), int(img.height() / ratio))
        w, h = img.width(), img.height()
        buf = img.constBits()
        buf.setsize(h * w * 4)
        full = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        # 显式 copy：constBits() 只是 Qt 内存的窗口，img 一析构这个视图就悬垂
        crop_left, crop_top, win_w, win_h = crop_box
        x0 = min(crop_left, w - 1)
        y0 = min(crop_top, h - 1)
        x1 = min(x0 + win_w, w)
        y1 = min(y0 + win_h, h)
        return np.ascontiguousarray(full[y0:y1, x0:x1].copy())

    frames: list[np.ndarray] = []
    for _ in range(n_frames):
        arr = window_frame()
        if arr is None:
            break
        frames.append(arr)
        pump(120)
    # 记成 (宽, 高)：numpy 的 shape 是 (行, 列)，直接抄出去会和 Qt/合成器的 (w,h) 反着对上
    info["window_px"] = [frames[0].shape[1], frames[0].shape[0]] if frames else None
    info["grab"] = window_frame  # 真机段要"与截屏同一冻结帧"的模板，闭包留着给它
    return frames, info, host, widget


_ORIG_FOCUS: dict = {}   # 探针开窗**之前**用户聚焦的是谁，main() 一进来就记，见 on_screen_stage


def main() -> int:
    ap = argparse.ArgumentParser(description="H3: meapet 真实帧的 fidus 可定位性")
    ap.add_argument("--frames", type=int, default=6, help="取几帧动画（默认 6）")
    ap.add_argument("--no-fidus", action="store_true", help="只跑复刻档，不连合成器")
    ap.add_argument("--on-screen", action="store_true", help="加跑真机截屏对比 + 误锁峰值")
    ap.add_argument("--no-float", action="store_true",
                    help="真机段不要 niri 浮动窗口（默认会要：平铺会把桌宠改尺寸）")
    ap.add_argument("--timing-repeats", type=int, default=25, help="重注册计时重复次数")
    ap.add_argument("--tag", default="h3", help="grim 临时文件名前缀")
    args = ap.parse_args()

    print("== 环境 ==")
    fact("python", f"{sys.version.split()[0]} {sys.executable}")
    fact("fidus", wheel_identity())
    outs = A10.niri_outputs()
    fact("niri outputs", json.dumps(outs, ensure_ascii=False)[:220])
    # 此刻还没有任何探针窗口存在 ⇒ 这是"用户原本在看谁"，真机段要把焦点还给它。
    _orig = next((w for w in _windows_json() if w.get("is_focused")), {})
    _ORIG_FOCUS.update({"id": _orig.get("id"), "title": _orig.get("title")})
    fact("开窗前聚焦", json.dumps(_ORIG_FOCUS, ensure_ascii=False))
    try:
        import fidus as fidus_mod
        has_fidus = True
    except Exception:  # noqa: BLE001
        has_fidus = False
    if args.no_fidus:
        has_fidus = False

    # 与 app.py:820-828 同一前置：QApplication 之前请求 alpha+stencil，
    # 否则 QOpenGLWidget 拿不到 Live2D 遮罩缓冲需要的 stencil ⇒ 量的就不是产品那条渲染路径。
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    fmt.setStencilBufferSize(8)
    fmt.setRenderableType(QSurfaceFormat.OpenGL)
    QSurfaceFormat.setDefaultFormat(fmt)
    # 先连 fidus：`build_wayland` 要连合成器并做 4 次分类截屏（~0.65s）。放在 Live2D 已初始化
    # 之后连，本机实测在 Cubism 的 GL 上下文下段错误 ⇒ 探针会"量不到"而不是"量出坏值"，
    # 这是顺序敏感的真机事实，不是猜测（见 §12f）。
    # Live2D 的 native 初始化必须先于建控件所属进程的任何渲染：漏掉 `init_live2d()`
    # （即 `live2d.init()`）时 `live2d.LAppModel()` 在 initializeGL 里**直接段错误**，
    # 不是抛异常（本机实测）。fidus 的连接与之无先后依赖，故留在下面按需再连。
    app = QApplication(sys.argv[:1])
    print("\n== 真实桌宠帧 ==")
    frames, rinfo, host, widget = render_live2d_frames(app, args.frames)
    fact("渲染路径", f"{rinfo.get('renderer')} ready={rinfo.get('ready')}")
    fact("几何·启动时", json.dumps(rinfo.get("layout_startup", {}), ensure_ascii=False))
    fact("几何·首帧后", json.dumps(rinfo.get("layout_first_frame", {}), ensure_ascii=False)
         + "（模板取这一次，与产品最终显示一致）")
    if not frames:
        print("[h3] ✗ 拿不到任何真实帧 ⇒ 量具不可用", flush=True)
        return 1
    fact("帧数", len(frames))
    fact("帧 0 alpha 统计", json.dumps(alpha_stats(frames[0]), ensure_ascii=False))
    uniq = {hashlib.sha256(f.tobytes()).hexdigest()[:10] for f in frames}
    fact("帧内容去重", f"{len(uniq)}/{len(frames)} 个不同哈希 ⇒ "
         f"{'动画确实在改帧（重注册问题成立）' if len(uniq) > 1 else '帧全同（动画未推进）'}")

    # ---------------------------------------------------------- 复刻链自证
    print("\n== REPLICA：复刻链 vs fidus 真判决（合成样例先行，已知答案） ==")
    rng = np.random.default_rng(7)
    synth = {
        "linear_gradient": np.dstack([np.tile(np.linspace(0, 255, 60, dtype=np.uint8), (40, 1))
                                     for _ in range(3)]
                                    + [np.full((40, 60), 255, np.uint8)]),
        "solid_block+border": _solid_with_border(40, 60),
        "hash_texture": np.dstack([rng.integers(0, 256, (40, 60), dtype=np.uint8)
                                   for _ in range(3)]
                                  + [np.full((40, 60), 255, np.uint8)]),
        "flat": np.full((40, 60, 4), 128, np.uint8),
        "too_small": np.full((3, 3, 4), 200, np.uint8),
    }
    agree = disagree = 0
    for name, arr in synth.items():
        verdict, worst, per = localizability(arr)
        line = f"  {name:22s} 复刻={verdict:14s} worst={_fmt(worst)} per_r={_fmt_list(per)}"
        if has_fidus:
            got, dt = fidus_register(_shared_fidus(), arr)
            want = "ok" if verdict == "ok" else "FidusUntrackable"
            mark = "一致" if got.startswith(want) else "不一致"
            agree += mark == "一致"
            disagree += mark != "一致"
            line += f" | fidus={got[:40]} ⇒ {mark}"
        print(line, flush=True)
    print(f"  合成样例复刻一致率: {agree}/{agree + disagree}"
          f"{'（复刻数值尚不可信，下面真实帧的数值只作相对比较）' if disagree else ''}",
          flush=True)

    print("\n== TARGET：真实帧可注册性（over_wallpaper 需要真机背景，见下方真机段） ==")
    shared = _shared_fidus() if has_fidus else None
    static_variants = ("raw", "over_black", "alpha_crop")
    for fi, frame in enumerate(frames):
        for variant in static_variants:
            tpl = make_feed(frame, bg=None, variant=variant)
            verdict, worst, per = localizability(tpl)
            line = (f"  f{fi} {variant:13s} size={tpl.shape[1]}x{tpl.shape[0]:<5d} "
                    f"复刻={verdict:13s} worst={_fmt(worst)} per_r={_fmt_list(per)}")
            if shared is not None:
                fid, dt = fidus_register(shared, tpl)
                line += f" fidus={fid[:46]} ({dt * 1000:.1f}ms)"
            print(line, flush=True)

    # ---------------------------------------------------------- 最小改动
    print("\n== PATCH：把模板换成质心小块，最小要多大才过 0.98 门 ==")
    first_ok_size = None
    for size in PATCH_SIZES:
        worsts: list[float | None] = []
        rejects: list[str] = []
        offs: list[tuple[float, float]] = []
        ms: list[float] = []
        ok_n = 0
        skipped = ""
        for frame in frames:
            try:
                tpl = center_patch(frame, size)
            except ValueError as exc:
                skipped = f"跳过：{exc}"
                break
            verdict, worst, _per = localizability(tpl)
            worsts.append(worst)
            if verdict != "ok":
                rejects.append(verdict)
            off = patch_offset(frame, size)
            if off is not None:
                offs.append(off)
            if shared is not None:
                got, dt = fidus_register(shared, tpl)
                ms.append(dt * 1000.0)
                ok_n += got == "ok"
        if skipped:
            fact(f"patch {size}", skipped)
            continue
        all_ok = not rejects and all(
            w is not None and w < MAX_SELF_SIMILARITY for w in worsts)
        if all_ok and first_ok_size is None:
            first_ok_size = size
        line = (f"patch {size:3d}² worst=[{' '.join(_fmt(w) for w in worsts)}]"
                f"{' 全部可定位' if all_ok else ' 仍有帧被拒 ' + str(sorted(set(rejects)))}")
        if shared is not None:
            line += (f" | fidus ok={ok_n}/{len(ms)}"
                     f" 中位={np.median(ms):.2f}ms")
        if offs:
            dxs = [o[0] for o in offs]
            dys = [o[1] for o in offs]
            line += (f" | 补丁中心−窗心=({np.mean(dxs):+.0f},{np.mean(dys):+.0f})px"
                     f" 跨帧漂移=({np.ptp(dxs):.0f},{np.ptp(dys):.0f})px")
        known = [w for w in worsts if w is not None]
        if known:
            # 复刻最坏值直接套 fidus 的封顶公式（lib.rs:152-155）：接受 ≠ 可用。
            line += f" | confidence 上限={max(1.0 - max(known), 0.05):.3f}"
        fact("", line)
    fact("最小全通过尺寸", str(first_ok_size) + ("（None ⇒ 这些档位都没救，整窗拒绝不是尺寸问题）"
                                                  if first_ok_size is None else ""))

    # ---------------------------------------------------------- 唯一被接受的入口
    if shared is not None:
        print("\n== AMBIGUOUS：整窗只在 ambiguous=True 时被收，代价是置信度上限 ==")
        for variant in ("raw", "alpha_crop", "patch"):
            tpl = make_feed(frames[0], bg=None, variant=variant)
            _v, worst, _per = localizability(tpl)
            got, dt = fidus_register(shared, tpl, ambiguous=True)
            w = 0.0 if worst is None else max(0.0, min(1.0, worst))
            ceiling = max(1.0 - w, 0.05)  # fidus-estimate/src/lib.rs:152-164
            fact(f"{variant} ambiguous=True",
                 f"{got[:40]} ({dt * 1000:.1f}ms) ⇒ confidence 上限={ceiling:.3f}")
        fact("上限的含义（fidus 自述，lib.rs:157-163）",
             "MIN_CONFIDENCE_CEILING=0.05 的设计意图就是让 L7 融合『让位给任何其他层』、"
             "且让按 confidence 取阈的调用方直接拒掉 ⇒ 0.05 不是'弱一点'，是'别用它'")
        fact("源码事实", "confidence_ceiling 只在 Rust 侧 pub（lib.rs:178-186），"
             "fidus-py 未暴露 getter ⇒ Python 侧看不出这次被封顶到多少，只能从返回的 confidence 反推")

    # ---------------------------------------------------------- 通则控制
    print("\n== 控制：0.98 这条门是不是'任何自然图像'的通病（而非桌宠特例） ==")
    tex = REPO_ROOT / "live2d/model/mea_live2d/textures/texture_00.png"
    if tex.is_file():
        img = np.asarray(Image.open(tex).convert("RGB"), dtype=np.uint8)
        th, tw = img.shape[:2]
        for i, (cx, cy) in enumerate([(tw // 4, th // 4), (tw // 2, th // 2), (tw * 3 // 4, th // 4)]):
            crop = np.dstack([img[cy - 80:cy + 80, cx - 80:cx + 80],
                              np.full((160, 160), 255, np.uint8)])
            v, worst, per = localizability(crop)
            ceil = "—" if worst is None else f"{max(1.0 - worst, 0.05):.3f}"
            line = (f"  美术原图裁块#{i} @{cx},{cy} 复刻={v:13s} worst={_fmt(worst)} "
                    f"per_r={_fmt_list(per)} confidence 上限={ceil}")
            if shared is not None:
                got, dt = fidus_register(shared, crop)
                line += f" fidus={got[:38]}"
            print(line, flush=True)
    else:
        print(f"  美术原图不存在（{tex}），跳过", flush=True)

    print("  -- 同一帧只改高频成分，看判决往哪边走（半径 2 的 NCC 量的到底是什么） --")
    for tagv, fn in (("锐化", unsharp), ("模糊", blur_rgba)):
        for amount in ((0.5, 1.0, 2.0, 4.0) if tagv == "锐化" else (0.25, 0.5, 1.0)):
            tpl = fn(frames[0], amount)
            v, worst, per = localizability(tpl)
            ceil = "—" if worst is None else f"{max(1.0 - worst, 0.05):.3f}"
            line = (f"  {tagv} ×{amount:<4} 复刻={v:13s} worst={_fmt(worst)} "
                    f"per_r={_fmt_list(per)} confidence 上限={ceil}")
            if shared is not None:
                got, dt = fidus_register(shared, tpl)
                line += f" fidus={got[:30]} ({dt * 1000:.0f}ms)"
            print(line, flush=True)
    fact("读法", "worst 全来自**半径 2**（per_r 第一项），量的其实是'平坦区占多少'：模糊↑分数↑、"
         "锐化↓分数↓是单调的，而半径 2 的平移歧义要看在最大半径上是否仍≈1（这里 r=16 已经掉到 0.74-0.89）")
    fact("读法·更正", "先前这里写的是'自然图像在 2px 位移下本来就近似自同'——**已被上面的美术裁块"
         "控制证伪**：同一张美术原图裁 512² 三块 worst=0.941/0.961/0.965，全部过门。"
         "桌宠整窗被拒不是'自然图像都这样'，是它 78% 的像素是 alpha=0 的平坦边距")

    # ---------------------------------------------------------- 重注册成本
    if shared is not None:
        print("\n== RR-COST：逐动画帧重注册 ==")
        times: list[float] = []
        ok_n = 0
        for rep in range(args.timing_repeats):
            tpl = make_feed(frames[rep % len(frames)], bg=None, variant="raw")
            got, dt = fidus_register(shared, tpl)
            times.append(dt)
            ok_n += got == "ok"
        ms = np.array(times) * 1000.0
        fact(f"{len(times)} 次重注册（真实帧轮转）",
             f"ok={ok_n} 中位={np.median(ms):.2f}ms p95={np.percentile(ms, 95):.2f}ms "
             f"max={ms.max():.2f}ms ⇒ 30fps 预算(33.3ms)占比 "
             f"{np.median(ms) / 33.3 * 100:.1f}%")
        fact("机制代价（源码事实，非测量）",
             "FusedEstimator::register_target 置 has_fix=false 并清 differ/last_bbox/lost_streak "
             "⇒ 每次重注册都退回『重新捕获』；在此之前 estimate() 走 !has_fix 分支 = TargetLost 异常")

    # ---------------------------------------------------------- 真机对比
    if args.on_screen:
        # 整段输出**缓冲后一次写**：探针就跑在 kitty 窗口里，边抓屏边打印会滚动终端，
        # 而那些滚动正好落进"有宠 vs 无宠"的差集里（本机实测差集 52 万 px、真值盒铺满
        # 全屏）。缓冲之后，采集期间屏幕上的文字一像素都不动。
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                on_screen_stage(app, host, widget, frames, shared, rinfo, args.tag,
                                want_float=not args.no_float)
        finally:
            sys.stdout.write(buf.getvalue())
            sys.stdout.flush()

    print("\n== 收口 ==")
    print("  本探针不改配置、不 commit、截屏落 /tmp 后即删；结论抄写见状态根 §12f。", flush=True)
    return 0


# ------------------------------------------------------------------ 小工具
_FIDUS_CACHE: list = []


def _shared_fidus():
    """整个探针共用一个 fidus 实例：`build_wayland` 要连合成器 + 4 次分类截屏（~0.65s）。"""
    if _FIDUS_CACHE:
        return _FIDUS_CACHE[0]
    import fidus
    t0 = time.perf_counter()
    obj = fidus.Fidus.build_wayland()
    fact("fidus build_wayland", f"{time.perf_counter() - t0:.2f}s → {obj!r}")
    _FIDUS_CACHE.append(obj)
    return obj


# ------------------------------------------------------------------ 真机段
def _diff_box(with_pet: np.ndarray, bg: np.ndarray, anim: np.ndarray | None = None):
    """`with_pet` 与 `bg` 之差的最大连通块外接盒 → (盒|None, keep_px, 说明)。

    刻意不用任何模板去找这个盒——那条链要在下面被模板检验，用它当基准就是循环论证。
    与 A10 的 `cursor_bbox` 同一纪律：不可判就返回 None 并说明，不记成 0。
    """
    if with_pet is None or bg is None:
        return None, 0, "grim 抓帧缺张"
    if with_pet.shape != bg.shape:
        return None, 0, f"帧尺寸不一致 {with_pet.shape} vs {bg.shape}"
    d = np.abs(with_pet.astype(np.int16) - bg.astype(np.int16)).max(axis=2) > 12
    if anim is not None:
        d &= ~anim
    keep = int(d.sum())
    if keep < 400:
        return None, keep, f"差集仅 {keep}px"
    box = A10._largest_component(d)
    if box is None:
        return None, keep, f"差集 {keep}px 但无连通块"
    bx, by, bw, bh, bn = box  # A10 给的是 (左,上,宽,高,px)，这里换成角点盒以免调用方再拆错
    return (bx, by, bx + bw, by + bh), keep, f"差集 {keep}px、最大连通块 {bn}px"


def _changed_frac(a_frame, b_frame, rect, mask: np.ndarray) -> float:
    """两帧在**桌宠自己画过的那些像素**上互相差多少（按模板 alpha>8 的掩模归一）。

    掩模是必须的，不是精度优化：整窗矩形里 78% 是透明区，那部分屏上本来就是桌面，而桌面
    是活着的（本机实测：我这边一刷新终端，不加掩模的"改动数"从 0.32 跳到 0.69，把明明还
    画着桌宠的那张'背景'洗成了干净）。只看桌宠自己的像素，桌面重绘就掺不进来。
    ≈1 ⇒ 桌宠真的不在其中一张里；远小于 1 ⇒ 它还在，拿它当"空背景"算误锁峰就是自欺。
    """
    if a_frame is None or b_frame is None or mask is None or not mask.any():
        return float("nan")
    x, y, w, h = rect if rect else (0, 0, a_frame.shape[1], a_frame.shape[0])
    if (h, w) != mask.shape[:2] or y + h > min(a_frame.shape[0], b_frame.shape[0]) \
            or x + w > min(a_frame.shape[1], b_frame.shape[1]):
        return float("nan")
    d = np.abs(a_frame[y:y + h, x:x + w, :3].astype(np.int16)
               - b_frame[y:y + h, x:x + w, :3].astype(np.int16)).max(axis=2) > 12
    return round(float((d & mask).sum()) / float(mask.sum()), 3)


def _masked_ncc(a: np.ndarray, b: np.ndarray) -> float:
    """两串等长像素的归一化互相关；任一侧无方差则 0.0（不编造分数）。"""
    ac = a - a.mean()
    bc = b - b.mean()
    den = float((ac * ac).sum()) ** 0.5 * float((bc * bc).sum()) ** 0.5
    return float((ac * bc).sum() / den) if den > 1e-6 else 0.0


def _masked_align(template: np.ndarray, frame: np.ndarray, px0: int, py0: int,
                  span: int = 20, step: int = 2, sub: int = 4):
    """手里那一帧 vs 屏上那一块：只按 alpha>8 的不透明像素算掩模 NCC，扫 ±span 偏移。

    `(px0, py0)` 是模板左上角在 `frame` 里的**假设落点**。透明区一律不参与 ⇒
    "屏上峰值上不去"再不能拿"78% 透明区口径"当挡箭牌：
    最佳偏移 ≠(0,0) 说明**探针裁错了**（或 RGB 通道序对不上），屏上数字得先修量具；
    (0,0) 且分数≈1 说明两边同源，口径差才是需要解释的事实。
    `sub` 只是降采样提速，两侧同相位取，不改口径。返回 (最佳分数, dx, dy)。
    """
    h, w = template.shape[:2]
    H, W = frame.shape[:2]
    mask = template[..., 3] > 8
    t = template[..., :3].astype(np.float32)
    f = frame[..., :3].astype(np.float32)
    best, bx, by = -1.0, 0, 0
    for dy in range(-span, span + 1, step):
        for dx in range(-span, span + 1, step):
            r0, c0 = max(0, -(py0 + dy)), max(0, -(px0 + dx))
            r1, c1 = min(h, H - (py0 + dy)), min(w, W - (px0 + dx))
            if r1 - r0 < 8 or c1 - c0 < 8:
                continue
            m = mask[r0:r1, c0:c1][::sub, ::sub]
            if int(m.sum()) < 64:
                continue
            tv = t[r0:r1, c0:c1][::sub, ::sub][m].reshape(-1)
            sr, sc = r0 + py0 + dy, c0 + px0 + dx
            sv = f[sr:r1 + py0 + dy, sc:c1 + px0 + dx][::sub, ::sub][m].reshape(-1)
            score = _masked_ncc(tv.astype(np.float64), sv.astype(np.float64))
            if score > best:
                best, bx, by = float(score), dx, dy
    return best, bx, by


def _niri(action: str) -> str:
    try:
        r = subprocess.run(["niri", "msg", "action", *action.split()],
                           capture_output=True, text=True, timeout=6)
        return (r.stdout + r.stderr).strip()[:120] or "ok"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def _focused_is_pet(expect_w: int, expect_h: int) -> dict:
    """合成器侧**独立**读出当前聚焦窗口的实际落点/尺寸（不经 Qt，不循环论证）。"""
    try:
        raw = subprocess.run(["niri", "msg", "--json", "focused-window"],
                             capture_output=True, text=True, timeout=6).stdout
        d = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    lay = d.get("layout", {})
    return {"title": d.get("title"), "app_id": d.get("app_id"), "floating": d.get("is_floating"),
            "tile_size": lay.get("tile_size"), "window_size": lay.get("window_size"),
            "expect": [expect_w, expect_h]}


def _windows_json() -> list[dict]:
    try:
        raw = subprocess.run(["niri", "msg", "--json", "windows"],
                             capture_output=True, text=True, timeout=6).stdout
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return []


def _win_id(title_part: str) -> object:
    return next((w.get("id") for w in _windows_json()
                 if title_part in str(w.get("title") or "")), None)


def _focused_win_id() -> object:
    return next((w.get("id") for w in _windows_json() if w.get("is_focused")), None)


def _is_focused(win_id) -> bool:
    return bool(next((w.get("is_focused") for w in _windows_json()
                      if w.get("id") == win_id), False))


def _focus_window(win_id) -> str:
    try:
        r = subprocess.run(["niri", "msg", "action", "focus-window", "--id", str(win_id)],
                           capture_output=True, text=True, timeout=6)
        return (r.stdout + r.stderr).strip()[:120] or "ok"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def _ring_style() -> tuple[tuple[int, int, int] | None, int | None, str]:
    """读 niri 配置里的 focus-ring active-color / width（环色由合成器决定，不自己猜）。"""
    cfg = Path.home() / ".config/niri/config.kdl"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError as exc:
        return None, None, f"读不到 {cfg}: {exc}"
    seg = text.split("focus-ring", 1)
    if len(seg) < 2:
        return None, None, "配置里没有 focus-ring 段"
    body = seg[1].splitlines()
    color_line = next((l for l in body if "active-color" in l), "")
    hexv = color_line.split('"')[1] if '"' in color_line else ""
    if len(hexv) < 7:
        return None, None, f"没解析出 active-color（{color_line.strip()!r}）"
    width_line = next((l for l in body if l.strip().startswith("width")), "")
    digits = "".join(c if c.isdigit() else " " for c in width_line).split()
    rgb = tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5))
    return rgb, (int(digits[0]) if digits else None), f"{color_line.strip()} / {width_line.strip()}"


def _ring_px(arr, rgb: tuple[int, int, int], box=None) -> int:
    """帧里"环底色"像素数，box = (x, y, 宽, 高)。用来**证明**屏上没有环，而不是假设没有。"""
    if arr is None:
        return -1
    if box is not None:
        x, y, w, h = box
        arr = arr[y:y + h, x:x + w]
    return int(((np.abs(arr[..., :3].astype(int) - np.array(rgb)) <= 6).all(axis=2)).sum())


def on_screen_stage(app, host, widget, frames, shared, rinfo, tag: str, want_float: bool) -> None:
    """屏幕上当前的桌宠 vs 手里的模板：真机峰、误锁峰、口径差。"""
    print("\n== ON-SCREEN：真机截屏对比 ==")

    def pump(ms: int) -> None:
        end = time.time() + ms / 1000.0
        while time.time() < end:
            app.processEvents()
            time.sleep(0.002)

    ww, wh = rinfo.get("window_px") or (0, 0)
    # 交还焦点的目标必须是**探针开窗之前**聚焦的那个窗口：等走到这里再读，聚焦的已经是
    # 桌宠自己（本机实测 id=87 = 探针窗口），"还回去"就成了把桌宠再聚焦一次。
    pet_id = _win_id("probe_h3")
    back = _ORIG_FOCUS.get("id")
    if back is None or back == pet_id:
        cur = _focused_win_id()
        back = cur if (cur is not None and cur != pet_id) else None
    # 平铺合成器上 show() 会**重排整个桌面**：本机实测桌宠被塞进 659×736 的 tile、
    # 终端同步改尺寸 ⇒ "有宠 vs 无宠"之差铺满全屏，真值盒失去意义。浮动窗口不参与
    # 平铺布局，所以它是这一段的前提而不是优化。
    # 请求浮动**之前**必须先确认聚焦的就是探针自己的窗口：`toggle-window-floating`
    # 作用对象是"当前聚焦窗口"，聚焦弄错就会把用户的终端浮动起来——那是我不该做的改动。
    # 焦点环：niri 把 focus-ring 画成**贴着窗口外扩 width 的实心矩形、且在窗口底下**
    # （config.kdl 自述"会透过半透明窗口的形状显示"）。桌宠一聚焦，它的透明区就透出这块
    # active-color 底色 ⇒ 屏上读数（真值盒、透明区平均色、opacity=0 残留像素）量的会是
    # 合成器画的蓝，不是客户端提交的像素。所以整段要在**失焦**下测，且要证明失焦生效。
    ring_rgb, ring_w, ring_src = _ring_style()
    fact("niri 焦点环", f"active-color={ring_rgb} width={ring_w} ← {ring_src}")
    fact("要交还焦点给", f"id={back} title={_ORIG_FOCUS.get('title')!r} 桌宠 id={pet_id}")
    host.activateWindow()
    host.raise_()
    pump(600)
    before = _focused_is_pet(ww, wh)
    fact("浮动前聚焦", json.dumps(before, ensure_ascii=False))
    mine = "probe_h3" in str(before.get("title") or "")
    floating = False
    if want_float and mine:
        fact("请求浮动窗口", _niri("toggle-window-floating"))
        pump(500)
        # 浮动只解除"参与平铺"，**不还原**被平铺强加的尺寸（本机实测 window_size 仍是
        # 659×736）。尺寸不对，下面量的就不是产品真正显示的那 461×614 个像素，故补一次
        # 客户端 resize——浮动窗口下合成器会接受客户端的尺寸请求。
        host.resize(ww, wh)
        pump(600)
        after = _focused_is_pet(ww, wh)
        fact("浮动+改尺寸后聚焦", json.dumps(after, ensure_ascii=False))
        floating = bool(after.get("floating"))
    else:
        fact("请求浮动窗口",
             "跳过：" + ("--no-float" if not want_float else "聚焦的不是探针窗口，不敢对别的窗口动手"))
    if want_float and not floating:
        print("  [on-screen] ✗ 未能浮动 ⇒ 真机段不可判（平铺会重排桌面，差值法失效），不猜",
              flush=True)
        return
    got_size = tuple(after.get("window_size") or ()) if floating else ()
    if got_size and (abs(got_size[0] - ww) > 2 or abs(got_size[1] - wh) > 2):
        fact("⚠ 合成器给的尺寸≠请求尺寸", f"屏上 {got_size} vs 请求 {(ww, wh)} ⇒ "
             "峰值按'模板与屏上内容不同尺寸'来读，下面的数只能当**下界**")
    pump(500)  # niri 默认开窗口动画，不等够就把动画当成"桌宠长这样"

    # 窗口在屏幕上的绝对矩形：niri 对浮窗**不给**绝对坐标（只有 tile_pos + window_offset），
    # 而焦点环是贴着窗口外扩 width 画的实心矩形 ⇒ 聚焦那一帧里量环框、内缩 width 就是窗口。
    # 这一帧只用来**定位**，不参与任何像素判读（它含合成器画的底色）。
    rect = None
    if ring_rgb is not None and ww and wh:
        rf = A10.capture(f"{tag}_ring")
        if rf is not None:
            m = (np.abs(rf[..., :3].astype(int) - np.array(ring_rgb)) <= 6).all(axis=2)
            ys, xs = np.nonzero(m)
            if m.sum() > 0:
                bx, by = int(xs.min()), int(ys.min())
                bw, bh = int(xs.max()) - bx + 1, int(ys.max()) - by + 1
                ix, iy = (bw - ww) // 2, (bh - wh) // 2
                if ix == iy and (ring_w is None or ix == ring_w):
                    rect = (bx + ix, by + iy, ww, wh)
                fact("环框", f"{bw}×{bh}@({bx},{by}) {m.sum()}px，内缩 {ix}/{iy} "
                     f"vs 配置 width {ring_w} ⇒ 窗口矩形 {rect}")
            else:
                fact("环框", "这一帧里一个环底色像素都没有 ⇒ 桌宠没聚焦，反推不了窗口矩形")
    else:
        fact("环框", "跳过：读不到 active-color 或窗口尺寸")

    # 把焦点还回去 ⇒ 焦点环不再画在桌宠底下。浮动与置顶不依赖焦点，所以桌宠仍在原处照画。
    defocused = False
    if back is not None and pet_id is not None:
        fact("焦点交还原窗口（避开焦点环实心背景）", _focus_window(back))
        pump(700)
        defocused = not _is_focused(pet_id)
        fact("桌宠是否已失焦", "是" if defocused else
             "否 ⇒ 下面所有屏上读数都会含一圈 active-color 底色，按存疑读")
    else:
        fact("焦点交还", f"跳过：原聚焦 id={back} 桌宠 id={pet_id} 有一个没读到 ⇒ "
             "屏上可能带焦点环底色，下面的屏上数按存疑读")

    # 冻结动画在前：with_pet 与 live 模板必须来自**同一帧内容**，否则"有宠峰"量的
    # 是相位差而不是口径差（相位衰减单独由下面的 Δ 曲线量）。
    if widget is not None:
        widget._timer.stop()
        pump(250)
    with_pet = A10.capture(f"{tag}_pet")
    live = (rinfo.get("grab") or (lambda: None))()   # 与 with_pet 同内容、冻结后取的模板

    # 衰减曲线**必须排在取背景之前**：取背景要把窗口藏起来，而 hide→show 会让 niri
    # 重新安放浮动窗口（本机实测两次落点差 16px），那样 Δ=0 就不是"同一位置同一帧"，
    # 整条曲线会被重排污染。这里窗口全程不动，只停/开动画定时器。
    tpl_live = live if live is not None else frames[0]
    decay_tpls = [("整窗", tpl_live)]
    try:
        decay_tpls.append(("补丁", make_feed(tpl_live, bg=None, variant="patch")))
    except ValueError:
        pass
    print("  -- 模板衰减：注册（冻结帧）后让动画再跑 t，模板不更新 ⇒ 峰怎么掉 --", flush=True)
    lum_by = [(n, luma709(t[..., :3]), t.shape[1], t.shape[0]) for n, t in decay_tpls]
    elapsed = 0
    for t_ms in (0, 200, 600, 1200):
        if t_ms > elapsed and widget is not None:
            widget._timer.start(33)
            pump(t_ms - elapsed)
            widget._timer.stop()
            pump(120)   # 等最后一帧真正提交到屏幕，否则量到的是"上一帧还在屏上"
        elif t_ms > elapsed:
            pump(t_ms - elapsed)
        elapsed = t_ms
        shot = A10.capture(f"{tag}_t{t_ms}")
        if shot is None:
            print(f"    t={t_ms:4d}ms 抓帧失败", flush=True)
            continue
        corr = A10.Correlator(luma709(shot[..., :3]).astype(np.float64))
        cells = []
        for name, lum, tw, th in lum_by:
            score, px, py, _s = _best_peak(corr, lum, [1.0])
            cells.append(f"{name} 峰={score:+.3f}@({px + tw / 2:.0f},{py + th / 2:.0f})")
        print(f"    t={t_ms:4d}ms " + " | ".join(cells), flush=True)
    if widget is not None:
        widget._timer.start(33)
        pump(200)
        widget._timer.stop()
        pump(200)
    # 背景用两种取法（opacity / hide），各自顺带量一件产品事实：
    #   opacity=0 —— 产品自己的淡入淡出就靠它（chat_input.py:124-126），它在 Wayland 上
    #                到底改不改屏幕像素，是产品行为问题，不只是探针的取巧手段；
    #   hide()    —— 一定改像素，但会动聚焦 ⇒ 合成器可能给终端换边框，是污染来源。
    host.setWindowOpacity(0.0)
    pump(400)
    bg_op1 = A10.capture(f"{tag}_bgop1")
    pump(120)
    bg_op2 = A10.capture(f"{tag}_bgop2")
    host.hide()
    pump(400)
    bg_hide = A10.capture(f"{tag}_bghide")
    host.setWindowOpacity(1.0)
    host.show()

    # 排查用的原图落盘（只在显式给了目录时才写；不写 = 默认行为不变）。透明区到底是
    # 不是"透出壁纸"，靠平均值一行字争论不出结果，得能自己去看那 20 个像素。
    dump_dir = os.environ.get("H3_DUMP")
    if dump_dir:
        for nm, arr in (("with_pet", with_pet), ("live", live),
                        ("bg_op", bg_op1), ("bg_hide", bg_hide)):
            if arr is not None:
                Image.fromarray(np.ascontiguousarray(arr[..., :3]).astype(np.uint8)).save(
                    os.path.join(dump_dir, f"h3_{nm}.png"))
        if live is not None and live.shape[2] == 4:
            Image.fromarray(live[..., 3]).save(os.path.join(dump_dir, "h3_live_alpha.png"))
        fact("原图落盘", f"{dump_dir}（{len(os.listdir(dump_dir))} 个文件）")

    anim = None
    anim_px = None
    if bg_op1 is not None and bg_op2 is not None and bg_op1.shape == bg_op2.shape:
        raw_anim = np.abs(bg_op1.astype(np.int16) - bg_op2.astype(np.int16)).max(axis=2) > 12
        anim_px = int(raw_anim.sum())
        anim = A10._dilate(raw_anim)
    # 两帧都在 opacity=0 下抓，理应只差"桌面自己在动的东西"（时钟、指针、壁纸动画）。
    # 若这个数接近桌宠的面积，说明 **setWindowOpacity(0) 在 Wayland 上根本没让窗口消失**，
    # 那"扣除动画区"就是在抠掉桌宠本身 ⇒ 真值盒会莫名偏小、NCC 会莫名偏低。先报数再决定。
    fact("两帧 opacity=0 之间的变化", f"{anim_px}px（应当 ≪ 窗口面积 "
         f"{(ww * wh) if ww and wh else '?'}px；同量级 = opacity 没藏住窗口，anim 不可用）"
         if anim is not None else "不可判（缺一张 opacity 抓帧）")
    # 候选背景必须**自己就没有宠**——这条不能靠假设。之前这里写的是"opacity 优先、hide 只作
    # 后备"，理由是 hide 会动聚焦与阴影、差集大 = 污染多；那是把**焦点环**当成了污染。实测
    # 反过来：opacity=0 在 Wayland 上根本没让桌宠消失（产品自己的淡入淡出正是用它，
    # chat_input.py:124-126），而 hide() 才是真干净。所以现在按量出来的数选，不按机制猜。
    # 判据：背景与有宠帧在窗口矩形内的改动像素数 ÷ 模板不透明像素数 ≥ 0.6 才算"里面没宠"。
    opaque_mask = (tpl_live[..., 3] > 8) \
        if tpl_live is not None and tpl_live.shape[2] == 4 else None
    truth = None
    for label, bgc in (("opacity=0", bg_op1), ("hide()", bg_hide)):
        box, keep, note = _diff_box(with_pet, bgc, None)
        frac = _changed_frac(with_pet, bgc, rect, opaque_mask)
        clean = frac == frac and frac >= 0.6          # NaN != NaN
        fact(f"真值盒[{label}]", f"{box} {note}")
        fact(f"  背景干净度[{label}]",
             f"桌宠自己那 {int(opaque_mask.sum()) if opaque_mask is not None else '?'}px 里改了 {frac}"
             + ("⇒ 桌宠确实不在里面" if clean else
                ("⇒ 判不了：掩模与窗口矩形不同尺寸，量具有问题" if frac != frac else
                 "⇒ **桌宠还在这张'背景'里**，不能当空背景用")))
        if anim is not None and anim_px is not None and anim_px < 0.25 * ww * wh:
            b2, k2, n2 = _diff_box(with_pet, bgc, anim)
            fact(f"  同盒(扣 anim)", f"{b2} {n2}")
        if box is not None and clean and truth is None:
            truth = (box, note, keep, bgc, label)
    # 两张候选背景互查：都干净 ⇒ 桌宠像素上应当几乎没差别；接近 1 ⇒ 其中一张还画着宠，
    # 上面那个"谁干净"的判断就有了独立的第二读数（不靠单一阈值说话）。
    fact("两种背景互查(桌宠像素上)", f"{_changed_frac(bg_op1, bg_hide, rect, opaque_mask)}"
         "（≈0 = 两张都没宠；≈1 = 一张有、一张没有）")
    bg1 = truth[3] if truth else bg_op1
    if truth is None:
        print("  [on-screen] ✗ 没有一种取背景方式能拿到'干净且差集成盒'⇒ 真机段不可判，不猜"
              "（桌宠没画上？一直在动？还是藏不掉？）", flush=True)
        return
    if bg1 is not None:
        fact("抓帧尺寸(物理px)", f"{bg1.shape[1]}x{bg1.shape[0]} / 模板帧(逻辑px) "
             f"{list(with_pet.shape[1::-1]) if with_pet is not None else None}"
             f" vs 窗口 {rinfo.get('window_px')}")
    fact("采用的背景", f"{truth[4]}（差集 {truth[2]}px）")
    bg_label = truth[4]
    truth, note = truth[0], truth[1]
    x0, y0, x1, y1 = truth
    disp = (x1 - x0, y1 - y0)
    if ring_rgb is not None:
        probe_box = rect or (x0, y0, x1 - x0, y1 - y0)
        rp, rb = _ring_px(with_pet, ring_rgb, probe_box), _ring_px(bg1, ring_rgb, probe_box)
        tail = "" if (defocused and not rp and not rb) else \
            " ⇒ 这一盒里混进了合成器画的环底色，屏上数不是客户端像素"
        fact("环底色自检(窗口矩形内)", f"有宠帧 {rp}px / 背景帧 {rb}px（都该 ≈0）{tail}")
    # 喂给峰值表的模板 = 与 with_pet **同一冻结帧**的那张（live）。用探针开头取的
    # frames[0] 会把"相位差"混进"口径差"里读不出来；相位衰减单独由下面的 Δ 曲线量。
    template0 = live if live is not None else frames[0]
    # 屏上"亮起来的那块"对应的是模板的 **alpha 外接盒**（本机 461×614 的窗口里只有
    # ~245×529 有像素），拿整窗尺寸去比是口径错。浮动窗口还带 niri 阴影，真值盒只会
    # 偏大不会偏小 ⇒ 这里的差值要按"上界含阴影"来读。
    st = alpha_stats(template0)
    vis = st.get("bbox")
    expect = (vis[2] - vis[0], vis[3] - vis[1]) if vis else (ww, wh)
    fact("屏上可见盒 / 手里模板 alpha 外接盒", f"{disp} / {expect}"
         f"（整窗 {(ww, wh)}，模板覆盖率 {st.get('coverage')}）")
    if abs(disp[0] - expect[0]) > 8 or abs(disp[1] - expect[1]) > 8:
        fact("⚠ 屏上尺寸≠模板可见尺寸",
             f"差 {disp[0] - expect[0]:+d},{disp[1] - expect[1]:+d}px ⇒ 峰值会被缩放/阴影摊薄，"
             "下面的数只能当**下界**读（真机段仍未清，缺的是环境不是判据）")

    # **H3 的口径主问题**：桌宠帧里 78% 的像素 alpha=0，屏幕上这些位置到底透出背景、
    # 还是被填成了某种颜色？fidus 的 `luma_at()` 丢 alpha、按 RGB 计亮度，所以填成什么
    # 直接决定"模板说的"和"屏上有的"是否同一件事。这一步不问模板，只比同两处像素。
    # 窗口矩形优先用**焦点环反推**的那一个：差集盒只有桌宠轮廓那么大（本机 230×526 <
    # 整窗 461×614），拿它当窗口矩形必然"放不下请求尺寸"⇒ 透明区/over_wallpaper 全被跳过。
    if rect:
        px0, py0, pw, ph = rect
        rect_src = "焦点环反推"
    else:
        ins_x = max(0, (disp[0] - ww) // 2) if ww and disp[0] >= ww else 0
        ins_y = max(0, (disp[1] - wh) // 2) if wh and disp[1] >= wh else 0
        px0, py0, pw, ph = x0 + ins_x, y0 + ins_y, ww, wh
        rect_src = "差集盒内缩（无环框可反推，退而求其次）"
    fact("窗口矩形", f"{(px0, py0, pw, ph)} ← {rect_src}")
    rect_bg = None   # 窗口矩形内的背景像素；由下面的透明区检查顺手填上，也供 over_wallpaper 用
    if pw and ph:
        rect_pet = with_pet[py0:py0 + ph, px0:px0 + pw]
        rect_bg = bg1[py0:py0 + ph, px0:px0 + pw]
        if rect_pet.shape[:2] == template0.shape[:2] == rect_bg.shape[:2]:
            # **量具自检（排在一切屏上读数之前）**：手里那一帧和屏上那一块到底同不同源？
            # 只用 alpha>8 的不透明像素算掩模 NCC 扫 ±20px 偏移，透明区一律不参与 ⇒
            # "峰值上不去"再不能拿"透明区口径"当挡箭牌。最佳偏移≠(0,0) 就是**探针裁错了**，
            # 下面所有屏上数字都要先修探针再读；(0,0) 且≈1 时，口径差才是真事实。
            score_a, dx_a, dy_a = _masked_align(template0, with_pet, px0, py0)
            fact("掩模对齐自检(仅不透明像素)",
                 f"最佳 NCC={score_a:+.3f} @偏移({dx_a:+d},{dy_a:+d})px ⇒ "
                 + ("探针裁切与屏上内容同源 ⇒ 下面的屏上读数可信"
                    if score_a > 0.9 and abs(dx_a) <= 2 and abs(dy_a) <= 2
                    else "⚠ 不同源（偏移大或分数低）⇒ 先修探针，屏上数字暂不采信"))
            trans = template0[..., 3] < 8
            opaque = ~trans
            mp = rect_pet[trans].mean(axis=0).round(1)
            mb = rect_bg[trans].mean(axis=0).round(1)
            changed = float((np.abs(rect_pet.astype(np.int16) - rect_bg.astype(np.int16))
                             .max(axis=2) > 12)[trans].mean())
            fact("透明区(占模板 "
                 f"{100 * trans.mean():.0f}%)屏上平均色", f"有宠 {mp} vs 同处背景 {mb} ⇒ "
                 f"屏上变了 {100 * changed:.1f}% 的透明区像素")
            mo = rect_pet[opaque].mean(axis=0).round(1)
            mbo = rect_bg[opaque].mean(axis=0).round(1)
            fact("不透明区屏上平均色", f"有宠 {mo} vs 同处背景 {mbo}"
                 "（这一栏是量具自检：它应当明显不同，否则'有宠帧'根本没拍到桌宠）")
        else:
            fact("透明区", f"跳过：屏上矩形 {rect_pet.shape[:2]} ≠ 模板 {template0.shape[:2]}")
    else:
        fact("透明区", "跳过：反推不出窗口矩形")

    bg_crop = rect_bg if rect_bg is not None else bg1[y0:y1, x0:x1]
    if bg_crop.shape[:2] != template0.shape[:2]:
        fact("over_wallpaper", f"跳过：背景块 {bg_crop.shape[:2]} 与模板帧 "
             f"{template0.shape[:2]} 不同尺寸，不强行对齐")
        bg_for_feed = None
    else:
        bg_for_feed = bg_crop
        fact("over_wallpaper 背景来源", f"窗口矩形（{rect_src}）内、由 {bg_label} 取的干净桌面像素")

    scales = [1.0]
    nominal = None
    try:
        # 实测（本机 niri 26.04）：`niri msg output <NAME> scale` 是**设置**子命令、不是查询
        # ——它要求一个 `<SCALE>` 参数，漏了才报 "required arguments were not provided"。
        # 也就是说若哪天补上数字，这一行会**改掉显示缩放**。查询只能走 `niri msg --json outputs`。
        raw = subprocess.run(["niri", "msg", "--json", "outputs"],
                             capture_output=True, text=True, timeout=8).stdout
        outs = json.loads(raw)
        nominal = float(next(iter(outs.values()))["logical"]["scale"])
    except Exception as exc:  # noqa: BLE001
        print(f"  [scale] 读不到（{type(exc).__name__}），只按 1.0 搜", flush=True)
    if nominal is not None and abs(nominal - 1.0) > 1e-6:
        scales.append(round(nominal, 4))
    fact("搜索尺度集", f"{scales}（niri 报 scale={nominal}；缩放问题 A10 已判，不在此重跑）")

    # 峰值表用同一冻结帧；之后每个 Δ 抓一次屏 ⇒ 峰随时间的衰减 = 该不该逐帧重注册
    print("  -- 各喂法在『真机当前帧』与『空背景』上的峰值"
          "（对照 fidus 文档门槛 ~0.35 可锁定）--", flush=True)
    for variant in FEEDS:
        try:
            tpl = make_feed(template0, bg=bg_for_feed, variant=variant)
        except ValueError as exc:
            print(f"  {variant:14s} 跳过：{exc}", flush=True)
            continue
        lum = luma709(tpl[..., :3])
        score_pet, px_pet, py_pet, s_pet = _best_peak(
            A10.Correlator(luma709(with_pet[..., :3]).astype(np.float64)), lum, scales)
        score_bg, px_bg, py_bg, s_bg = _best_peak(
            A10.Correlator(luma709(bg1[..., :3]).astype(np.float64)), lum, scales)
        cx_pet, cy_pet = px_pet + tpl.shape[1] / 2, py_pet + tpl.shape[0] / 2
        tcx, tcy = (x0 + x1) / 2, (y0 + y1) / 2
        verdict, worst, _per = localizability(tpl)
        fid = ""
        if shared is not None:
            fid_v, _dt = fidus_register(shared, tpl)
            fid = f" fidus={fid_v[:40]}"
        print(f"  {variant:14s} size={tpl.shape[1]}x{tpl.shape[0]:<5d} "
              f"复刻={verdict:13s} worst={_fmt(worst)} | "
              f"有宠峰={score_pet:+.3f}@({cx_pet:.0f},{cy_pet:.0f})s{s_pet} "
              f"离真值中心={((cx_pet - tcx) ** 2 + (cy_pet - tcy) ** 2) ** 0.5:6.1f}px | "
              f"空背景误锁峰={score_bg:+.3f}@({px_bg + tpl.shape[1] / 2:.0f},"
              f"{py_bg + tpl.shape[0] / 2:.0f})"
              f"{'← 已过 fidus 自述可用线 0.35，桌面空处就能锁' if score_bg >= LOCK_THRESHOLD else ''}"
              f"{fid}", flush=True)


def _best_peak(corr: "A10.Correlator", tmpl_luma: np.ndarray, scales: list[float]):
    """在给定尺度集里取 NCC 峰最高者，返回 (score, x, y, scale)。"""
    best = (-2.0, -1, -1, None)
    for s in scales:
        score, x, y = corr.peak(_scale(tmpl_luma, s))
        if score > best[0]:
            best = (score, x, y, s)
    return best


def _scale(plane: np.ndarray, s: float) -> np.ndarray:
    if abs(s - 1.0) < 1e-9:
        return plane
    img = Image.fromarray(np.clip(plane, 0, 255).astype(np.uint8), "L")
    img = img.resize((max(1, int(round(img.width * s))), max(1, int(round(img.height * s)))),
                     Image.BILINEAR)
    return np.asarray(img, dtype=np.float64)


def _solid_with_border(h: int, w: int) -> np.ndarray:
    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[..., :3] = 30
    a[..., 3] = 255
    a[2:h - 2, 2:w - 2, :3] = 230
    return a


def _fmt(v) -> str:
    return "  —  " if v is None else f"{v:.3f}"


def _fmt_list(vs: list[float]) -> str:
    return "[]" if not vs else "[" + " ".join(f"{v:.3f}" for v in vs) + "]"


if __name__ == "__main__":
    raise SystemExit(main())
