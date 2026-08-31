"""Wayland 最小绑定：libwayland-client 的 wl_compositor / wl_region 接口封装。

为什么需要这个模块
-------------------
``libwayland-client.so`` **不导出** ``wl_compositor_create_region``、
``wl_region_add``、``wl_surface_set_input_region``、``wl_surface_commit`` 等
接口方法。这些方法是 ``wl_compositor`` / ``wl_region`` / ``wl_surface``
接口的生成 vfunc，必须通过 ``wl_proxy_marshal`` / ``wl_proxy_marshal_constructor``
按 **opcode** 分派（与 wayland-scanner / pywayland 内部做法一致）。

接口方法 opcode（wayland 协议稳定，不会变）::

    wl_compositor::create_region(proxy) -> new wl_region   opcode 1
    wl_region::add(region, x, y, w, h)                    opcode 0
    wl_region::destroy(region)                  destructor opcode 1
    wl_surface::set_input_region(surface, region)          opcode 6
    wl_surface::commit(surface)                            opcode 2

可选：通过 wl_registry 绑定 wl_compositor 全局（需要先 bind registry 再
roundtrip）。本模块暴露 ``bind_compositor(display)`` 供桌面层按需使用；
常见的更简单路径是：Qt Wayland 已经持有 wl_compositor，桌面层直接通过
私有 API 取出并传给我们的 resolver 即可。

依赖：仅 ctypes + libwayland-client。不依赖 pywayland（若装了也可作为
备用，见 ``load_libwayland`` 的容错逻辑）。

实机调试提示
------------
- 若 ``load_libwayland()`` 返回 None，说明系统缺 ``libwayland-client.so.0``
  （Debian/Ubuntu: ``apt install libwayland-client0``；NixOS/Fedora 类似）。
- ``create_region`` / ``add`` / ``set_input_region`` / ``commit`` / ``destroy``
  均为 ** marshaled 调用**：它们在客户端排队，真正的协议请求在
  ``wl_display_flush`` / 下一次事件分发时发出。因此：
    * 调用顺序必须是 create_region -> add* -> set_input_region -> commit -> destroy
    * destroy 必须在 commit **之后**（协议要求，区域在 commit 后才生效）。
- 坐标系：surface-local（窗口左上角为原点），与 X11 ShapeInput 一致。
"""
from __future__ import annotations

import ctypes
import ctypes.util
from ctypes import c_void_p, c_int, c_uint32


class WaylandError(RuntimeError):
    """Raised when a libwayland symbol or proxy call is unavailable."""


def load_libwayland():
    """dlopen libwayland-client. Returns ctypes.CDLL or None."""
    try:
        name = ctypes.util.find_library("wayland-client") or "libwayland-client.so.0"
        return ctypes.CDLL(name)
    except Exception:
        return None


# ----------------------------------------------------------------------
# 懒加载：首次访问时解析符号 + 设置调用约定。模块导入不抛异常。
# ----------------------------------------------------------------------
_lib = None
_bound = False


def _ensure_bound():
    global _lib, _bound
    if _bound:
        return _lib
    _bound = True
    _lib = load_libwayland()
    if _lib is None:
        return None

    wl = _lib

    # wl_proxy* wl_proxy_marshal_constructor(proxy, opcode, interface, versioned...)
    try:
        wl.wl_proxy_marshal_constructor_versioned.restype = c_void_p
        wl.wl_proxy_marshal_constructor_versioned.argtypes = [
            c_void_p,    # proxy
            c_uint32,    # opcode
            c_void_p,    # interface (wl_interface*)
            c_uint32,    # version
        ]
    except Exception:
        pass

    try:
        # variadic fallback: wl_proxy_marshal_constructor(proxy, opcode, interface)
        wl.wl_proxy_marshal_constructor.restype = c_void_p
    except Exception:
        pass

    try:
        wl.wl_proxy_destroy.argtypes = [c_void_p]
        wl.wl_proxy_destroy.restype = None
    except Exception:
        pass

    try:
        wl.wl_display_flush.argtypes = [c_void_p]
        wl.wl_display_flush.restype = c_int
    except Exception:
        pass

    try:
        wl.wl_display_roundtrip.argtypes = [c_void_p]
        wl.wl_display_roundtrip.restype = c_int
    except Exception:
        pass
    return _lib


def compositor_interface_ptr():
    """返回 wl_compositor_interface 的地址（c_void_p）。"""
    _ensure_bound()
    if _lib is None:
        raise WaylandError("libwayland-client not available")
    return c_void_p.in_dll(_lib, "wl_compositor_interface")


def region_interface_ptr():
    """返回 wl_region_interface 的地址（c_void_p）。"""
    _ensure_bound()
    if _lib is None:
        raise WaylandError("libwayland-client not available")
    return c_void_p.in_dll(_lib, "wl_region_interface")


# ----------------------------------------------------------------------
# 高层 API
# ----------------------------------------------------------------------

