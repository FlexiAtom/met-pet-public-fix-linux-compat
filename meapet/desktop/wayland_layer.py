"""
wayland_layer.py —— Niri (wlroots) 点击穿透的 layer-shell 后端。

原理：通过 liblayer_shell_shim.so 创建【裸】wl_surface（无 xdg_toplevel role），
     挂 layer-shell OVERLAY + 空 input region → pointer/touch 穿透。

调用流程：
  1. layer_shell_init()                    —— 从 Qt 拿 wl_display，绑定全局 state
  2. layer_create_context(NULL, w, h, x, y) —— 创建 layer context（state 自取）
  3. layer_set_click_through(ctx, 1)       —— 穿透；0 = 恢复可点
  4. layer_update_pixels(ctx, rgba, w, h)  —— 每帧推送像素（Phase 2）
  5. layer_destroy_context(ctx)            —— 销毁

依赖：liblayer_shell_shim.so（C 层，已编译到项目根目录）
"""

import ctypes
from ctypes import (POINTER, c_char_p, c_int, c_uint32, c_ubyte, c_void_p)
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage

# shim 位于项目根目录：meapet/desktop/wayland_layer.py -> 上三级
_SHIM_PATH = str(
    Path(__file__).resolve().parent.parent.parent / "liblayer_shell_shim.so"
)

# wl_shm 格式常量（与 C 层 layer_shell_c.c 保持一致）
WL_SHM_FORMAT_ARGB8888 = 0
WL_SHM_FORMAT_XRGB8888 = 1
WL_SHM_FORMAT_ABGR8888 = 0x34324241
WL_SHM_FORMAT_XBGR8888 = 0x34324258
WL_SHM_FORMAT_RGBA8888 = 0x34324952
WL_SHM_FORMAT_RGBX8888 = 0x34325852
WL_SHM_FORMAT_BGRA8888 = 0x41524742
WL_SHM_FORMAT_BGRX8888 = 0x42315852


