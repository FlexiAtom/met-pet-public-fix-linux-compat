#!/usr/bin/env python3
"""L1 探针 · 人工 2026-10-15T03:07 报的两个 Bug 的尺寸读数。

被测对象是**产品真身**的方法，不是复刻：
  PetRenderHostMixin._init_renderer / _init_png_renderer / _set_layer_mode
  / _layer_surface_region / _do_layer_frame / _toggle_render_mode / _init_live2d
  / _fit_window_to_model -- meapet/desktop/render_host.py
入口也与用户点的那个一致：`window_chrome.py:480` 的菜单项 `triggered` 直连
`_toggle_render_mode`，所以这里直接调它，不另造切换逻辑。

三条支路各判一件事（`--arm` 选一，每次一个新进程，避免上一臂的残留把读数揉软）：
  A  png-penetrate-message : PNG 模式下切穿透，那一句提示到底是什么、`enable()` 收到什么矩形
  B  live2d-interactive    : 交互态切 Live2D，窗口尺寸被谁、按哪份画布尺寸改成什么
  C  live2d-while-penetrated: 穿透态切 Live2D，surface 的尺寸请求有没有人重新发过

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
                    choices=["A", "B", "C"],
                    help="A=PNG切穿透的文案与矩形 B=交互态切Live2D的尺寸链 "
                         "C=穿透态切Live2D之后有没有人重新请求surface尺寸")
    ap.add_argument("--settle", type=float, default=12.0,
                    help="等 Live2D 首帧的最长秒数（本机冷启动实测可达 9 s 量级）")
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
    config.setdefault("fidus", {})["enabled"] = False   # 挂载走直发那一支，不起 fidus 抓屏

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
            resize_log.append({"to": (self.width(), self.height()),
                               "who": caller_trail()})

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