def create_region(compositor_ptr: int, version: int = 1) -> int:
    """wl_compositor.create_region(opcode=1) -> 新 wl_region 的 proxy 指针。

    ``compositor_ptr`` 是真实 wl_compositor*（由 resolver 提供）。
    返回值是 wl_region*，为 0 表示失败。

    实现：``wl_proxy_marshal_constructor(proxy, opcode, interface)``。
    这里的 ``interface`` 是 **返回类型** 的描述符，即 ``&wl_region_interface``
    （create_region 的请求签名里携带 "new_id wl_region"）。wayland-scanner
    用它确定返回 proxy 的接口，后续 wl_region_add 等调用才能正确分派。
    """
    _ensure_bound()
    if _lib is None:
        raise WaylandError("libwayland-client not available")
    if not compositor_ptr:
        raise WaylandError("create_region: null compositor")

    wl = _lib
    # 优先 versioned 变体（带 version 参数，现代 libwayland 必备）
    if hasattr(wl, "wl_proxy_marshal_constructor_versioned"):
        region = wl.wl_proxy_marshal_constructor_versioned(
            c_void_p(compositor_ptr),   # proxy = wl_compositor
            1,                           # opcode: compositor.create_region
            region_interface_ptr(),      # 返回类型描述符 = &wl_region_interface
            c_uint32(version),
        )
    else:
        # 旧版 fallback：wl_proxy_marshal_constructor(proxy, opcode, interface)
        wl.wl_proxy_marshal_constructor.restype = c_void_p
        wl.wl_proxy_marshal_constructor.argtypes = [
            c_void_p, c_uint32, c_void_p
        ]
        region = wl.wl_proxy_marshal_constructor(
            c_void_p(compositor_ptr),
            1,
            region_interface_ptr(),
        )
    if not region:
        raise WaylandError("wl_compositor_create_region returned null")
    return region


def region_add(region_ptr: int, x: int, y: int, w: int, h: int) -> None:
    """wl_region.add(opcode=0)：向 region 添加一个矩形。"""
    _ensure_bound()
    if _lib is None:
        raise WaylandError("libwayland-client not available")
    if not region_ptr:
        raise WaylandError("region_add: null region")
    wl = _lib
    # wl_region_add(region, x, y, w, h)  opcode 0
    wl.wl_proxy_marshal(
        c_void_p(region_ptr), 0,
        c_int(x), c_int(y), c_int(w), c_int(h),
    )


def surface_set_input_region(surface_ptr: int, region_ptr: int) -> None:
    """wl_surface.set_input_region(opcode=6)：NULL(0) 表示全 surface 可输入。"""
    _ensure_bound()
    if _lib is None:
        raise WaylandError("libwayland-client not available")
    if not surface_ptr:
        raise WaylandError("surface_set_input_region: null surface")
    _lib.wl_proxy_marshal(
        c_void_p(surface_ptr), 6,
        c_void_p(region_ptr or 0),
    )


def surface_commit(surface_ptr: int) -> None:
    """wl_surface.commit(opcode=2)：双缓冲生效，必须调用。"""
    _ensure_bound()
    if _lib is None:
        raise WaylandError("libwayland-client not available")
    if not surface_ptr:
        raise WaylandError("surface_commit: null surface")
    _lib.wl_proxy_marshal(c_void_p(surface_ptr), 2)


def region_destroy(region_ptr: int) -> None:
    """wl_region.destroy(opcode=1, destructor)：释放 region（需在 commit 后）。"""
    _ensure_bound()
    if _lib is None:
        raise WaylandError("libwayland-client not available")
    if not region_ptr:
        return
    _lib.wl_proxy_destroy(c_void_p(region_ptr))


def destroy_proxy(proxy_ptr: int) -> None:
    """通用 wl_proxy_destroy（用于 wl_surface / wl_compositor 等）。"""
    _ensure_bound()
    if _lib is None or not proxy_ptr:
        return
    _lib.wl_proxy_destroy(c_void_p(proxy_ptr))


# ----------------------------------------------------------------------
# 一键操作：apply_input_region
# ----------------------------------------------------------------------
def apply_input_region(
    surface_ptr: int,
    compositor_ptr: int,
    rects,
    *,
    mode: str = "transparent",
) -> None:
    """按 mode 设置 wl_surface 的 input region。

    Parameters
    ----------
    surface_ptr, compositor_ptr
        真实 wl_surface* / wl_compositor*（由 resolver 提供）。
    rects
        (x, y, w, h) 列表，surface-local 坐标。
    mode
        "transparent" —— 空 rects => **empty region**（全穿透）。
                          非空 rects => region = 这些"保留输入"矩形。
        "opaque"     —— 无论 rects 是否为空，都重置为 NULL（全 surface 可点击），
                          用于 disable / 恢复。

    语义约定（修正旧版的 NULL/empty 双关）：
        empty region  (= 0 个矩形) => 无输入区 => 全穿透
        NULL region   (= 传 0)    => 全 surface 可输入 => 不穿透
    两者在协议上层语义相反，故必须靠 mode 显式区分。
    """
    if not surface_ptr:
        raise WaylandError("apply_input_region: null surface")

    region = None
    try:
        # 1) 创建空 region（任何 mode 都先建一个，便于统一 commit/destroy）
        region = create_region(compositor_ptr)

        if mode == "opaque":
            # 恢复全输入：set_input_region(surface, NULL)
            surface_set_input_region(surface_ptr, 0)
        else:
            # "transparent"：把 rects 加入 region（空列表 => empty => 全穿透）
            for x, y, w, h in rects or ():
                region_add(region, int(x), int(y), int(w), int(h))
            surface_set_input_region(surface_ptr, region)
        # 2) commit：双缓冲生效（必须在 destroy region 之前）
        surface_commit(surface_ptr)
    finally:
        # 3) region 已提交，可以销毁（协议要求 destructor 在 commit 后）
        if region:
            region_destroy(region)
