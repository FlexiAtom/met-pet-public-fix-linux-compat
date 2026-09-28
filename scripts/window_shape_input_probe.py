#!/usr/bin/env python3
"""判定探针 · 「轮廓＝热区」在 X11 支路上到底通不通（两档都**只在悬浮窗上判**）

起因：人工从不同维护者拿到不同口径（有说能用、有说点不动），要我自己下一个判定。
分歧的根源**不在算法而在平台**：这条链的唯一下手处是 `QWidget.setMask`
（`render_host.py:_apply_live2d_window_region` → 1653），而 setMask 在各后端的归宿完全不同。

**为什么两档都先过悬浮闸门**（人工纠正 2026-09-29，逐字）：
「只要窗口不是悬浮窗，就都会忽略，而你创建的窗口一定是平铺，这是个一根筋两头堵的问题，
解法就是先创建，然后 Win + V 变成悬浮窗，再调整」
⇒ `float_gate()` 就是那句"再调整"。判据到这是第三版，前两版的教训都记在函数串里：
v1 拿 `X11BypassWindowManagerHint` 当悬浮 ⇒ hint 是请求不是事实；
v2 拿"请求尺寸被服务端照做"当悬浮 ⇒ **人工点名作废：平铺可以正常改大小**，尺寸分不开两态。
现在只读合成器自报的 `is_floating`（`niri msg --json windows`）。
合成器说不悬浮 ⇒ 出「不可判」不出结论；请人工按 Mod+V，每 ~2 s 重问一次，留足反应时间。

先说清**为什么没有 layer 臂**（两条人工纠正各砍掉一臂的一半）：

* **layer 臂不成立，删**。layer 模式下可见 surface 归 Rust shim，桥接层的输入区只有
  full / empty **两态**（`wayland.rs:440-442`，符号表里没有任何收矩形/区域的入口），
  而 Qt 那只挂了 mask 的窗口已经被 `hide()`。⇒ 这一支路要么整体穿透、要么整体可点，
  **"轮廓"没有能承载它的语义**，实验的前提就不存在——不是"测出来不通"，是**无从测**。
  判定对这一支改用结构论证（符号表 + 两态 + hide），不装成实测。
* **叠放那一档必须由人配合**。上一版靠叠一只背景窗看"命中有没有落到下层"，但 niri 是
  **平铺**合成器：两只窗各占一格、永不重叠 ⇒ 命中翻转落进的是背景窗自己的格子，跟 mask
  毫无关系（同臂的 XShape 两次都一样，那一格自己就证伪了"翻转＝穿透"）。
  改用 `X11BypassWindowManagerHint`（override-redirect）也**不保证**：Qt xcb 跑在 XWayland 上，
  摆放权在合成器手里，这个 hint 可以被整个忽略 ⇒ 只当"请求"，一切以服务端回读的几何为准。

`--stage probe` 量的是**服务端自己的答案**，一只悬浮窗就够：

    环节                       问法                                     谁可能坏在这
    ① Qt 有没有发出形状请求   XShapeQueryExtents 的 bounding/input 位   Qt 的 xcb 插件
    ② 服务端认不认输入形状     根窗上的 XQueryPointer 命中测试           X 服务端（Xwayland 也是）
    ③ 形状落到用户手指         `--stage fallthrough`                    XWayland ↔ 合成器的转发

①②之间带一条**判权对照**（「轮廓直发」）：**同一个区域**不经 Qt、由探针自己送进 ShapeInput。
它裁得动而 `setMask` 裁不动，断点才真的落在「Qt 没把 mask 送进 XShape」；缺了这条，
"setMask 后命中不变"分不清是 Qt 没发请求还是这台服务端根本不按 ShapeInput 裁输入。
直发之后紧跟一次「直发还原」（送全框），否则残留形状会替 `setMask` 假装成功。

用法
----
    QT_QPA_PLATFORM=xcb .venv/bin/python scripts/window_shape_input_probe.py \\
        --stage probe --repeat 10 --wait-float 60     # 提示「待悬浮」后对这扇窗按 Mod+V
    QT_QPA_PLATFORM=xcb .venv/bin/python scripts/window_shape_input_probe.py \\
        --stage fallthrough --wait-float 60           # 两只窗各按一次 Mod+V

副作用：移动真实指针（开头存、结尾必还原）；开/关临时窗；不改产品码。
退出码：0 = 判到（结论再否定也是 0）；1 = 量具/环境不可用（**不得**当否证）。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
PROBES = HERE / "fidus_positioning_probe"     # 复用 H4 的事件泵口径
for _p in (str(PROBES), str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SIDE = 240                       # 被测窗边长
BACK = 700                       # fallthrough 的背景窗边长（要大于被测窗才能验"落到下层"）
KEEP_AT = (0.15, 0.15)           # 三角形轮廓**内**的取样点（窗口本地比例）
CUT_AT = (0.85, 0.85)            # 矩形内、轮廓外的取样点
TRIANGLE = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))

TITLE_MARK = "fidus轮廓热区探针"   # 与 niri 对表的连接键前缀（实测只有 title 能对上）
FIRST_MS = 8000                    # 每道"要人工动手"的闸，开口后先给这么久的反应时间
                                   #（人工点名两次"太快了，加延时，不然人工没法反应"）
POLL_MS = 2000                     # 等人工动作期间的重问节拍


def say(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


def pump(app, ms: int) -> None:
    import probe_h4_ceiling_infer as H4

    H4.pump(app, ms)


# ------------------------------------------------------------------ X 侧量具 ──

class XRectangle(ctypes.Structure):
    """X.h 里的 XRectangle：x/y 是 INT16，width/height 是 CARD16。"""

    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short),
                ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]


def _x11():
    lib = ctypes.cdll.LoadLibrary("libX11.so.6")
    p = ctypes.POINTER
    W, I, U, B = ctypes.c_ulong, ctypes.c_int, ctypes.c_uint, ctypes.c_int
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XDefaultScreen.argtypes = [ctypes.c_void_p]
    lib.XRootWindow.restype = W
    lib.XRootWindow.argtypes = [ctypes.c_void_p, I]
    lib.XSync.argtypes = [ctypes.c_void_p, B]
    lib.XGetGeometry.restype = B
    lib.XGetGeometry.argtypes = [ctypes.c_void_p, W, p(W), p(I), p(I), p(U), p(U), p(U), p(U)]
    lib.XTranslateCoordinates.restype = B
    lib.XTranslateCoordinates.argtypes = [ctypes.c_void_p, W, W, I, I, p(I), p(I), p(W)]
    lib.XQueryPointer.restype = B
    lib.XQueryPointer.argtypes = [ctypes.c_void_p, W, p(W), p(W), p(I), p(I), p(I), p(I), p(B)]
    lib.XQueryTree.restype = B
    lib.XQueryTree.argtypes = [ctypes.c_void_p, W, p(W), p(W), p(p(W)), p(U)]
    lib.XFree.argtypes = [ctypes.c_void_p]
    return lib


def _xext():
    """libXext 的句柄（ctypes 按 soname 缓存 ⇒ 全进程同一个对象，argtypes 设一次就够）。

    ★ 两处 ctypes 声明都是踩过坑才写成这样的：
      1) 不声明 argtypes 时 ctypes 把 Python int 当 C `int`（32 位）传 ⇒ `Display*` 被截断，当场段死。
      2) `XShapeQueryExtents` 的 `bounding_shaped` / `input_shaped` 在 Xlib 头里是 **`Bool`，
         而 Xlib 的 `Bool` 是 `int`（4 字节）**。按 1 字节 `c_bool` 声明 ⇒ 服务端往一字节对象
         连写四字节，读回来的是内存噪声：上一版报的 `(False, False)` 就是这么来的
         （那条明明与"直接 ShapeInput 已经改掉命中测试"这条实测相互矛盾）。
    """
    lib = ctypes.cdll.LoadLibrary("libXext.so.6")
    p = ctypes.POINTER
    lib.XShapeQueryExtents.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong,
        p(ctypes.c_int), p(ctypes.c_int), p(ctypes.c_int), p(ctypes.c_int),
        p(ctypes.c_uint), p(p(XRectangle)),
        p(ctypes.c_int), p(ctypes.c_int), p(ctypes.c_int),
        p(ctypes.c_uint), p(p(XRectangle)),
    ]
    lib.XShapeQueryExtents.restype = ctypes.c_int
    return lib


def root_of(dpy) -> int:
    lib = _x11()
    return int(lib.XRootWindow(dpy, lib.XDefaultScreen(dpy)))


def open_display():
    lib = _x11()
    d = lib.XOpenDisplay(None)
    if not d:
        raise RuntimeError("XOpenDisplay 失败（没有 DISPLAY？XWayland 没起来？）")
    return d


def x_geometry(dpy, wid) -> tuple[int, int, int, int]:
    """**服务端**记着的绝对几何：尺寸来自 XGetGeometry，原点来自 XTranslateCoordinates→根窗。

    不读 `Qt.geometry()`——摆位由合成器说了算，Qt 报的可能是客户端的请求值。
    """
    lib = _x11()
    root_ret = ctypes.c_ulong(0)
    gx, gy = ctypes.c_int(), ctypes.c_int()
    gw, gh = ctypes.c_uint(), ctypes.c_uint()
    bw, depth = ctypes.c_uint(), ctypes.c_uint()
    rc = lib.XGetGeometry(dpy, ctypes.c_ulong(wid), ctypes.byref(root_ret),
                          ctypes.byref(gx), ctypes.byref(gy), ctypes.byref(gw), ctypes.byref(gh),
                          ctypes.byref(bw), ctypes.byref(depth))
    if rc == 0:
        raise RuntimeError(f"XGetGeometry(id={wid}) 失败")
    dx, dy = ctypes.c_int(), ctypes.c_int()
    child = ctypes.c_ulong(0)
    rc = lib.XTranslateCoordinates(dpy, ctypes.c_ulong(wid), ctypes.c_ulong(root_of(dpy)),
                                   0, 0, ctypes.byref(dx), ctypes.byref(dy), ctypes.byref(child))
    if rc == 0:
        raise RuntimeError(f"XTranslateCoordinates(id={wid}) 失败")
    return int(dx.value), int(dy.value), int(gw.value), int(gh.value)


def x_hit(dpy) -> tuple[int, int, int, bool]:
    """根窗上的命中测试：返回 (根x, 根y, 被指针对着的根窗子窗 id, same_screen)。

    X 服务端这条追踪本来就按 **ShapeInput** 裁 ⇒ 问的是"服务端认不认这个输入形状"，
    不是"Qt 心里怎么想"。
    """
    lib = _x11()
    root_ret, child_ret = ctypes.c_ulong(0), ctypes.c_ulong(0)
    rx, ry, wx, wy = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    same = ctypes.c_int()
    lib.XQueryPointer(dpy, ctypes.c_ulong(root_of(dpy)), ctypes.byref(root_ret),
                      ctypes.byref(child_ret), ctypes.byref(rx), ctypes.byref(ry),
                      ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(same))
    return int(rx.value), int(ry.value), int(child_ret.value), bool(same.value)


def x_stack(dpy) -> list[int]:
    """根窗直接子窗的 **X 侧**叠序，从底到顶（`XQueryTree` 的返回顺序即此）。

    加它的出处：fallthrough 那一档 `windowraise` 之后，`挂前` 命中的仍是背景窗，
    光看一个命中数分不清"请求没生效"还是"生效了但命中测试不按 X 侧叠序走"。
    这一列把 X 侧的事实摆出来，两边一对照才定位得准。
    """
    lib = _x11()
    root = ctypes.c_ulong(root_of(dpy))
    root_ret, parent_ret = ctypes.c_ulong(0), ctypes.c_ulong(0)
    children = ctypes.POINTER(ctypes.c_ulong)()
    n = ctypes.c_uint(0)
    rc = lib.XQueryTree(dpy, root, ctypes.byref(root_ret), ctypes.byref(parent_ret),
                        ctypes.byref(children), ctypes.byref(n))
    if rc == 0:
        raise RuntimeError("XQueryTree(根窗) 失败")
    try:
        return [int(children[i]) for i in range(n.value)]
    finally:
        if children:
            lib.XFree(ctypes.cast(children, ctypes.c_void_p))


def xshape_flags(dpy, wid) -> tuple:
    """服务端记的形状位：(bounding_shaped, input_shaped, bounding 矩形数, input 矩形数)。

    rc!=1 ⇒ 全 None，不硬猜。这一路问的是**环节①**——Qt 的 xcb 插件到底有没有发出形状请求；
    与 `x_hit`（环节②，服务端按不按形状裁输入）是同源不同问法，两条都要有才能定位断点。
    """
    xext = _xext()          # argtypes 已在 _xext() 设齐
    kind = ctypes.c_int()
    b_shaped, i_shaped = ctypes.c_int(), ctypes.c_int()
    b_x, b_y, i_x, i_y = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    n_bounding, n_input = ctypes.c_uint(), ctypes.c_uint()
    brects = ctypes.POINTER(XRectangle)()
    irects = ctypes.POINTER(XRectangle)()
    rc = xext.XShapeQueryExtents(ctypes.c_void_p(dpy), ctypes.c_ulong(wid),
                                 ctypes.byref(kind), ctypes.byref(b_shaped),
                                 ctypes.byref(b_x), ctypes.byref(b_y), ctypes.byref(n_bounding),
                                 ctypes.byref(brects),
                                 ctypes.byref(i_shaped), ctypes.byref(i_x), ctypes.byref(i_y),
                                 ctypes.byref(n_input), ctypes.byref(irects))
    if rc != 1:
        return (None, None, None, None)
    lib = _x11()
    lib.XFree.argtypes = [ctypes.c_void_p]
    for ptr in (brects, irects):
        if ptr:
            lib.XFree(ctypes.cast(ptr, ctypes.c_void_p))
    return (bool(b_shaped.value), bool(i_shaped.value), int(n_bounding.value), int(n_input.value))


def x_mouse(xdo: str) -> tuple[int, int, int]:
    out = subprocess.run([xdo, "getmouselocation", "--shell"],
                         capture_output=True, text=True, check=True).stdout
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return int(kv["X"]), int(kv["Y"]), int(kv["WINDOW"])


def warp(xdo: str, app, x: int, y: int) -> tuple[int, int]:
    subprocess.run([xdo, "mousemove", str(x), str(y)], check=True)
    pump(app, 200)
    px, py, _w = x_mouse(xdo)
    return px, py


def apply_shape(dpy, wid: int, rects) -> None:
    """自己发那条 XShape 请求（`ShapeInput` + `ShapeSet`，取值取自产品，与 `_x11_apply_shape` 同一发）。

    为什么不直接调产品的 `_x11_apply_shape`：它每次调用都 `_xrectangle_type()` **现造一个新类**，
    而 `_enable_x11` / `_x11_set_shape` 的 argtypes 是拿**另一个**现造类声明的——
    ctypes 认类不认字段布局 ⇒ 每次调用必 `ArgumentError`。这条真机实测见本探针的运行记录。
    """
    from meapet.desktop.click_through import ShapeInput, ShapeSet

    xext = _xext()
    xext.XShapeCombineRectangles.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(XRectangle), ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ]
    xext.XShapeCombineRectangles.restype = ctypes.c_int
    rect_list = list(rects)
    arr = (XRectangle * len(rect_list))() if rect_list else None
    for i, (x, y, w, h) in enumerate(rect_list):
        arr[i].x, arr[i].y, arr[i].width, arr[i].height = x, y, w, h
    xext.XShapeCombineRectangles(dpy, wid, ShapeInput, 0, 0, arr, len(rect_list), ShapeSet, 0)
    _x11().XSync(dpy, 0)


# --------------------------------------------------------------- 被测窗口链 ──

def product_contour_region(win):
    """产品自己的算法：三角形轮廓 ⇒ 右下半「在矩形内、在轮廓外」。"""
    from meapet.desktop.render_host import Live2DViewportLayout, calculate_live2d_window_region

    layout = Live2DViewportLayout(
        widget_x=0, widget_y=0, widget_width=win.width(), widget_height=win.height(),
        window_width=win.width(), window_height=win.height())
    shape = {"enabled": True, "contours": ({"operation": "add", "points": TRIANGLE},)}
    return calculate_live2d_window_region(layout, shape)


def make_window(app, *, color: str, size: int, title: str):
    """开一扇置顶无边框的临时窗。**不带** X11BypassWindowManagerHint：
    人工口径是"不是悬浮窗就一律忽略"，hint 是请求不是事实 ⇒ 能不能判交给 `float_gate()` 问合成器。

    `title` 不是装饰：与 niri 对表只有标题这一条路可走（实测 XWayland 窗在 niri 里
    记的 `pid` 是 XWayland 自己的 1730，不是本进程），所以每扇窗自打唯一标记。
    """
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QWidget

    win = QWidget()
    win.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    win.setWindowTitle(title)
    win.setStyleSheet(f"background:{color};")
    win.resize(size, size)
    win.show()
    pump(app, 700)
    return win


# ------------------------------------------------------- 与合成器对表（niri） ──

def niri_rows(marker: str) -> list[dict]:
    """问 **niri 自己**：带这个标记的窗在它眼里是什么状态。

    ★ 判据为什么换掉（人工纠正 2026-09-29，逐字）：「那个不是悬浮态，**平铺可以正常改大小**」
      ⇒ 旧判据"客户端请求尺寸被服务端照做 ⇒ 已悬浮"**作废**：尺寸这一半分不开悬浮与平铺，
      上一轮 fallthrough 我就是拿它当悬浮自证的，那句结论不认。
      现在只读合成器自报的 `is_floating`，不再从别的量倒推。
    ★ 连接键为什么只能是标题：实测同一份旗标（Frameless + StayOnTop）裸窗，niri 记
      `pid=1730`（XWayland 的进程）而 `title` 与 `setWindowTitle` 逐字相同 ⇒ 只有标题对得上。
    """
    out = subprocess.run(["niri", "msg", "--json", "windows"],
                         capture_output=True, text=True, check=True)
    return [r for r in json.loads(out.stdout) if str(r.get("title") or "").startswith(marker)]


def niri_floating(title: str):
    """合成器怎么说这一扇窗的悬浮态：True／False／None（问不到或对不上号）。

    单独给一个"只问一题"的入口，是因为跑遍过程中每遍都要复查一次，
    不能把整套闸门（含改尺寸、请人工）都重跑一遍。
    """
    try:
        rows = niri_rows(title)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return None
    if not rows:
        return None
    return rows[0].get("is_floating") is True


def settled_hit(app, dpy, xdo: str, wid: int, xy, tries: int = 10):
    """把指针挪到 xy，**等服务端自己报的指针坐标真等于 xy** 再取命中。

    为什么要等：`xdotool mousemove` 之后立刻 `XQueryPointer`，偶发读到指针还在别处，
    命中就报成 `0`（既不是我方也不是背景，是根窗）。本机实测：不加这道等待时，
    同一条腿 10 遍里漂过 1 遍。返回 (命中 id, 服务端自报指针坐标, 被测窗服务端几何, 是否落位)。
    """
    hit = pos = geo = None
    for _ in range(tries):
        warp(xdo, app, *xy)
        hx, hy, hit, _same = x_hit(dpy)
        pos = (hx, hy)
        geo = x_geometry(dpy, wid)
        if pos == xy:
            return hit, pos, geo, True
    return hit, pos, geo, False


def points(fx: int, fy: int, fw: int, fh: int) -> tuple[tuple[int, int], tuple[int, int]]:
    return ((fx + round(fw * KEEP_AT[0]), fy + round(fh * KEEP_AT[1])),
            (fx + round(fw * CUT_AT[0]), fy + round(fh * CUT_AT[1])))


def float_gate(app, dpy, xdo: str, wid: int, size: int, wait_s: int, title: str):
    """**悬浮闸门**：只认合成器自报的 `is_floating`，不从别的量倒推。

    判据为什么是第三版（两条人工纠正逐字）：
      ·「不能假设 X11BypassWindowManagerHint 平铺器就不接管，我必须提醒你，你一直在开平铺窗口」
        ⇒ v1 作废：hint 是一条请求，不是事实。
      ·「那个不是悬浮态，**平铺可以正常改大小**」
        ⇒ v2 作废：上一版我拿"请求尺寸被服务端照做"当悬浮的证据，这条倒推不成立。
      · v3：读 `niri msg --json windows` 里 `is_floating` 那一列——合成器自己怎么说就怎么算。
    实测依据（同旗标的裸窗两试）：niri 记 `is_floating=False`、请求 321×321 被吃成 659×736；
    对表键只有 `title`（同一条记录里 `pid` 是 XWayland 的 1730，不是本进程）。

    尺寸仍然发、仍然回读，但只当**取样用的事实**：摆位与尺寸都归合成器，回读多少就按多少取样
    （`windowmove` 在 XWayland 这边整个不被认，位置从来就争不来）。
    返回悬浮定稿后的服务端几何；不悬浮／量具缺席 ⇒ None（调用方出"不可判"，不许在平铺态下判）。

    焦点这一半是本轮补上的：Mod+V 作用于**当前聚焦**的窗，而两只窗叠在一起时人无从知道
    焦点在谁身上（实测这一跑两只都在，`is_focused` 只给了后创建的那只）⇒ 每轮先把焦点
    交给要判的那扇，再把合成器报的聚焦位一起打出来，让人看见"我这一下会打在谁身上"。
    """
    pump(app, FIRST_MS)                      # 人工得先看清窗摆成什么样，才谈得上反应
    deadline = time.time() + wait_s
    while True:
        subprocess.run([xdo, "windowactivate", str(wid)], check=False)
        subprocess.run([xdo, "windowsize", str(wid), str(size), str(size)], check=False)
        pump(app, 600)
        geo = x_geometry(dpy, wid)
        try:
            rows = niri_rows(title)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            say(f"VERDICT[{title}]", f"不可判｜问不到合成器（`niri msg` 这条量具不灵：{exc}）"
                                     " ⇒ 悬浮态无从自证，这一格不出结论")
            return None
        if not rows:
            say(f"VERDICT[{title}]", f"不可判｜niri 的记录里没有标题 `{title}` 这一条"
                                     " ⇒ 对表键断了（标题被改过？压根不是这扇窗？），不猜")
            return None
        row = rows[0]
        wsz = row.get("layout", {}).get("window_size")
        if row.get("is_floating") is True:
            say(f"悬浮[{title}]", f"niri 自报 is_floating=True｜它记的尺寸={wsz}"
                                  f"｜X 侧回读几何={geo} ⇒ 可以判（取样以 X 侧回读为准）")
            return geo
        if time.time() >= deadline:
            say(f"VERDICT[{title}]", f"不可判｜等了 {wait_s} s，niri 仍自报"
                                     f" is_floating={row.get('is_floating')}（尺寸 {wsz}）"
                                     " ⇒ 平铺态下量到的形状不算产品那一档，不出结论："
                                     "请对这扇窗按 **Mod+V** 转成悬浮窗后重跑")
            return None
        say(f"待悬浮[{title}]", f"niri 自报 is_floating=False（它记的尺寸={wsz}，"
                                f"聚焦={row.get('is_focused')}）"
                                f"｜剩余约 {deadline - time.time():.0f} s"
                                f" ⇒ **请按 Mod+V**（Mod+V 打在聚焦窗上，我已每轮把焦点"
                                f"切到这扇；不行就自己点一下它再按）"
                                f"，每 ~{POLL_MS // 1000} s 重问一次合成器")
        pump(app, POLL_MS)


def stack_gate(app, dpy, xdo: str, wid: int, bid: int, wait_s: int, arrange):
    """把"我方在背景之上"这件事做到，**做完还得回头看一眼叠放还在不在**；成功返回 arrange() 的结果。

    为什么要有这道闸（本轮实测出处）：`xdotool windowraise` 发过之后，我方框内那个点在
    5 遍里 **5/5 命中的都是背景窗** ⇒ 红窗根本没在上层，于是"轮廓外落到背景"那 5 个 ✓
    全是白给的，谁都没资格当证据。X 侧的 raise 是一条**请求**，落不落归合成器——
    与 `X11BypassWindowManagerHint`、`windowmove` 同一类坑：客户端提请求，服务端记账才是事实。

    为什么要和叠放**互相收尾**（下一轮实测出处）：这一版压序当场就成功了（命中=我方、
    X 侧叠序 我方=10 背景=9），可紧接着 5 遍仍全报"两窗已不叠" ⇒ raise 之后合成器把窗挪了位。
    ⇒ 两件事一轮一轮地拧：摆好 → 压序 → 再回读叠放，任一不成就请人工再来一次，都成了才交出去。
    判据全程只有回读这两条：`arrange()` 说两窗还叠着、轮廓外那个取样点的命中说我方在上层。
    """
    order = {}

    def ask(pt) -> int:
        """回读命中 + 同刻抓一份 X 侧叠序，存进 `order` 供逐条对照。"""
        hit, _pos, _geo, _ok = settled_hit(app, dpy, xdo, wid, pt)
        stack = x_stack(dpy)
        order["last"] = (stack.index(wid) if wid in stack else None,
                         stack.index(bid) if bid in stack else None)
        return hit

    ATTEMPTS = (
        ("windowraise", [[xdo, "windowraise", str(wid)]]),
        ("windowactivate", [[xdo, "windowactivate", str(wid)]]),
        ("windowraise+windowactivate",
         [[xdo, "windowraise", str(wid)], [xdo, "windowactivate", str(wid)]]),
    )

    def left() -> str:
        return f"剩余约 {max(0.0, deadline - time.time()):.0f} s"

    pump(app, FIRST_MS)                      # 人工得先看清两只窗摆成什么样
    deadline = time.time() + wait_s
    while time.time() < deadline:
        arr = arrange()
        if arr is None:
            say("待叠放", f"我方={x_geometry(dpy, wid)} 背景={x_geometry(dpy, bid)}｜{left()}"
                          f" ⇒ **请把红窗整个拖进绿窗里**（罩住即可、不必精确，"
                          f"每 ~{POLL_MS // 1000} s 回读一次服务端）")
            pump(app, POLL_MS)
            continue
        cut_xy = arr[2][1]                     # arrange() 第 3 项 = (轮廓内, 轮廓外)
        ok = False
        for which, argvs in ATTEMPTS:
            for argv in argvs:
                subprocess.run(argv, check=False)
            pump(app, 400)
            hit = ask(cut_xy)
            if hit == wid:
                wi, bi = order["last"]
                say("压序", f"{which} 生效｜命中={hit}==我方｜X 侧叠序（底→顶）我方={_wi_pos(wi)}、"
                            f"背景={_wi_pos(bi)}")
                ok = True
                break
            wi, bi = order["last"]
            say("压序未生效", f"试了 {which}｜命中={hit}（{name_of(hit, wid, bid)}）"
                              f"｜X 侧叠序 我方={_wi_pos(wi)} 背景={_wi_pos(bi)}"
                              "（数字没变＝X 侧压根没动；变了＝动了但命中仍不归我方）")
        if not ok:
            hit = ask(cut_xy)
            while time.time() < deadline and hit != wid:
                say("待压序", f"我方={wid} 背景={bid}｜{cut_xy} 处命中={hit}"
                              f"（{name_of(hit, wid, bid)}）｜{left()}"
                              f" ⇒ **请点一下红窗**把它提到最前（每 ~{POLL_MS // 1000} s 重问一次命中）")
                pump(app, POLL_MS)
                hit = ask(cut_xy)
            if hit == wid:
                say("压序", f"人工点击生效｜命中={hit}==我方")
                ok = True
        if not ok:
            break
        arr2 = arrange()
        if arr2 is not None:
            say("叠放+压序", f"两件事同时成立：我方={arr2[1]} 在背景={arr2[0]} 之内且在上层")
            return arr2
        say("压序后叠散了", f"刚压住序，回读却是我方={x_geometry(dpy, wid)} "
                            f"背景={x_geometry(dpy, bid)} ⇒ raise 把窗挪走了；再摆一次，我再压一次")

    say("VERDICT-压序", f"不可判｜等了 {wait_s} s，'两窗叠着'与'我方在上层'这两件事"
                        "没能同时成立 ⇒ 这侧争不来，穿透那一格不出结论")
    return False


def _wi_pos(idx):
    return "不在根窗子表" if idx is None else idx


def name_of(hit, wid, bid) -> str:
    if hit == 0:
        return "根窗（那儿没有 X 侧子窗，或指针没落位）"
    return {wid: "我方", bid: "背景"}.get(hit, "别处")


# ------------------------------------------------------------------ stage probe

def stage_probe(app, dpy, xdo: str, repeat: int, wait_float_s: int) -> int:
    """①②：Qt 有没有发形状请求 + 这台服务端认不认输入形状。**只在悬浮窗上判**。"""
    title = f"{TITLE_MARK}·probe"
    win = make_window(app, color="#b71c1c", size=SIDE, title=title)
    wid = int(win.winId())
    say("口径", "产品那扇窗是**悬浮/置顶**的 ⇒ 本档只认**合成器自报悬浮**的读数；"
                "悬浮与否不靠倒推（尺寸照做不算证据——人工点名：平铺可以正常改大小），"
                "只读 `niri msg windows` 的 `is_floating`")
    geo = float_gate(app, dpy, xdo, wid, SIDE, wait_float_s, title)
    if geo is None:
        win.deleteLater()
        return 1
    fx, fy, fw, fh = geo
    pump(app, 400)                       # 让 Qt 收到这次 configure，layout 才跟着改
    region = product_contour_region(win)
    keep_xy, cut_xy = points(fx, fy, fw, fh)
    say("被测窗", f"id={wid} 服务端绝对几何=({fx},{fy},{fw}x{fh})"
                  f"｜Qt 自报=({win.x()},{win.y()},{win.width()}x{win.height()})")
    say("轮廓（闸门时）", "calculate_live2d_window_region → "
        + ("None" if region is None else f"{region.rectCount()} 块，外框 {region.boundingRect()}"
                                          "｜跑起来后每遍按当遍尺寸重算"))
    if region is None or region.isEmpty():
        say("VERDICT", "✗ 轮廓算出来是空 ⇒ 我方请求本身没成立，本臂不判")
        win.deleteLater()
        return 1
    say("取样点", f"轮廓内={keep_xy}｜矩形内轮廓外={cut_xy}（**按闸门几何**，跑起来每遍会重算）")

    home = x_mouse(xdo)
    say("旁证｜XShape 位", f"{xshape_flags(dpy, wid)}"
                          "＝(bounding_shaped, input_shaped, bounding 矩形数, input 矩形数)"
                          "——本机这路自相矛盾（shaped 位 False 却报一大把矩形）⇒ 只作旁证")
    say("判权分配", "判权全在命中测试；四处噪声各有守卫：合成器不再自报悬浮／指针没落位／"
                    "Qt 与服务端尺寸对不上／跑中途几何变了 ⇒ 那一遍整条作废，"
                    "不进分子也不进分母")
    try:
        return run_legs(app, dpy, xdo, win, wid, title, geo, home, repeat, wait_float_s)
    finally:
        subprocess.run([xdo, "mousemove", str(home[0]), str(home[1])], check=False)
        win.deleteLater()
        pump(app, 200)


# 每腿的期望：True ⇒ 该点**应**命中我方窗；False ⇒ 应被裁掉（不命中我方）
# 「轮廓直发」是**判权腿**：同一个区域不经 Qt、由探针自己送进 ShapeInput ⇒
# 它裁得动而 setMask 裁不动，断点才真的落在「Qt 没把 mask 送进 XShape（环节①）」。
# 其后必须紧跟「直发还原」，否则残留的直发形状会替 setMask 假装成功。
LEGS = (("基线", True), ("对照", False), ("还原", True),
        ("轮廓直发", False), ("直发还原", True),
        ("遮罩外", False), ("遮罩内", True), ("撤遮罩", True))


def run_legs(app, dpy, xdo, win, wid, title, gate_geo, home, repeat: int, wait_s: int) -> int:
    """八腿连跑 `repeat` 遍并**计数**，而不是跑一遍下结论。

    为什么必须每遍自己守：这条链的读数在**三件事**上会自己飘，而它们都能把"通"伪装出来——
    ① 悬浮守不住：**合成器**一转头就不再自报 `is_floating`（本机实测发生：闸门里还悬浮，
       跑起来又记回平铺格 659×736）。此时轮廓（按 240 算）只盖住左上角，轮廓外那个取样点
       当然"被裁"——**这是量具假象**。判悬浮只认合成器自报这一条，**不拿尺寸倒推**：
       人工点名「平铺可以正常改大小」，尺寸这一半分不开两态。
    ② 指针落位有竞态：`xdotool mousemove` 之后立刻 `XQueryPointer`，偶发读到指针还在别处
       （表现为 `命中=0`＝指到根窗，既不是我方也不是背景）；
    ③ Qt 自报尺寸与服务端几何不一致（轮廓盖错了框），或跑中途窗被挪走（八腿量的不是
       同一个物理点）。
    ⇒ 三种都改成**作废该遍**：不进分子也不进分母。干净样本不足 3 遍就出「不可判」。
    """
    gw0, gh0 = gate_geo[2], gate_geo[3]      # 回闸时重新请求的尺寸（取样按当遍几何重算，位置不争）
    tally = {name: 0 for name, _want in LEGS}
    counted = 0
    voids = []

    def read(leg, xy, sink):
        hit, pos, geo, ok = settled_hit(app, dpy, xdo, wid, xy)
        sink[leg] = (hit, pos, geo, ok)
        return hit

    for i in range(1, repeat + 1):
        # ── 守卫①：这一遍开始时，**合成器还自报悬浮**吗（本机实测真的会丢）。
        #    判据只有 `is_floating` 这一条；尺寸不在这里判悬浮（平铺也能改大小），
        #    尺寸归守卫④管——那里问的是"按 Qt 尺寸算的轮廓还盖不盖得准这个框"。
        fl = niri_floating(title)
        if fl is not True:
            voids.append(f"第 {i} 遍：合成器不再自报悬浮（is_floating={fl}）")
            say(f"第 {i} 遍", f"**作废**｜{voids[-1]} ⇒ 平铺态的读数不进统计；"
                              "下面重新等一次 Mod+V")
            new_gate = float_gate(app, dpy, xdo, wid, gw0, wait_s, title)
            if new_gate is None:
                voids.append("回闸超时 ⇒ 停止跑遍")
                break
            gate_geo = new_gate
            continue
        geo = x_geometry(dpy, wid)
        if (win.width(), win.height()) != geo[2:]:
            # 尺寸对不上先拧一次（悬浮态下合成器照做客户端请求），再不对就整遍作废。
            subprocess.run([xdo, "windowsize", str(wid), str(gw0), str(gh0)], check=False)
            pump(app, 400)
            geo = x_geometry(dpy, wid)
        # ── 守卫④：Qt 自报尺寸与服务端几何不一致 ⇒ 轮廓盖错了框，读数全是假象。
        if (win.width(), win.height()) != geo[2:]:
            voids.append(f"第 {i} 遍：Qt 报 {win.width()}x{win.height()} 而服务端记 "
                         f"{geo[2]}x{geo[3]}（重请求也不照做）")
            say(f"第 {i} 遍", f"**作废**｜{voids[-1]}")
            continue
        region = product_contour_region(win)     # 按当遍尺寸重算：别拿旧轮廓去裁新窗
        if region is None or region.isEmpty():
            voids.append(f"第 {i} 遍：轮廓算出来是空（Qt 自报 {win.width()}x{win.height()}）")
            say(f"第 {i} 遍", f"**作废**｜{voids[-1].split('：', 1)[1]}")
            continue
        contour_rects = tuple((r.x(), r.y(), r.width(), r.height()) for r in region.rects())
        gx, gy, gw, gh = geo
        keep_xy, cut_xy = points(gx, gy, gw, gh)
        full = (0, 0, gw, gh)
        seen = {}
        # 起点必须干净：上一轮的形状不许漏进这一轮
        win.clearMask()
        apply_shape(dpy, wid, (full,))
        pump(app, 400)
        read("基线", cut_xy, seen)                       # 无形状 ⇒ 该点应命中我方
        apply_shape(dpy, wid, ((0, 0, gw // 4, gh // 4),))
        pump(app, 400)
        read("对照", cut_xy, seen)                       # 直接 ShapeInput 1/4 ⇒ 应被裁
        apply_shape(dpy, wid, (full,))
        pump(app, 400)
        read("还原", cut_xy, seen)                       # 撤掉对照形状 ⇒ 应回到我方
        apply_shape(dpy, wid, contour_rects)             # 同一个区域，绕开 Qt 直发
        pump(app, 400)
        read("轮廓直发", cut_xy, seen)
        apply_shape(dpy, wid, (full,))                   # 必须还原：不许残留形状替下一腿假装成功
        pump(app, 400)
        read("直发还原", cut_xy, seen)
        win.setMask(region)                              # 真那条链：render_host.py:1653 的唯一下手处
        pump(app, 500)
        read("遮罩外", cut_xy, seen)
        read("遮罩内", keep_xy, seen)
        win.clearMask()
        pump(app, 500)
        read("撤遮罩", cut_xy, seen)

        # ── 守卫②：每一腿都得指针真落位；守卫③：八腿之间几何不许变
        not_settled = [n for n, _w in LEGS if not seen[n][3]]
        if not_settled:
            voids.append(f"第 {i} 遍：指针没落到目标点（{not_settled}）")
            say(f"第 {i} 遍", f"**作废**｜指针没落位：{not_settled} ⇒ 不进统计")
            continue
        moved = [n for n, _w in LEGS if seen[n][2] != geo]
        if moved:
            voids.append(f"第 {i} 遍：跑中途服务端几何变了（{moved}）")
            say(f"第 {i} 遍", f"**作废**｜跑中途几何变了：{moved} ⇒ 不进统计")
            continue

        marks = []
        for name, want in LEGS:
            hit = seen[name][0]
            good = (hit == wid) if want else (hit != wid)
            tally[name] += int(good)
            marks.append(f"{name}={'✓' if good else f'✗(命中={hit})'}")
        counted += 1
        say(f"第 {i} 遍", f"取样={cut_xy} 形状={gw}x{gh}"
                          + "｜" + " ".join(marks))

    print("\n=== 判定 ===")
    say("样本", f"计入 {counted}/{repeat} 遍"
                + ("" if not voids else "｜作废原因：" + "；".join(voids)))
    if counted < 3:
        say("VERDICT", f"不可判｜{repeat} 遍里只凑出 {counted} 遍干净样本 ⇒ 这台环境连测量条件"
                       "都守不住（悬浮会丢／指针会飘／几何会改），不给这条链下任何结论")
        return 1
    for name, want in LEGS:
        say(f"{name}（应{'命中我方' if want else '被裁'}）", f"{tally[name]}/{counted} 遍符合")

    prem = ("基线", "对照", "还原", "轮廓直发", "直发还原", "遮罩内")
    hard_fail = [n for n in prem if tally[n] != counted]
    if hard_fail:
        say("VERDICT", f"不可判｜{hard_fail} 这些**判权前提**有遍数不符合 ⇒ 量具或这台服务端不稳，"
                       "遮罩外那一腿的数不作结论")
        return 1
    if tally["遮罩外"] == 0:
        say("VERDICT", f"不通｜{counted} 遍里**同一个区域直发 ShapeInput 每次都裁得动**"
                       "（轮廓直发判权腿全绿），而 `setMask(轮廓)` **一次都没**裁动，"
                       "且撤遮罩每次都能回到我方"
                       " ⇒ 断点在 **Qt 没把 mask 送进 XShape（环节①）**，不在算法、不在轮廓计算")
        return 0
    if tally["遮罩外"] == counted and tally["撤遮罩"] == counted:
        say("VERDICT", f"成立｜{counted} 遍全绿，且每遍都复查过**合成器仍自报悬浮**"
                       "（`is_floating`）、**Qt 与服务端尺寸一致**、指针**真落位**："
                       "`setMask(产品轮廓)` 每次都裁掉轮廓外那个点、轮廓内每次照旧命中、"
                       "`clearMask` 后每次都能回到我方"
                       " ⇒ **X11 支路上「轮廓＝热区」在服务端输入形状这一层稳定生效**")
        return 0
    say("VERDICT", f"不判｜遮罩那两腿在 {counted} 遍里漂（遮罩外 {tally['遮罩外']}/{counted}、"
                   f"撤遮罩 {tally['撤遮罩']}/{counted}）而判权前提全绿 ⇒ 这是**链路不稳定**，"
                   "不是「能」也不是「不能」。要么找齐漂移的因（时序？XWayland 的输入区转发？），"
                   "要么这一格就记作不可判")
    return 1


# ------------------------------------------------------------- stage fallthrough

def stage_fallthrough(app, dpy, xdo: str, wait_float_s: int, repeat: int) -> int:
    """环节③：用户手指那一档。两只窗**都要先过悬浮闸门**（人工 Mod+V），叠放才谈得上穿透。

    这一档只有**一条腿被量**（轮廓外那个点挂上轮廓后落到背景），其余三件事是**判权前提**，
    每遍在遍内逐条把关，任何一条破 ⇒ 整遍作废（不进分子也不进分母）：
      · 挂前命中我方（同一物理点，没挂轮廓时就得是我方，否则"落到背景"是**叠序**给的）；
      · 轮廓内对照在（挂轮廓后仍命中我方 ⇒ 中途背景没爬到我方上面，这一遍的翻转才归给形状）；
      · 撤轮廓回得来（形状请求确实进出过，不是一次性丢了）。
    三条前提为什么必须做成"作废"而不是"计分腿"：本机 20 遍实测里 `遮罩外` 看着 19/19 全绿，
    可其中 **14 遍连"挂前"就已经命中背景**——那 14 个 ✓ 是叠序翻了造成的。把它们计进分母，
    出来的就是假绿（人工口径「穿透不稳定应为误报」说的正是这个）。

    ★ 本档的**覆盖面边界**（出结论时必须一起报，不许省略）：背景窗同样是 Qt/xcb 开的窗
    ⇒ 它在合成器眼里也是 **XWayland 面**。所以"落到背景"证到的是**穿透到另一扇 X 侧窗**，
    不自动等于穿透到底下的**原生 Wayland 应用**。这一条差别是结构性的（XWayland 把整块
    矩形当成它的表面），不是多跑几遍能收敛的。
    """
    back_t = f"{TITLE_MARK}·背景"
    mine_t = f"{TITLE_MARK}·我方"
    back = make_window(app, color="#2e7d32", size=BACK, title=back_t)   # 绿：露绿＝真透到下层
    win = make_window(app, color="#b71c1c", size=SIDE, title=mine_t)     # 红：我方
    wid, bid = int(win.winId()), int(back.winId())
    home = x_mouse(xdo)
    try:
        say("布置", f"我方 id={wid}（标题 `{mine_t}`）｜背景 id={bid}（标题 `{back_t}`）"
                    "｜悬浮与否只问合成器（`niri msg windows` 里 `is_floating` 那一列），"
                    "不拿尺寸倒推——人工点名：平铺可以正常改大小")
        if float_gate(app, dpy, xdo, bid, BACK, wait_float_s, back_t) is None:
            return 1
        if float_gate(app, dpy, xdo, wid, SIDE, wait_float_s, mine_t) is None:
            return 1
        # 位置争不来（XWayland 不吃 windowmove）⇒ 叠放只能请人工 Mod+拖；因此**每遍都按当时**
        # 的服务端几何就地重算取样点。"叠着"与"我方在上层"这两件事由 `stack_gate` 一轮一轮
        # 地拧到同时成立（实测：压序成功之后叠放会散，两道闸不能各过一次就算完）。
        def arrange():
            bgeo, fgeo = x_geometry(dpy, bid), x_geometry(dpy, wid)
            bx, by, bw, bh = bgeo
            fx, fy, fw, fh = fgeo
            if not (bx <= fx and fx + fw <= bx + bw and by <= fy and fy + fh <= by + bh):
                return None
            return bgeo, fgeo, points(fx, fy, fw, fh)

        arr = stack_gate(app, dpy, xdo, wid, bid, wait_float_s, arrange)
        if arr is None:
            return 1
        _bgeo, _fgeo, (keep_xy, cut_xy) = arr
        say("取样点", f"轮廓内={keep_xy}｜轮廓外（应落到背景）={cut_xy}｜我方={wid} 背景={bid}")
        say("重复", f"{repeat} 遍：这一档每遍都要重新验"
                    "「两窗仍叠着／我方仍在上层／指针真落位／撤轮廓后回得来」，"
                    "凑不齐 3 遍干净样本就不给结论")

        tally = {"遮罩外": 0}
        counted = 0
        voids = []
        for i in range(1, repeat + 1):
            arr = arrange()
            if arr is None:
                voids.append(f"第 {i} 遍：两窗已不叠（我方={x_geometry(dpy, wid)} "
                             f"背景={x_geometry(dpy, bid)}，压序成功时是 我方={_fgeo} 背景={_bgeo}）")
                say(f"第 {i} 遍", f"**作废**｜{voids[-1]}")
                continue
            bgeo, fgeo, (keep_i, cut_i) = arr
            moved = "｜移位后重算取样点" if (bgeo, fgeo) != (_bgeo, _fgeo) else ""
            region = product_contour_region(win)      # 每遍重算：layout 跟 Qt 尺寸走
            stale = (win.width(), win.height()) != (fgeo[2], fgeo[3])
            h0, _p0, _g, ok0 = settled_hit(app, dpy, xdo, wid, cut_i)
            if ok0 and h0 != wid:                     # 开局叠序不在我方这边 ⇒ 先按一次再问
                subprocess.run([xdo, "windowraise", str(wid)], check=False)
                pump(app, 400)
                h0, _p0, _g, ok0 = settled_hit(app, dpy, xdo, wid, cut_i)
            win.setMask(region)
            pump(app, 500)
            hc, _pc, _g, okc = settled_hit(app, dpy, xdo, wid, cut_i)
            hk, _pk, _g, okk = settled_hit(app, dpy, xdo, wid, keep_i)
            win.clearMask()
            pump(app, 500)
            hb, _pb, _g, okb = settled_hit(app, dpy, xdo, wid, cut_i)
            if not all((ok0, okc, okk, okb)):
                voids.append(f"第 {i} 遍：指针没落位（挂前{ok0} 遮罩外{okc} 遮罩内{okk} 撤遮罩{okb}）")
                say(f"第 {i} 遍", f"**作废**｜{voids[-1]}")
                continue
            # 判权前提**逐条**在遍内把关，任何一条破 ⇒ 整遍作废（不进分子也不进分母）：
            #   ·挂前必须命中我方——否则这一遍问的根本不是我方这扇窗，之后命中落到背景是
            #     **叠序**造成的，与形状无关。本机 20 遍实测 14 遍就是这么被读成「✓落到背景⇒穿透」
            #     的（人工口径：「穿透不稳定应为误报」——那条误报的成因就在这一格）。
            #   ·轮廓内必须照旧命中我方：它是**遍内**的叠序对照。挂轮廓后连轮廓内那个点都
            #     落到背景，说明中途背景爬到了我方上面，这一遍的「轮廓外落到背景」同样不作数。
            #   ·撤轮廓后必须回得来：证明形状请求确实进出过，不是一次性丢了。
            if h0 != wid:
                voids.append(f"第 {i} 遍：挂轮廓前就没命中我方（命中={name_of(h0, wid, bid)}）"
                             "⇒ 叠序不在我方这边，这一遍量的不是我方")
                say(f"第 {i} 遍", f"**作废**｜{voids[-1]}")
                continue
            good = {"遮罩外": hc == bid, "遮罩内": hk == wid, "撤遮罩": hb == wid}
            if not good["遮罩内"]:
                voids.append(f"第 {i} 遍：挂轮廓后轮廓内那个点也没命中我方"
                             f"（命中={name_of(hk, wid, bid)}）⇒ 中途叠序翻了，不是形状裁的")
                say(f"第 {i} 遍", f"**作废**｜{voids[-1]}")
                continue
            # 撤轮廓回不来 = 这一遍结束时**形状没退干净**或**叠序又翻了**，两种都让「轮廓外
            #   落到背景」变得不可解释 ⇒ 同样整遍作废，理由原样写进作废清单给人看。
            if not good["撤遮罩"]:
                voids.append(f"第 {i} 遍：撤轮廓后没回到我方（命中={name_of(hb, wid, bid)}）"
                             "⇒ 形状没退干净或叠序已翻，这一遍不作数")
                say(f"第 {i} 遍", f"**作废**｜{voids[-1]}")
                continue
            tally["遮罩外"] += int(good["遮罩外"])
            counted += 1
            fell = "✓落到背景⇒穿透" if good["遮罩外"] else "✗仍命中我方"
            say(f"第 {i} 遍", f"挂前={h0}✓（我方）｜轮廓内={hk}✓（对照在）→ "
                              f"轮廓外={hc}{fell}"
                              + (f"｜Qt报的尺寸≠服务端{fgeo}" if stale else "") + moved)

        print("\n=== 判定 ===")
        say("样本", f"计入 {counted}/{repeat} 遍"
                    + ("" if not voids else "｜作废原因：" + "；".join(voids)))
        if counted < 3:
            say("VERDICT-fallthrough", f"不可判｜只凑出 {counted} 遍干净样本"
                                       f"（作废 {len(voids)} 遍，理由逐条见上）"
                                       " ⇒ 叠放/落位/对照守不住，这一格不出结论")
            return 1
        say("各腿", f"遮罩外（这一档唯一被量的腿）{tally['遮罩外']}/{counted}"
                    f"｜作废 {len(voids)} 遍——作废判的是三条**判权前提**："
                    "挂前命中我方／轮廓内对照在／撤轮廓回得来")
        if tally["遮罩外"] == counted:
            say("VERDICT-fallthrough",
                f"成立（限 X 侧）｜{counted} 遍里同一个物理点只改 `setMask` ⇒ 命中每次都从"
                f"我方（{wid}）**落到背景窗**（{bid}）"
                " ⇒ X 侧形状至少转发到了**同一台 XWayland 内的另一扇窗**这一层。"
                "⚠ 背景窗也是 XWayland 面 ⇒ **没**证到穿透到底下的**原生 Wayland 应用**，"
                "那一格仍是未测")
            return 0
        if tally["遮罩外"] == 0:
            say("VERDICT-fallthrough",
                f"不通｜{counted} 遍里轮廓外那个点挂上轮廓后**仍命中我方**（撤掉后也回得来，"
                "说明形状请求确实进出过） ⇒ X 服务端按 ShapeInput 裁了自己的命中，"
                "但 XWayland 没把它转成合成器认的表面输入区 ⇒ **用户手指上透不过去**")
            return 0
        say("VERDICT-fallthrough", f"不判｜遮罩外那腿在 {counted} 遍里漂（{tally['遮罩外']}/{counted}）"
                                   "而前提全绿 ⇒ 链路不稳，这一格记不可判")
        return 1
    finally:
        subprocess.run([xdo, "mousemove", str(home[0]), str(home[1])], check=False)
        for w in (win, back):
            w.deleteLater()
        pump(app, 200)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=("probe", "fallthrough"), default="probe")
    ap.add_argument("--wait-float", type=int, default=60,
                    help="等人工把这（两）只窗转悬浮的秒数（niri: Mod+V）；两档都用")
    ap.add_argument("--repeat", type=int, default=5,
                    help="probe：连跑几遍并计数（单遍通过不足以判稳）")
    args = ap.parse_args()

    xdo = shutil.which("xdotool")
    if xdo is None:
        say("VERDICT", "✗ 缺 xdotool ⇒ 移动指针没有量具，本探针不判（不是否证）")
        return 1
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    if app.platformName() != "xcb":
        say("VERDICT", f"✗ Qt 插件={app.platformName()} ≠ xcb ⇒ 跑错平台，不判")
        return 1
    try:
        dpy = open_display()
    except RuntimeError as exc:
        say("VERDICT", f"✗ {exc}")
        return 1
    if args.stage == "fallthrough":
        return stage_fallthrough(app, dpy, xdo, args.wait_float, args.repeat)
    return stage_probe(app, dpy, xdo, args.repeat, args.wait_float)


if __name__ == "__main__":
    raise SystemExit(main())
