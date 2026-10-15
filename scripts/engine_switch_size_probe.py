#!/usr/bin/env python3
"""L1 探针 · 人工 2026-10-15T03:07 报的两个 Bug 的尺寸读数。

被测对象是**产品真身**的方法，不是复刻：
  PetRenderHostMixin._init_renderer / _init_png_renderer / _set_layer_mode
  / _layer_surface_region / _do_layer_frame / _toggle_render_mode / _init_live2d
  / _fit_window_to_model -- meapet/desktop/render_host.py
入口也与用户点的那个一致：`window_chrome.py:480` 的菜单项 `triggered` 直连
`_toggle_render_mode`，所以这里直接调它，不另造切换逻辑。

各支路各判一件事（`--arm` 选一，每次一个新进程，避免上一臂的残留把读数揉软）：
  A  png-penetrate-message : PNG 模式下切穿透，那一句提示到底是什么、`enable()` 收到什么矩形
  B  live2d-interactive    : 交互态切 Live2D，窗口尺寸被谁、按哪份画布尺寸改成什么
  C  live2d-while-penetrated: 穿透态切 Live2D，surface 的尺寸请求有没有人重新发过
  D  png-premount-chain    : PNG 模式下「量完再挂」那一发到底走到哪一格——交得出帧吗、
                             帧是什么、`request_locate` 收到的是哪一份、量出来之前 `enable` 是不是 0
                             （**必须配 `--fidus`**：不带时本臂拒绝跑，见下面那段坑）
  E  engine-switch-size-request : 交互态点「切 Live2D」那一下，**每一次顶层 resize 是按哪份数发的**
                             （占位 (525,735) 还是真画布），以及发出去之后有没有人回读实得。
                             本臂只判"发了几次、按哪份数发、读没读回"——offscreen QPA 下 `resize`
                             立即生效，**判不了合成器给不给**（那是 C 臂与他真机那两格的分工）。

失败模式（agents-rules §1，三段）：
  收益  = A 判那句文案（Bug 1）；B/C 判「切换之后谁重新请求尺寸」这一条结构事实，
          这三格在真机上都要人工在场点菜单才走得到，探针把它们变成可重复读数。
  崩溃  = `Host.show()` / `LayerDebugPanel.show()` / QOpenGLWidget 都会映射真窗口，
          在 xcb 或 wayland QPA 下这支"零改动"探针会自己改用户的桌面。
  兜底  = QPA 强制 offscreen，且在**任何 widget 存在之前**读 `app.platformName()`；
          门控被测代码的 Wayland 谓词是在那之后才假造的，所以假值永远宽不过本进程映射的东西。
          桥接层用 FakeBackend（同 `_ctx` 单槽、同 `enable` 先回收旧 ctx 的幂等守卫、
          同 `update_pixels` 在 `_ctx is None` 时静默 no-op），**本进程一次都不加载
          liblayer_shell_shim.so** ⇒ 屏上不会出现真 surface。
  写侧  = `_toggle_render_mode` 会 `_save_config()`（改的是他的 config.json），探针把
          `_save_config` 换成记录器、config 用 deepcopy，**一个字都不落盘**。

这探针**判不了**的（agents-rules §10：L1 不是 L3）：
  * 真合成器接不接受 `layer_set_size` 的新请求、重发的 configure 是多少（offscreen 没有合成器）；
  * C 臂里 Live2D 的 `frameSwapped` 在窗口 `hide()` 之后到底还来不来（真机上才有答案）；
  * 人眼看到的"切过去对不对"。
  * **D 臂量不到"fidus 对着 PNG 立绘匹不匹得准"**：本臂把 `have_engine` 与 `request_locate`
    一起 mock 掉，证明的只是"帧交得出去、链路走得到"那一半。
    （**更正 2026-10-15**：这句原先写的是"这台机器没有 fidus wheel（`find_spec('fidus')` → None）"，
    那是**假话**——用系统裸 `python` 查才 None，`.venv/bin/python` 下 `have_engine()` 是 **True**，
    连 `fidus.Fidus.build_wayland()` 在无头里都**建得起来**。真正的拦路不是"没件"，是
    **件一干活就摸合成器**：`register_target`/`calibrate_once` 实测要 2.0–2.2 s 且**屏幕闪现**，
    在用户的桌面上造可见效果须他明确点头（禁忌 7），所以本探针仍然 mock，仍然量不到匹配精度。）
  * **E 臂判得了"按哪份数发"，判不了"合成器给不给"**：offscreen 下 `resize` 立即生效、
    `width()/height()` 恒等于请求值 ⇒ `want == got` 在本臂是**必然**，不是结论。
    「平铺下请求被吃」这一格只有 L3（他真机）能证，人工 2026-10-15 已现测过一次
    （请求 461×614、实得 659×736，记在 `render_host.py:1654-1657`）。
  * **E 臂还引不出首帧**（2026-10-16 现量）：offscreen 没有 GL 交换 ⇒ `frameSwapped` 永不到来
    ⇒ C1 那发读回挂在的 `_on_live2d_first_frame` 在本臂**一次都不触发**（现场读数：
    等待 4.81 s 后 `_l2d_pending` 已由 5 s 超时回退清掉、`_l2d_switch_size_check=False`、
    全程没有一行 `[live2d] 尺寸请求`）。所以本臂的判据收窄成两格：**C2**（切换瞬间的顶层
    `resize` 序列该为空，而不是一个按占位算的数）与**标志生命周期**（置起 → 回退清掉）。
    "首帧那一发发的是真画布、且读了回"由 L2
    （`tests/test_layer_panel_startup.py::TestEngineSwitchRequestsAndReadsBackSize`，
    它直接调 `_on_live2d_first_frame`）判，"合成器到底给不给"仍只有 L3。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

# 解释器可能是系统 Python（裸 `python`），它读不到仓库包，所以仓根必须先进 sys.path；
# 放在脚本最前面，晚于 QApplication 的那些 import 才都有落点。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def drain(app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


class FakeBackend:
    """照 WaylandLayerBackend 的 ctx 生命周期复刻（wayland_layer.py:172-226）。"""

    def __init__(self, accept: bool = True):
        self.enable_calls: list[tuple[int, int, int, int]] = []
        self.size_calls: list[tuple[int, int]] = []
        self.position_calls: list[tuple[int, int]] = []
        self.destroyed: list[int] = []
        self.frames: list[tuple[int, int]] = []
        self.dropped: int = 0
        self._ctx: int | None = None
        self._seq = 0
        self._configured: tuple[int, int] | None = None
        self._accept = accept     # False = 合成器把请求夹小（本探针不用这档）

    def enable(self, qwindow, width, height, pos_x, pos_y):
        self.enable_calls.append((int(width), int(height), int(pos_x), int(pos_y)))
        if self._ctx:              # 真门面的幂等守卫（wayland_layer.py:180-181）
            self.destroy_context()
        self._seq += 1
        self._ctx = self._seq
        self._configured = (int(width), int(height))   # 这臂判的是"有没有人重新请求"，不是夹小
        return self._ctx

    def set_size(self, w, h):
        self.size_calls.append((int(w), int(h)))
        if self._ctx:
            self._configured = (int(w), int(h))

    def set_position(self, x, y):
        self.position_calls.append((int(x), int(y)))

    def logical_size(self):
        return self._configured if self._ctx else None

    def destroy_context(self):
        if not self._ctx:
            return
        self.destroyed.append(self._ctx)
        self._ctx = None
        self._configured = None

    def update_pixels(self, image):
        if not self._ctx:           # wayland_layer.py 的规则：没 ctx 就静默丢
            self.dropped += 1
            return
        self.frames.append((image.width(), image.height()))


def caller_trail(depth: int = 8) -> list[str]:
    """最近的 render_host/live2d_widget 帧，形如 `render_host.py:2310 _apply_...`。"""
    out: list[str] = []
    frame = sys._getframe(1)
    seen = 0
    while frame is not None and seen < depth:
        name = Path(frame.f_code.co_filename).name
        if name in ("render_host.py", "live2d_widget.py", "window_chrome.py", "app.py"):
            out.append(f"{name}:{frame.f_lineno} {frame.f_code.co_name}")
        frame = frame.f_back
        seen += 1
    return out[:3]


def snap(host) -> dict:
    widget = getattr(host, "sprite_label", None)
    canvas = None if widget is None else (widget.width(), widget.height(),
                                          widget.x(), widget.y())
    return {
        "window": (host.width(), host.height()),
        "canvas": canvas,
        "use_live2d": bool(getattr(host, "_use_live2d", False)),
        "ready": bool(getattr(host, "_renderer_ready", False)),
        "pending": bool(getattr(host, "_l2d_pending", False)),
        "visible": host.isVisible(),
        "hidden": host.isHidden(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True,
                    choices=["A", "B", "C", "D", "E"],
                    help="A=PNG切穿透的文案与矩形 B=交互态切Live2D的尺寸链 "
                         "C=穿透态切Live2D之后有没有人重新请求surface尺寸 "
                         "D=PNG 走「量完再挂」那一发的落点（须配 --fidus） "
                         "E=交互态换引擎：每次顶层 resize 按哪份数发、有没有人读回实得")
    ap.add_argument("--settle", type=float, default=12.0,
                    help="等 Live2D 首帧的最长秒数（本机冷启动实测可达 9 s 量级）")
    ap.add_argument("--fidus", action="store_true",
                    help="按 config.json 里 fidus.enabled 的原值跑（默认强制关掉，让挂载走直发那一支）")
    ap.add_argument("--size-factor", dest="size_factor", type=float, default=None,
                    help="覆盖 display.size_factor。D 臂的放大对照档就靠它：0.5（他的原值）该被"
                         "预检闸门当场拒绝，0.75 该放行到 `request_locate`")
    args = ap.parse_args()

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["MEA_PET_FORCE_PNG"] = "1"    # 从产品自己的开关进 PNG，不另造状态

    from PyQt5.QtGui import QGuiApplication
    from PyQt5.QtWidgets import QApplication, QWidget

    app = QApplication(sys.argv)
    real_qpa = app.platformName()
    if real_qpa != "offscreen":
        print(f"ABORT: qpa={real_qpa} 会映射真窗口，本探针拒绝动用户的桌面", file=sys.stderr)
        return 2

    # render_host.py:708 用 platformName()=="wayland" 门控整条双模链；offscreen 下它是 False，
    # 不假造就测到"自己不在那条路上"。QPA 仍是 offscreen，改的只是 Qt 报出来的字符串。
    QGuiApplication.platformName = staticmethod(lambda: "wayland")

    import meapet.desktop.wayland_layer as wl

    backend = FakeBackend()
    wl.get_backend = lambda: backend
    wl.is_available = lambda: True

    from meapet.config.store import normalize_config
    from meapet.desktop.render_host import PetRenderHostMixin

    config = normalize_config(json.loads((ROOT / "config.json").read_text(encoding="utf-8")))
    if not args.fidus:
        config.setdefault("fidus", {})["enabled"] = False   # 挂载走直发那一支，不起 fidus 抓屏
    if args.size_factor is not None:
        # 只换画布那一个乘子：`pixmap.width * scale * size_factor`（`render_host.py:2578-2579`），
        # 其余照他 config.json 原值，这样"放大一档"与"换了另一台机"分得开。
        config.setdefault("display", {})["size_factor"] = args.size_factor

    resize_log: list[dict] = []

    class Host(QWidget, PetRenderHostMixin):
        """只把两件与尺寸链无关的副作用换成记录器：写盘、气泡。"""

        def __init__(self, cfg):
            super().__init__()
            self.config = cfg
            self._standby = False
            self.bubbles: list[str] = []
            self.saved = 0

        def _save_config(self):
            self.saved += 1          # 不写盘：那是他的 config.json

        def _show_bubble(self, text, *a, **k):
            self.bubbles.append(str(text))

        # Live2D 控件的四个互动信号在 `_init_live2d` 里连到宿主方法上；这里要的只是
        # "控件能连上、几何那一段跑到"，摸头/聊天那几条链不在本案的判据里。
        def _on_head_patted(self, *a, **k):
            pass

        def _on_lower_left_patted(self, *a, **k):
            pass

        def _on_lower_right_patted(self, *a, **k):
            pass

        def _start_chat(self, *a, **k):
            pass

        def resize(self, *a):
            super().resize(*a)
            m = getattr(self, "_l2d_model", None)
            resize_log.append({"to": (self.width(), self.height()),
                               "who": caller_trail(),
                               # E 臂要判"这一发按的是哪份数"：模型对象在但 `model` 还没加载时
                               # `get_suggested_size()` 回的是 `PLACEHOLDER_CANVAS`，那时
                               # `canvas_read` 就是那对占位数。别的臂只读 to/who，不受影响。
                               "l2d_loaded": getattr(m, "model", None) is not None,
                               "canvas_read": None if m is None
                               else self._read_canvas_size_from_model(m)})

    host = Host(copy.deepcopy(config))
    host._init_renderer()
    print(f"\n=== ARM {args.arm} · qpa={real_qpa}（假装的 wayland 谓词只改字符串） ===")
    print(f"[起点·PNG] {snap(host)}")
    print(f"  读到的画布基准 _live2d_base_size()={host._live2d_base_size()}")

    if args.arm == "A":
        before = len(backend.enable_calls)
        host._set_layer_mode(True)
        drain(app, 1.0)
        print(f"[切穿透后] snap={snap(host)}")
        print(f"  enable_calls={backend.enable_calls[before:]}")
        print(f"  configure(logical_size)={backend.logical_size()}")
        print(f"  推帧 {len(backend.frames)} 张，尺寸集合={sorted(set(backend.frames))}")
        print(f"  dropped={backend.dropped}")
        host._set_layer_mode(False)
        print("[回收] 交互态：destroyed=" + str(backend.destroyed))
        return 0

    if args.arm == "D":
        if not config.get("fidus", {}).get("enabled"):
            print("ABORT: D 臂判的就是 fidus 开着那条支路。不带 --fidus 时本探针把 "
                  "fidus.enabled 强制关成 False（那正是上一案「PNG 切不上穿透」被遮住的坑），"
                  "关着跑这一臂只会量到那条已经不走的路", file=sys.stderr)
            return 2
        import numpy as np
        from unittest import mock

        import meapet.desktop.fidus_position as FP

        widget = host.sprite_label
        print(f"[D·取帧面] sprite_label={type(widget).__name__} "
              f"render_offscreen={hasattr(widget, 'render_offscreen')} "
              f"frame_image={hasattr(widget, 'frame_image')} "
              f"use_live2d={bool(getattr(host, '_use_live2d', False))}")
        img = host._current_frame_image()
        print(f"[D·QImage] {'None' if img is None else f'{img.width()}x{img.height()} fmt={img.format()}'}")
        frame = host._fidus_current_frame()
        if frame is None:
            print(f"[D·numpy 帧] None ⇒ 预挂载在 `render_host.py` 的取帧那一格就断了："
                  f"{host._frame_absence_reason()}")
        else:
            mask = frame[..., 3] > FP.ALPHA_MIN
            rows, cols = np.nonzero(mask.any(axis=1))[0], np.nonzero(mask.any(axis=0))[0]
            bbox = (int(rows.min()), int(rows.max()) + 1,
                    int(cols.min()), int(cols.max()) + 1) if rows.size else None
            print(f"[D·numpy 帧] shape={frame.shape} dtype={frame.dtype} "
                  f"不透明像素={int(mask.sum())} 轮廓(上,下,左,右)={bbox} "
                  f"← 帧 dims 必须逐位等于画布 dims：锚点定死在**帧中心**")
            print(f"           _layer_geometry()={host._layer_geometry()}")

        if frame is not None:
            t_gate = time.perf_counter()
            edges = sorted({c.edge for c in FP.iter_candidates(frame)}, reverse=True)
            gate_ms = (time.perf_counter() - t_gate) * 1000
            print(f"[D·预检闸门] 画布={frame.shape[1]}x{frame.shape[0]} 可测档={edges} "
                  f"下限={FP.PREMOUNT_MIN_EDGE}² ⇒ {'放行' if any(e >= FP.PREMOUNT_MIN_EDGE for e in edges) else '当场拒绝'}"
                  f" 耗时={gate_ms:.1f} ms")
            print("  （产品在同一发里自己再跑一次同一条 `iter_candidates`；这里量的是那一发的毫秒数，"
                  "它换掉的是「放大窗口 + 最多四发 × 0.9–13.5 s + 弹模态请人按 Mod+V + 20 s 预算」）")

        box: dict = {}

        def fake_request(sent_frame, prior, *, move, on_done, log, **kw):
            box["shape"] = None if sent_frame is None else tuple(sent_frame.shape)
            box["prior"] = prior
            box["kw"] = {k: v for k, v in kw.items()}
            box["on_done"] = on_done
            box["calls"] = box.get("calls", 0) + 1

        with mock.patch.object(FP, "have_engine", lambda: True), \
                mock.patch.object(FP, "request_locate", fake_request):
            host._set_layer_mode(True)
            drain(app, 0.3)
            print(f"[D·测量发出之后、结果回来之前] request_locate 调用={box.get('calls', 0)} 次 "
                  f"喂进去的帧={box.get('shape')} 先验={box.get('prior')}")
            print(f"  参数={box.get('kw')}")
            print(f"  enable_calls={backend.enable_calls}  ← **必须还是 []**：量完之前挂就是旧那条路")
            print(f"  窗口={host.width()}x{host.height()} "
                  f"放大前={getattr(host, '_probe_saved', None)} "
                  f"四向空档(右,下,左,上)={host._content_probe_room()} "
                  f"_pending_mount={getattr(host, '_pending_mount', None)}")
            print(f"  气泡={host.bubbles}")
            pending = getattr(host, "_pending_mount", None)
            if pending is not None and box.get("on_done") is not None:
                _x, _y, w, h = pending
                fix = FP.Fix(center=(_x + w / 2.0, _y + h / 2.0), conf=0.9,
                             edge=min(FP.SIZES[0], w, h), ceiling=0.9)
                print(f"[D·交回一发读数] center={fix.center} 贴片 {fix.edge}²"
                      f"（按信念矩形中心给 ⇒ 挂载位就该等于那一份，好对照）")
                box["on_done"](fix)
                drain(app, 1.5)
        print(f"[D·结果之后] enable_calls={backend.enable_calls} configure={backend.logical_size()}")
        print(f"  推帧 {len(backend.frames)} 张 尺寸集合={sorted(set(backend.frames))} "
              f"dropped={backend.dropped}")
        print(f"  snap={snap(host)} 气泡={host.bubbles}")
        host._set_layer_mode(False)
        print(f"[D·回收] destroyed={backend.destroyed} 窗口={host.width()}x{host.height()} "
              f"放大令牌 _probe_saved={getattr(host, '_probe_saved', None)}")
        return 0

    if args.arm == "E":
        from meapet.desktop.live2d_widget import PLACEHOLDER_CANVAS
        from meapet.desktop.render_host import LIVE2D_STARTUP_TIMEOUT_MS
        cfg_l2d = copy.deepcopy(config.get("live2d", {})).get("enabled")
        print(f"[E·起点] use_live2d={bool(getattr(host, '_use_live2d', False))} "
              f"窗口={host.width()}x{host.height()} config.live2d.enabled(原值)={cfg_l2d} "
              f"PLACEHOLDER_CANVAS={PLACEHOLDER_CANVAS}")
        resize_log.clear()
        t0 = time.monotonic()
        host._toggle_render_mode()
        # 这一格单列，不塞进 `snap()`：`snap` 是 A/B/C/D 四臂共用的打印器，本案的判据之一正是
        # 「老三臂不带新旗标时读数一字不变」，往里加键就先动了那三条的读数。
        print(f"[E·切换瞬间] snap={snap(host)} "
              f"_l2d_switch_size_check={getattr(host, '_l2d_switch_size_check', '（无此标志）')}")
        base = host._live2d_base_size()
        print(f"  此刻 _live2d_base_size()={base} "
              f"{'== 占位 ⇒ 这一发请求的数是已知假的' if tuple(base) == tuple(PLACEHOLDER_CANVAS) else '!= 占位'}")
        deadline = t0 + args.settle
        while time.monotonic() < deadline:
            app.processEvents()
            if host._renderer_ready and not host._l2d_pending:
                break
            time.sleep(0.01)
        dt = time.monotonic() - t0
        timer = getattr(host, "_live2d_startup_timer", None)
        print(f"[E·等待 {dt:.2f}s] snap={snap(host)} "
              f"首帧预算定时器={None if timer is None else timer.isActive()} "
              f"（True 且 {dt:.1f}s ≥ {LIVE2D_STARTUP_TIMEOUT_MS / 1000:.0f}s ⇒ 已走过超时回退）")
        print("  顶层 resize 序列（`canvas_read` 就是那一发据以请求的画布读数）：")
        for row in resize_log:
            tag = ("按占位 (525,735)" if row.get("canvas_read") == PLACEHOLDER_CANVAS
                   else f"按 {row.get('canvas_read')}" if row.get("canvas_read")
                   else "canvas 读不出")
            print(f"    → {row['to'][0]}x{row['to'][1]}  模型已加载={row.get('l2d_loaded')} "
                  f"{tag}  由 {row['who']}")
        readback = getattr(host, "_l2d_switch_size_check", "（没有这枚标志 ⇒ 换引擎那一发没人读回实得）")
        # 首帧在本臂来不来，由现场读数判，不写死：`_use_live2d` 还被顶着且不再 pending ⇒ 首帧到了；
        # 回到 PNG ⇒ 是超时回退把标志清的，跟"读过一次回"是两回事（旧文案把这两种揉成一句）。
        arrived = bool(getattr(host, "_use_live2d", False)) and not getattr(host, "_l2d_pending", False)
        print(f"[E·回读] _l2d_switch_size_check={readback} 窗口={host.width()}x{host.height()} "
              f"首帧={'到了' if arrived else '没到（offscreen 无 GL 交换 ⇒ `_on_live2d_first_frame` 未触发）'}"
              f" ⇒ {'want/got 那一格本臂读过' if arrived else '本臂只判 C2 与标志生命周期，want/got 归 L2/L3'}")
        print(f"[E·落盘] _save_config 被调用 {host.saved} 次（探针里不写盘）；"
              f"内存里的 config.live2d.enabled={host.config.get('live2d', {}).get('enabled')} "
              f"← 与产品写盘值同源；气泡={host.bubbles}")
        return 0

    # ---- B / C：先记住 PNG 那两把尺寸，再切 Live2D
    png_window = (host.width(), host.height())
    png_canvas = host.sprite_label.geometry().getRect() if host.sprite_label else None
    if args.arm == "C":
        host._set_layer_mode(True)
        drain(app, 0.5)
        print(f"[挂载·PNG] enable_calls={backend.enable_calls} "
              f"configure={backend.logical_size()} 帧数={len(backend.frames)}")

    resize_log.clear()
    t0 = time.monotonic()
    host._toggle_render_mode()
    print(f"[切换瞬间] snap={snap(host)}")
    print(f"  此刻 _live2d_base_size()={host._live2d_base_size()}")

    # 等首帧：`_on_live2d_first_frame` 里才有 `_fit_window_to_model` 那一发
    deadline = t0 + args.settle
    while time.monotonic() < deadline:
        app.processEvents()
        if host._renderer_ready and not host._l2d_pending:
            break
        time.sleep(0.01)
    dt = time.monotonic() - t0
    print(f"[等待 {dt:.2f}s] snap={snap(host)}")
    print(f"  此刻 _live2d_base_size()={host._live2d_base_size()}")
    model = getattr(host, "_l2d_model", None)
    canvas_from_model = None
    if model is not None:
        try:
            canvas_from_model = (model.model is not None) and tuple(model.model.GetCanvasSize())
        except Exception as exc:
            canvas_from_model = f"读不出: {type(exc).__name__}: {exc}"
    print(f"  模型真画布 GetCanvasSize()={canvas_from_model}（未加载时 get_suggested_size 报 "
          f"{model.get_suggested_size() if model else None}）")
    print("  切换后的 resize 序列：")
    for row in resize_log:
        print(f"    → {row['to'][0]}x{row['to'][1]}  由 {row['who']}")
    layout = host._live2d_viewport_layout(host._size_factor)
    print(f"  按当前基准算出的应有几何 window={layout.window_width}x{layout.window_height} "
          f"canvas={layout.widget_width}x{layout.widget_height}")
    print(f"  PNG 时代的窗口={png_window} 画布={png_canvas}")

    if args.arm == "C":
        drain(app, 1.0)
        print(f"[切换后·穿透态] enable_calls 总数={len(backend.enable_calls)} "
              f"set_size 调用={backend.size_calls} "
              f"configure={backend.logical_size()}")
        print(f"  帧尺寸集合={sorted(set(backend.frames))}（裁剪对齐={backend.frames != []}")
        print(f"  对齐那一句的判决缓存 _fit_size={getattr(host, '_fit_size', None)} "
              f"frame_origin={getattr(host, '_layer_frame_origin', None)} "
              f"canvas_size={getattr(host, '_layer_canvas_size', None)}")
    print(f"[收尾] _save_config 被调用 {host.saved} 次（探针里它不写盘）；气泡 {len(host.bubbles)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
