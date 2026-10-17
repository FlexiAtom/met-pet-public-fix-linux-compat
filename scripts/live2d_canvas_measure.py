#!/usr/bin/env python3
"""L1 读数：Live2D 模型的**真画布**到底是什么，以及它和窗口几何怎么对上。

Subject under test: `meapet.desktop.live2d_widget.Live2DModel.get_suggested_size()`
→ live2d-py 1.0 的顶层 `live2d.Model` 上的 `GetCanvasSize()`，以及
`render_host._read_canvas_size_from_model` / `_live2d_base_size` /
`calculate_live2d_viewport_layout` 这三段算术的输入输出。

它判的一件事（#258）：切换引擎后窗口尺寸该由**模型自己的画布**决定，可今天那条读数通道
一次都没成立过 —— `GetCanvasSize()` 给的是**模型单位**（不是像素），`int()` 之后是
`(1, 2)`，被 `MIN_CANVAS_SIZE=256` 的范围校验当场拒掉，于是每一层回退都落空，
`_live2d_base_size()` 最终报硬编码 `(1024, 1024)`。这脚本把「真数是多少、哪个 API 给得出
像素、画出来的模型在这个盒子里占多大」三个读数一次拿齐。

为什么要 `grab()`：`initializeGL` 只在一次真实 paint 里跑（QOpenGLWidget 的前提），
offscreen 平台上 `show()` + `processEvents()` 不足以把首帧逼出来，得主动抓一次。
这条链在真机上是 Qt 自己驱动的，这里只是把同一份代码逼到能读数的状态。

What this CANNOT decide：它不判真机上首帧到达的时机，也不判 niri 配出多大 surface
（那是 `scripts/engine_switch_size_probe.py` 与 L3 的活）；`grab()` 出来的 alpha 轮廓
只用来量「模型在盒子内的相对占比」，不是像素级验收。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication  # noqa: E402


def alpha_bbox(img) -> tuple | None:
    """QImage(RGBA8888) 的不透明像素外接框 `(x0, y0, x1, y1)`，全透明返回 None。"""
    import numpy as np

    w, h = img.width(), img.height()
    buf = img.constBits()
    buf.setsize(w * h * 4)
    a = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[..., 3]
    rows = np.flatnonzero((a > 8).any(axis=1))
    cols = np.flatnonzero((a > 8).any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    return (int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max()))


def main() -> int:
    from meapet.config.store import resolve_resource_path
    from meapet.desktop.live2d_widget import Live2DModel, init_live2d
    from meapet.desktop.render_host import (
        DEFAULT_CANVAS_SIZE,
        MAX_CANVAS_SIZE,
        MIN_CANVAS_SIZE,
        calculate_live2d_viewport_layout,
    )

    from meapet.live2d_api import live2d as l2d

    app = QApplication(sys.argv[:1])
    init_live2d()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    model_dir = resolve_resource_path(cfg["live2d"]["model_dir"])
    print(f"model_dir={model_dir}")

    l2d_model = Live2DModel(model_dir)
    print(f"[未加载] get_suggested_size() = {l2d_model.get_suggested_size()}")
    widget = l2d_model.create_widget()
    widget.resize(366, 512)
    widget.show()
    app.processEvents()
    grabbed = widget.grab()
    app.processEvents()
    for _ in range(40):
        app.processEvents()
        if l2d_model.model is not None:
            break
    model = l2d_model.model
    if model is None:
        print("ABORT: 离屏里模型没加载出来（首帧通道没通），下面的读数拿不到")
        return 2

    print(f"[已加载] GetCanvasSize()        = {model.GetCanvasSize()}")
    print(f"[已加载] GetCanvasSizePixel()   = {model.GetCanvasSizePixel()}")
    print(f"[已加载] GetPixelsPerUnit()     = {model.GetPixelsPerUnit()}")
    print(f"[已加载] get_suggested_size()   = {l2d_model.get_suggested_size()}")
    print(f"[已加载] Live2DModel 上有 get_canvas_size/GetCanvasSize/getCanvasSize? "
          f"{[n for n in ('get_canvas_size', 'GetCanvasSize', 'getCanvasSize') if hasattr(l2d_model, n)]}")
    print(f"[已加载] {type(model).__name__} 上有 GetCanvasWidth/canvasWidth/width? "
          f"{[n for n in ('GetCanvasWidth', 'getCanvasWidth', 'canvasWidth', 'width') if hasattr(model, n)]}")
    print(f"[已加载] {type(model).__name__} 上有 GetCanvasSizePixel? {hasattr(model, 'GetCanvasSizePixel')}")
    print(f"常量 MIN/MAX/DEFAULT = {MIN_CANVAS_SIZE}/{MAX_CANVAS_SIZE}/{DEFAULT_CANVAS_SIZE}")

    px = model.GetCanvasSizePixel()
    aspect = float(px[0]) / float(px[1])
    print(f"真画布宽高比 = {aspect:.4f}（5:7 = {5/7:.4f}）")

    factor = 0.5
    for label, base in (
        ("现状·加载后(硬编码 1024 方)", (1024, 1024)),
        ("现状·加载前(525x735)", (525, 735)),
        ("候选·按真画布长边归一到 1024", (round(1024 * aspect), 1024)),
    ):
        layout = calculate_live2d_viewport_layout(base[0], base[1], factor,
                                                  cfg["live2d"].get("window_mask"))
        print(f"{label}: base={base} → window={layout.window_width}x{layout.window_height} "
              f"canvas={layout.widget_width}x{layout.widget_height} "
              f"offset=({layout.widget_x},{layout.widget_y})")

    from PyQt5.QtGui import QImage

    def painted_bbox(size: tuple[int, int]):
        """按 `_fit_model_to_window` 的口径把模型摆成 size，再离屏取 alpha 外接框。

        产品里这一步是 `resizeGL → model.Resize(w, h)`（`live2d_widget.py:262-271`），
        这里直接调同一句 `Resize`，再走 `render_offscreen()` 的 FBO 通道取帧——
        offscreen 平台上 `QOpenGLWidget.grab()` 明确不支持，FBO 是唯一能量轮廓的通路。
        """
        widget.resize(*size)
        model.Resize(*size)
        for _ in range(10):
            app.processEvents()
        img = widget.render_offscreen()
        if img is None:
            return None
        return (img.width(), img.height(), alpha_bbox(img))

    for size in ((366, 512), (512, 512), (262, 368), (1024, 1024)):
        got = painted_bbox(size)
        if got is None or got[2] is None:
            print(f"[画出来的模型] widget={size[0]}x{size[1]} → 全透明（没画出来）")
            continue
        iw, ih, bb = got
        bw, bh = bb[2] - bb[0] + 1, bb[3] - bb[1] + 1
        print(f"[画出来的模型] widget={size[0]}x{size[1]} 取回帧={iw}x{ih} "
              f"alpha 外接框={bb} 绝对={bw}x{bh} 占高={bh / ih:.3f} 中心x={(bb[0]+bb[2]) / 2:.0f}")

    # 动作余量：把窗口从 307 窄到 219 之前，先量「动作跑起来时轮廓最宽到哪儿」。
    # 取的是**跨帧并集**，不是某一帧——裁边风险由最宽的那一帧决定。
    probe_size = (366, 512)
    widget.resize(*probe_size)
    model.Resize(*probe_size)
    union = None
    samples = 0
    try:
        model.StartMotion("Idle", 0, l2d.MotionPriority.FORCE)
    except Exception as exc:
        print(f"[动作余量] StartMotion('Idle') 失败：{type(exc).__name__}: {exc}")
    for i in range(120):
        for _ in range(3):
            app.processEvents()
        img = widget.render_offscreen()
        bb = alpha_bbox(img) if img is not None else None
        if bb is None:
            continue
        samples += 1
        union = bb if union is None else (min(union[0], bb[0]), min(union[1], bb[1]),
                                          max(union[2], bb[2]), max(union[3], bb[3]))
        try:
            model.Update()
        except Exception:
            pass
    if union is None:
        print("[动作余量] 120 帧都没量到（离屏渲染没出图）")
    else:
        crop = (88, 0, 307, 410)     # base=(731,1024)、factor=0.5 时窗口覆盖的画布矩形
        print(f"[动作余量] 跨 {samples} 帧并集 = {union} "
              f"宽 {union[2] - union[0] + 1} 高 {union[3] - union[1] + 1}")
        print(f"[动作余量] 候选窗口覆盖画布 x∈[{crop[0]},{crop[2]}) y∈[{crop[1]},{crop[3]}) "
              f"→ 越界 左{max(0, crop[0] - union[0])} 右{max(0, union[2] - (crop[2] - 1))} "
              f"上{max(0, crop[1] - union[1])} 下{max(0, union[3] - (crop[3] - 1))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