class WaylandLayerBackend:
    """单例式后端，供 click_through._enable_wayland 调用。"""

    def __init__(self):
        self._shim = None
        self._ctx = None
        self._pixel_format = 0  # 0 = 自动选择

    # ---------- 懒加载 shim ----------
    def _load(self) -> ctypes.CDLL:
        if self._shim is None:
            self._shim = ctypes.CDLL(_SHIM_PATH)

            # init / cleanup
            self._shim.layer_shell_init.restype = c_int
            self._shim.layer_shell_cleanup.restype = None

            # layer context API
            self._shim.layer_create_context.restype = c_void_p
            self._shim.layer_create_context.argtypes = [
                c_void_p,          # state（传 None 则 C 层自取全局 state）
                c_int, c_int,      # width, height
                c_int, c_int,      # pos_x, pos_y
            ]
            self._shim.layer_set_click_through.argtypes = [c_void_p, c_int]
            self._shim.layer_destroy_context.argtypes = [c_void_p]

            # Phase 3: 双模切换（可选符号，缺失时降级而非整体失效）
            self._optional = set()
            for name, argtypes in (
                ("layer_clear", [c_void_p]),
                ("layer_set_position", [c_void_p, c_int, c_int]),
                ("layer_set_size", [c_void_p, c_int, c_int]),
            ):
                fn = getattr(self._shim, name, None)
                if fn is None:
                    print(f"[layer] ⚠ shim 缺少 {name}，该功能降级", flush=True)
                    continue
                fn.restype = None
                fn.argtypes = argtypes
                self._optional.add(name)

            # Phase 2: 像素上传
            self._shim.layer_update_pixels.restype = None
            self._shim.layer_update_pixels.argtypes = [
                c_void_p,
                POINTER(c_ubyte),
                c_int, c_int,
            ]
            self._shim.layer_update_pixels_with_format.restype = None
            self._shim.layer_update_pixels_with_format.argtypes = [
                c_void_p,
                POINTER(c_ubyte),
                c_int, c_int,
                c_uint32,
            ]
        return self._shim

    # ---------- 可用性 ----------
    def is_available(self) -> bool:
        try:
            shim = self._load()
            ret = shim.layer_shell_init()
            if ret != 0:
                print(f"[layer] ✗ layer_shell_init 返回 {ret}", flush=True)
            return ret == 0
        except Exception as exc:
            print(f"[layer] ✗ 探测异常: {type(exc).__name__}: {exc}", flush=True)
            return False

    # ---------- 生命周期 ----------
    def enable(self, qwindow, width: int, height: int, pos_x: int, pos_y: int):
        """创建 layer context，开启穿透。返回 ctx 句柄。"""
        shim = self._load()
        if shim.layer_shell_init() != 0:
            raise RuntimeError("layer-shell init 失败（compositor 不支持？）")

        # state 传 None → C 层自取 g_state
        self._ctx = shim.layer_create_context(None, width, height, pos_x, pos_y)
        if not self._ctx:
            raise RuntimeError("创建 layer context 失败")
        return self._ctx

    def set_click_through(self, enabled: bool):
        if self._ctx:
            self._shim.layer_set_click_through(self._ctx, 1 if enabled else 0)

    def clear(self):
        if self._ctx and "layer_clear" in getattr(self, "_optional", ()):
            self._shim.layer_clear(self._ctx)

    def set_position(self, x: int, y: int):
        if self._ctx and "layer_set_position" in getattr(self, "_optional", ()):
            self._shim.layer_set_position(self._ctx, x, y)

    def set_size(self, w: int, h: int):
        if self._ctx and "layer_set_size" in getattr(self, "_optional", ()):
            self._shim.layer_set_size(self._ctx, w, h)

    def disable(self):
        if self._ctx:
            self._shim.layer_destroy_context(self._ctx)
            self._ctx = None
            try:
                self._shim.layer_shell_cleanup()
            except Exception:
                pass

    # ---------- Phase 2: 像素上传 ----------
    def update_pixels(self, image) -> None:
        """把 QImage 推送到 layer surface（每帧调用）。

        image: PyQt5.QtGui.QImage，任意格式（内部自动转 RGBA8888）
        失败时静默返回，避免中断渲染循环。
        """
        if not self._ctx or not self._shim:
            return
        if not isinstance(image, QImage):
            return

        if image.format() != QImage.Format_RGBA8888:
            image = image.convertToFormat(QImage.Format_RGBA8888)

        w, h = image.width(), image.height()
        if w <= 0 or h <= 0:
            return

        # 关键：constBits() 返回 sip.voidptr，必须拷贝一份到 Python 管理的内存，
        # 否则 QImage 被 GC 后 C 层读到野指针 → 崩溃
        bits = image.constBits()
        bits.setsize(image.byteCount())
        buf = (c_ubyte * image.byteCount()).from_buffer_copy(bits)

        if self._pixel_format:
            self._shim.layer_update_pixels_with_format(
                self._ctx, buf, w, h, self._pixel_format
            )
        else:
            self._shim.layer_update_pixels(self._ctx, buf, w, h)

    def set_pixel_format(self, fmt: int) -> None:
        """强制指定 wl_shm 格式（0 = 自动）。颜色错乱时用于调试。

        例如：set_pixel_format(WL_SHM_FORMAT_ARGB8888)
        """
        self._pixel_format = fmt

    def destroy_context(self):
        """只销毁 layer context，保留 g_state 供下次重建。

        ⚠️ 不能调 layer_shell_cleanup()——它会把 g_state 清成 NULL，
           导致下次 layer_create_context 直接失败。
        """
        if self._ctx is None:
            return
        try:
            self._shim.layer_destroy_context(self._ctx)
        except Exception as exc:
            print(f"[layer] destroy_context 失败: {exc}", flush=True)
        self._ctx = None


# ---------- 单例 ----------
_backend = WaylandLayerBackend()


# ---------- 门面函数（供 click_through.py / render_host.py 调用）----------
def is_available() -> bool:
    return _backend.is_available()


def enable(qwindow, width: int, height: int, pos_x: int, pos_y: int):
    return _backend.enable(qwindow, width, height, pos_x, pos_y)


def set_click_through(enabled: bool) -> None:
    _backend.set_click_through(enabled)


def disable() -> None:
    _backend.disable()


def update_pixels(image) -> None:
    _backend.update_pixels(image)

def clear() -> None:
    _backend.clear()


def set_position(x: int, y: int) -> None:
    _backend.set_position(x, y)


def set_size(w: int, h: int) -> None:
    _backend.set_size(w, h)


def get_backend() -> WaylandLayerBackend:
    """返回后端单例，供 Live2DWidget 注入后每帧推像素。"""
    return _backend

