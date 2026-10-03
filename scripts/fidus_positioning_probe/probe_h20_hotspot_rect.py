#!/usr/bin/env python3
"""H20 · 量出交互态桌宠**当前**的热区矩形，并把点击分区界换算到屏幕坐标。

出处：`~/.Athena/projects/meapet/working/fractional-scale-head-clip.md` §8。

## 这个量具为什么存在（两条把上一版设计推翻的实测）
1. **「帧 vs 轮廓」不是个问题**。人工裁决 2026-09-28：「帧=热区，轮廓=热区是无根据的，
   从代码注释可知，曾经有过椭圆裁切，但是从未有过轮廓裁切，以及在Wayland上的点击穿透必须
   layer」。⇒ 上一版（probe_h20_band_hit.py，A0-A4 五臂 + 拖拽位移读数）整段作废：
   它给一个无根据的假设列了判据臂。
2. **真正会变的是"那个矩形由谁定"**。悬浮态 ⇒ Qt 请求生效（本机 461x614）；被平铺 ⇒ niri
   忽略请求、以用户预设为准（本机实测 659x736）。且「包括但不限」⇒ 别把这两条当全集。
   于是任何按屏幕坐标设计的手势**不能一次量完全程复用**：交互态下平铺矩形会随焦点/排布
   变动（本轮 T5 就是这么偏的，见 §8.5）。⇒ 每步现量，本件就是干这个的。

## 量法
niri 默认把焦点环画成**窗口底下的实心矩形**（config.kdl:170-178 自述「they will show up
through semitransparent windows」），active-color 是 #7fc8ff ⇒ 桌宠**聚焦**时，透明区显蓝，
那块蓝就是热区本体的形状。窗口矩形 = 蓝框内缩 focus-ring width（config 里是 4）。
前提：桌宠必须聚焦，否则环不画，本件直接报"读不到"而不是给一个猜的数。

## 读数换算（live2d_widget.py:529-541，全窗口范围、不做任何形状裁剪）
    nx = (widget_x / w) * 2 - 1 ;  ny = -((widget_y / h) * 2 - 1)
    ny > 0 → upper(head) ; 否则 nx < 0 → lower_left ; 其余 → lower_right
w/h 是 Live2D 子控件自己的尺寸（日志「canvas=768x768 offset=(-184,0)」），不是窗口尺寸，
所以分区界落在 窗口左 + offset_x + w/2 —— 平铺后窗口变宽，界**不**跟着居中。

## 用法
    probe_h20_hotspot_rect.py            # 抓一帧，打印蓝框/窗口/分区界/透明带
    probe_h20_hotspot_rect.py --frame /tmp/x.png   # 用已有帧（离线复算）
只读：grim 抓一帧 + 一次像素投影。不动窗口、不合成输入、不写任何状态。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
RING_COLOR = (127, 200, 255)   # niri focus-ring active-color "#7fc8ff"
TOL = 8
MIN_FILL = 3   # 投影门限：终端里偶发的同色字形不该把蓝框撑大（本轮实测踩过同类）
CONFIG = Path.home() / ".config" / "niri" / "config.kdl"


def ring_width() -> int | None:
    """从 niri 配置读 focus-ring width；读不到/环关掉 → None（调用方必须出声，不能按 0 算）。

    先剥掉整行注释再找块：config.kdl 头部有 `//    focus-ring { }` 这种示例，
    不剥就会先匹配到它，拿到的"块"里没有 width ⇒ 静默返回 0（本轮踩过）。
    """
    try:
        text = CONFIG.read_text()
    except OSError:
        return None
    code = "\n".join(line.split("//")[0] for line in text.splitlines())
    block = re.search(r"focus-ring\s*\{(.*?)\n\s*\}", code, re.S)
    if not block:
        return None
    body = block.group(1)
    if re.search(r"^\s*off\s*$", body, re.M):
        return None
    m = re.search(r"^\s*width\s+(\d+)", body, re.M)
    return int(m.group(1)) if m else None


def canvas_geom() -> tuple[int, int, int, int]:
    """从产品日志尾部取最近一次 canvas=WxH offset=(X,Y)；取不到退回 (0,0,0,0)。"""
    log = Path("/tmp/h20_pet.log")
    if not log.exists():
        return (0, 0, 0, 0)
    hit = None
    for line in log.read_text(errors="replace").splitlines():
        m = re.search(r"canvas=(\d+)x(\d+) offset=\((-?\d+),(-?\d+)\)", line)
        if m:
            hit = tuple(int(v) for v in m.groups())
    return hit or (0, 0, 0, 0)


def is_ring(a) -> np.ndarray:
    """逐像素判「等于焦点环蓝」——三通道都在容差内。"""
    return np.logical_and.reduce(
        [np.abs(a[..., i] - RING_COLOR[i]) <= TOL for i in range(3)]
    )


def blue_box(path: Path) -> tuple[int, int, int, int] | None:
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    mask = is_ring(a)
    rows = np.where(mask.sum(axis=1) >= MIN_FILL)[0]
    cols = np.where(mask.sum(axis=0) >= MIN_FILL)[0]
    if not len(rows) or not len(cols):
        return None
    return int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())


def opaque_runs(a, y: int, x0: int, x1: int) -> list[tuple[int, int]]:
    """给定行，返回蓝框内的不透明段（= 身子实际占的列）。"""
    ring = is_ring(a)[y]
    segs: list[tuple[int, int]] = []
    start = None
    for x in range(x0, x1 + 1):
        if not ring[x] and start is None:
            start = x
        elif ring[x] and start is not None:
            segs.append((start, x - 1))
            start = None
    if start is not None:
        segs.append((start, x1))
    return segs


def cmd_measure(args) -> int:
    frame = Path(args.frame)
    if frame is None or not frame.exists():
        frame = Path("/tmp/h20_hotspot_frame.png")
        if subprocess.run(["grim", "-t", "png", str(frame)],
                          capture_output=True).returncode != 0:
            print("★ grim 抓帧失败 ⇒ 无从判读（不是「没有热区」）")
            return 1
    box = blue_box(frame)
    if box is None:
        print(f"★ {frame} 里读不到焦点环蓝 ⇒ 桌宠当前失焦或不在屏上。")
        print("  本件**不猜**矩形：请先让桌宠聚焦（点它一下或长按显形）再量。")
        return 2
    ring = ring_width()
    if ring is None:
        print("★ 读不到 focus-ring width（配置里没有、或环被 off）⇒ 下面按**蓝框原样**给，未内缩。")
        print("  真实窗口 = 蓝框内缩环宽；把蓝框直接当窗口会每边多算，别拿它出手势坐标。")
        ring = 0
    x0, y0, x1, y1 = box
    wx0, wy0, wx1, wy1 = x0 + ring, y0 + ring, x1 - ring, y1 - ring
    print(f"帧 {frame}")
    print(f"焦点环蓝框  x[{x0},{x1}] y[{y0},{y1}]  {x1-x0+1}x{y1-y0+1}")
    print(f"窗口矩形    x[{wx0},{wx1}] y[{wy0},{wy1}]  "
          f"{wx1-wx0+1}x{wy1-wy0+1}   （内缩 focus-ring width={ring}）")
    cw, ch, ox, oy = canvas_geom()
    if cw:
        split_x = wx0 + ox + cw // 2
        split_y = wy0 + ch // 2
        print(f"画布自述    {cw}x{ch} offset=({ox},{oy})（产品日志最近一条）")
        print(f"分区界      upper = 屏幕 y < {split_y} ;  左右分界 = 屏幕 x {split_x}")
        print(f"            widget 覆盖窗口 x[{wx0+ox},{wx0+ox+cw}]，"
              f"超出窗口的部分收不到点击")
    a = np.asarray(Image.open(frame).convert("RGB")).astype(int)
    for y in (wy0 + 120, wy0 + (wy1 - wy0) // 2, wy1 - 60):
        if y > wy1:
            continue
        segs = opaque_runs(a, y, wx0, wx1)
        left_band = (segs[0][0] - wx0) if segs else (wx1 - wx0)
        print(f"  y={y}: 身子段 {segs}  左透明带宽 {left_band} px")
    print("⇒ 手势坐标请**每次现量**：平铺矩形会随焦点/排布变动（§8.5）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--frame", default=None, help="用已有 grim 帧，不重新抓")
    args = ap.parse_args()
    return cmd_measure(args)


if __name__ == "__main__":
    sys.exit(main())
