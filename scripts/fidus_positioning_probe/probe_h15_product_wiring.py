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
* **A2 退回（位移之前）**：给一帧只有轮廓、没有纹理的图 ⇒ 引擎结构门不过、候选全拒
  ⇒ 一次 `set_position` 都不该发生，`_proxy_rect` 不该动。
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

用法：

    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h15_product_wiring.py

退出码：0 = 四臂都跑到出口且三判据全绿；1 = 量具/环境不可用（不产出任何主张）；
2 = 有臂判红。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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
fact = H12.fact


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


def place_host(app, host, x: int, y: int, fw: int, fh: int):
    """摆 Qt 侧宿主，返回 `_layer_geometry()` 实际读到的矩形（产品的请求值就是它）。"""
    host.resize(fw, fh)
    host.sprite_label.setGeometry(0, 0, fw, fh)
    host.move(x, y)
    H4.pump(app, 350)
    return host._layer_geometry()


def run_until_settled(app, host, timeout: float = LOCATE_TIMEOUT) -> bool:
    """泵 GUI 线程直到 fidus 线程交回结果；返回 True = 仍卡着。"""
    deadline = time.perf_counter() + timeout
    while getattr(host, "_fidus_busy", False) and time.perf_counter() < deadline:
        H4.pump(app, 200)
    return bool(getattr(host, "_fidus_busy", False))


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
    box, off = H12.probe_box(frame)
    if box is None:
        print("VERDICT-WIRING : ✗ 选不出独立量具盒 ⇒ 量具自身失效，不猜")
        return 1

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
    t_mount = H12.box_truth(box, off, "h15_a0")
    if t_mount is None or t_mount[2] < H12.PRESENT_PEAK:
        peak = "-" if t_mount is None else f"{t_mount[2]:.4f}"
        print(f"VERDICT-VISIBLE : ✗ 独立尺子峰 {peak} < {H12.PRESENT_PEAK} ⇒ 我方内容不在屏上，"
              "全案不产出读数")
        teardown(host, app)
        return 1
    want0 = (geom0[0] + geom0[2] / 2.0, geom0[1] + geom0[3] / 2.0)
    err0 = (t_mount[0] - want0[0], t_mount[1] - want0[1])
    fact("A0 真值", f"surface 中心 ({t_mount[0]:.2f},{t_mount[1]:.2f}) 峰={t_mount[2]:.4f}"
                    f"　请求中心 ({want0[0]:.2f},{want0[1]:.2f}) ⇒ 信念误差 "
                    f"({err0[0]:+.2f},{err0[1]:+.2f}) px")
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

    host.places.clear()
    host.bubbles.clear()
    t_call = time.perf_counter()
    host._set_layer_mode(True)
    mount_secs = time.perf_counter() - t_call
    mid_layers = pet_layers()
    busy_mid = bool(getattr(host, "_fidus_busy", False))
    fact("A1 切换即时性", f"`_set_layer_mode(True)` 返回用 {mount_secs * 1000:.0f} ms，"
                          f"返回时 fidus 在跑={busy_mid}、合成器已有 {len(mid_layers)} 只浮层"
                          f"（挂载不等测量）")
    stuck = run_until_settled(app, host)
    secs = time.perf_counter() - t_call
    if stuck:
        print(f"VERDICT-A1 : ✗ {LOCATE_TIMEOUT:.0f} s 仍未收口 ⇒ 接线卡死，本臂不判精度")
        teardown(host, app)
        return 2
    applied = tuple(getattr(host, "_fidus_surface", geom_pre))
    proxy_a1 = rect_of(host.sprite_label._proxy_rect)
    H4.pump(app, 500)
    t_final = H12.box_truth(box, off, "h15_a1")
    print(f"\n--- A1 收尾：全程 {secs:.1f} s　摆位序列 {host.places}　气泡 {host.bubbles} ---")
    if t_final is None or t_final[2] < H12.PRESENT_PEAK:
        print("VERDICT-A1 : ✗ 事后独立尺子看不见 ⇒ 量具断，不判精度")
        teardown(host, app)
        return 1
    want_f = (applied[0] + fw / 2.0, applied[1] + fh / 2.0)
    errf = (t_final[0] - want_f[0], t_final[1] - want_f[1])
    jump = (applied[0] - geom_pre[0], applied[1] - geom_pre[1])
    proxy_same = proxy_a1 == applied
    immediate = mount_secs * 1000 < MOUNT_MS_MAX and busy_mid
    a1_ok = immediate and max(abs(errf[0]), abs(errf[1])) <= TOL_PX and proxy_same
    print(f"VERDICT-A1 : {'PASS' if a1_ok else 'FAIL'} 校正后请求 {applied[:2]} ⇒ 中心 "
          f"({want_f[0]:.2f},{want_f[1]:.2f}) vs 真值 ({t_final[0]:.2f},{t_final[1]:.2f})"
          f" ⇒ 误差 ({errf[0]:+.2f},{errf[1]:+.2f}) px ≤ {TOL_PX}"
          f"｜全程 {secs:.1f} s（含每会话一次的校准）")
    print(f"VERDICT-A1-JUMP : 跳位 ({jump[0]:+.0f},{jump[1]:+.0f}) px —— 本机 niri 照请求摆位"
          " ⇒ 期望 ≈0；几十像素即接线吃了错坐标")
    print(f"VERDICT-A1-PROXY : `_proxy_rect`={proxy_a1} vs 实际摆下 {applied} ⇒ "
          f"{'同一个数' if proxy_same else '分叉'}")
    print(f"VERDICT-A1-MOUNT : 切换 {mount_secs * 1000:.0f} ms < {MOUNT_MS_MAX} ms 且测量在跑"
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
    host.places.clear()
    host.bubbles.clear()
    host._set_layer_mode(True)
    stuck = run_until_settled(app, host)
    proxy_a2 = rect_of(host.sprite_label._proxy_rect)
    fell_back = any("没能量准" in b for b in host.bubbles)
    a2_ok = (not stuck) and host.places == [] and fell_back and proxy_a2 == tuple(geom_a2)
    print(f"VERDICT-A2 : {'PASS' if a2_ok else 'FAIL'} 摆位 {len(host.places)} 次、"
          f"气泡 {host.bubbles}、`_proxy_rect`={proxy_a2} vs 挂载 {geom_a2}")
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
    t_ref = H12.box_truth(box, off, "h15_a3_ref")
    if t_ref is None or t_ref[2] < H12.PRESENT_PEAK:
        print("VERDICT-A3 : ✗ 该位独立尺子看不见 ⇒ 量具断，本臂不判")
        teardown(host, app)
        return 1
    margin = geo.width() - (geom_ref[0] + fw)
    will_hit = margin < FP.MOVE_PX
    fact("A3 阶段一", f"关着挂载 请求 {geom_ref} ⇒ 真值中心 ({t_ref[0]:.2f},{t_ref[1]:.2f}) "
                      f"峰={t_ref[2]:.4f}")
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
    t_end = H12.box_truth(box, off, "h15_a3_end")
    if stuck or t_end is None or t_end[2] < H12.PRESENT_PEAK:
        print(f"VERDICT-A3 : ✗ {'卡死' if stuck else '事后看不见'} ⇒ 量具断")
        teardown(host, app)
        return 1
    expect = (t_ref[0] + shift[0], t_ref[1] + shift[1])   # 合成器照请求摆 ⇒ 该停在这
    drift = (t_end[0] - expect[0], t_end[1] - expect[1])
    worst = max(abs(drift[0]), abs(drift[1]))
    fell_back_a3 = any("没能量准" in b for b in host.bubbles)
    a3_stranded = worst > TOL_PX
    stranded_txt = f"桌宠停在离挂载位 {worst:.0f} px 处" if a3_stranded else "回到挂载位"
    print(f"VERDICT-A3 : 出口={'退回' if fell_back_a3 else '有值'}　摆位序列 {host.places}")
    print(f"VERDICT-A3-STRAND : 收尾真值中心 ({t_end[0]:.2f},{t_end[1]:.2f}) vs "
          f"应停位 ({expect[0]:.2f},{expect[1]:.2f}) ⇒ 净漂移 "
          f"({drift[0]:+.2f},{drift[1]:+.2f}) px ⇒ {stranded_txt}")
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
        t_a4 = H12.box_truth(r_box, r_off, "h15_a4_end")
        mount_a4 = tuple(getattr(host, "_fidus_mount", ()))
        if stuck or t_a4 is None or t_a4[2] < H12.PRESENT_PEAK or not mount_a4:
            print(f"VERDICT-A4 : ✗ {'卡死' if stuck else '独立尺子看不见换帧后的内容'} ⇒ 量具断")
        else:
            fell_a4 = any("没能量准" in b for b in host.bubbles)
            want_a4 = (mount_a4[0] + mount_a4[2] / 2.0, mount_a4[1] + mount_a4[3] / 2.0)
            d = (t_a4[0] - want_a4[0], t_a4[1] - want_a4[1])
            restored = max(abs(d[0]), abs(d[1])) <= TOL_PX
            proxy_a4 = rect_of(host.sprite_label._proxy_rect)
            last = host.places[-1][:2] if host.places else None
            fact("A4 换帧", f"轮廓不变、纹理在 bbox 内平移 {ROLL_PX} px ⇒ 期望闭环判失败"
                            f"　摆位序列 {host.places}")
            print(f"VERDICT-A4 : 出口={'退回' if fell_a4 else '有值'}｜收尾真值中心 "
                  f"({t_a4[0]:.2f},{t_a4[1]:.2f}) vs 挂载中心 ({want_a4[0]:.2f},{want_a4[1]:.2f})"
                  f" ⇒ 净漂移 ({d[0]:+.2f},{d[1]:+.2f}) px")
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
    a4_txt = "不判（前提未成立）" if a4_ok is None else ("PASS" if a4_ok else "FAIL")
    print(f"VERDICT-WIRING : A0={'PASS' if a0_ok else 'FAIL'} A1={'PASS' if a1_ok else 'FAIL'} "
          f"A2={'PASS' if a2_ok else 'FAIL'} A4={a4_txt}｜A3 净漂移 {worst:.2f} px、出口="
          f"{'退回' if fell_back_a3 else '有值'}（{a3_note}）—— A3 只出事实，不计入红绿")
    reds = [not a0_ok, not a1_ok, not a2_ok, a4_ok is False]
    return 2 if any(reds) else 0


if __name__ == "__main__":
    sys.exit(main())
