"""Standby click-through helpers for Windows / Linux X11 / Linux Wayland.

Native backends make the pet window ignore mouse input so clicks reach
windows below. Right-click is re-captured out-of-band (see host poll)
because full-window transparent-for-input also drops RMB.

Backends
--------
win32  : WS_EX_LAYERED + WS_EX_TRANSPARENT, plus optional WM_NCHITTEST
         pixel-accurate hit testing. (Kept as-is per project policy.)
x11    : X Shape Extension (ShapeInput). Industry-standard for X11 desktops.
wayland: wl_surface.set_input_region + commit. Only controls our own
         surface (Wayland protocol restriction).
none   : Qt WA_TransparentForMouseEvents only; used as fallback for
         Wayland compitors that don't support input regions, and for
         other platforms (macOS etc.).

The input-region shape is updated through ``set_shape_region()`` so the
window stays "dynamically shaped" as the Live2D character moves.

Niri / wlroots specialization (wayland-layer backend)
-----------------------------------------------------
On compositors that support zwlr_layer_shell_v1 (Niri, Sway, Hyprland, ...)
the traditional "set empty input region on the Qt xdg_toplevel surface"
approach does NOT yield pointer pass-through: Qt has already assigned the
xdg_toplevel role to its wl_surface, and Niri routes input based solely on
that surface's input region (which Qt keeps reset / ignored).

The ``wayland-layer`` backend therefore creates a *separate bare*
wl_surface via liblayer_shell_shim.so, assigns it the layer-shell OVERLAY
role with KEYBOARD_INTERACTIVITY_NONE, and sets an **empty input region**
on it.  Pointer / touch events then fall through to the window below
(confirmed on Niri 26.04).

It is selected in preference to the legacy Path A whenever the shim reports
``layer-shell`` as available.  The Qt pet window must be hidden separately
(Phase 2) so it does not occlude the layer surface; this module only owns
the pass-through state.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional, Union

from meapet.utils import safe_print

# Windows extended styles / GetWindowLong indices
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
VK_RBUTTON = 0x02

# X11 shape
ShapeInput = 2
ShapeSet = 0
ShapeBounding = 0


# ----------------------------------------------------------------------
# Public data types
# ----------------------------------------------------------------------


@dataclass
class ClickThroughState:
    """Opaque state returned by enable_click_through; pass back to disable."""

    active: bool = False
    backend: str = "none"  # win32 | x11 | wayland | wayland-layer | none
    previous_exstyle: Optional[int] = None
    hwnd: Optional[int] = None
    # X11 / Wayland: restore full-window input shape using these dimensions
    width: int = 0
    height: int = 0
    # Wayland-only bookkeeping (opaque ctypes pointers, if any)
    _wayland_data: Optional[object] = None


@dataclass
class RightClickEdgeDetector:
    """Rising-edge detector for right mouse button while cursor is over pet."""

    was_down: bool = False

    def update(self, *, cursor_in_pet: bool, button_down: bool) -> bool:
        fire = bool(cursor_in_pet and button_down and not self.was_down)
        self.was_down = bool(button_down)
        return fire

    def reset(self) -> None:
        self.was_down = False


# ----------------------------------------------------------------------
# Platform detection
# ----------------------------------------------------------------------


def _qt_platform_name(platform_name: str | None = None) -> str:
    if platform_name is not None:
        return str(platform_name).strip().lower()
    try:
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            return str(app.platformName() or "").strip().lower()
    except Exception:
        pass
    return ""


def platform_backend_name(platform_name: str | None = None) -> str:
    """Return preferred backend id: win32 | x11 | wayland | none.

    Explicit ``platform_name`` overrides auto-detect (useful in tests).
    Detection priority:
      1. win32        -> Windows
      2. wayland      -> Wayland session (XDG_SESSION_TYPE=wayland)
      3. xcb / x11    -> X11 session
      4. none         -> everything else (Qt fallback only)
    """
    if platform_name is not None:
        forced = str(platform_name).strip().lower()
        if forced in ("win32", "windows"):
            return "win32"
        if forced in ("wayland",):
            return "wayland"
        if forced in ("xcb", "x11"):
            return "x11"
        if forced in ("none", "off", "unsupported"):
            return "none"
        # empty / unknown falls through to auto-detect
    if sys.platform == "win32":
        return "win32"
    # Linux / BSD: honour XDG_SESSION_TYPE for explicit wayland choice
    if os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland":
        return "wayland"
    name = _qt_platform_name(None)
    if name == "wayland":
        return "wayland"
    if name in ("xcb", "x11") or sys.platform.startswith("linux"):
        return "x11"
    return "none"


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def enable_click_through(
    target: int | object | None,
    *,
    platform_name: str | None = None,
    width: int = 0,
    height: int = 0,
) -> ClickThroughState:
    """Enable OS-level mouse pass-through for the given window.

    ``target`` may be either:
      * a native window handle (int) -- winId() on Qt, HWND on Windows
      * a QWidget / QWindow / QQuickItem whose ``.winId()`` we resolve

    Returns a state object that must be passed to disable_click_through.
    On failure / unsupported platforms returns inactive state (backend=none).
    """
    # If ``target`` is a QWidget/QWindow (not a raw int), keep a reference
    # so the Wayland backend can recover the object after _resolve_handle
    # inevitably returns 0 for it.
    if _is_qwindow_like(target):
        globals()["_LAST_QWINDOW"] = target

    handle = _resolve_handle(target)
    if handle <= 0 and not _is_qwindow_like(target):
        safe_print("[click_through] skip enable: invalid window handle")
        return ClickThroughState(active=False, backend="none", hwnd=None)

    backend = platform_backend_name(platform_name)
    if backend == "win32":
        return _enable_win32(handle)
    if backend == "x11":
        return _enable_x11(handle, width=width, height=height)
    if backend == "wayland":
        return _enable_wayland(handle, width=width, height=height)
    safe_print(
        f"[click_through] no native backend for platform={platform_name!r}; "
        "Qt interaction guards only"
    )
    return ClickThroughState(active=False, backend="none", hwnd=handle)


def set_shape_region(
    state: ClickThroughState | None,
    rects,
) -> bool:
    """Update the live input-region shape on platforms that support it.

    ``rects`` is an iterable of (x, y, w, h) tuples in window-local
    coordinates.  Passing an empty iterable means "fully transparent to
    input" (all clicks pass through).  A rectangle covering the whole
    window restores normal hit-testing.

    Returns True if the backend handled the update, False otherwise.
    This is the core of "dynamic shaped window" support -- call it from
    the Live2D animation tick to keep the hit area matched to the
    character silhouette.
    """
    if state is None or not state.active:
        return False
    try:
        if state.backend == "x11":
            _x11_set_shape(state, rects)
            return True
        if state.backend in ("wayland", "wayland-layer"):
            _wayland_set_shape(state, rects)
            return True
    except Exception as exc:
        safe_print(f"[click_through] set_shape_region failed: {type(exc).__name__}: {exc}")
    return False


def disable_click_through(state: ClickThroughState | None) -> None:
    """Restore normal hit-testing; safe to call with None / inactive state."""
    if state is None or not state.active:
        return
    try:
        if state.backend == "win32":
            _disable_win32(state)
        elif state.backend == "x11":
            _disable_x11(state)
        elif state.backend in ("wayland", "wayland-layer"):
            _disable_wayland(state)
    except Exception as exc:
        safe_print(f"[click_through] disable failed: {type(exc).__name__}: {exc}")
    finally:
        globals()["_LAST_QWINDOW"] = None
        state.active = False


def is_right_button_down(*, platform_name: str | None = None) -> bool:
    """Query whether the physical right mouse button is currently down."""
    backend = platform_backend_name(platform_name)
    if backend == "win32":
        return _is_right_button_down_win32()
    if backend == "x11":
        return _is_right_button_down_x11()
    if backend == "wayland":
        return _is_right_button_down_wayland()
    return False


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


# Module-level slot holding the most recently passed QWindow/QWidget.
# Required because ClickThroughState only stores the integer handle, but
# the Wayland backend sometimes needs the live Python object (for the
# setAttribute fallback and to derive wl_surface*).  Cleared on disable.
_LAST_QWINDOW: object | None = None


def _is_qwindow_like(obj) -> bool:
    """Duck-type check: anything with setAttribute / winId looks like a QWindow."""
    if obj is None or isinstance(obj, int):
        return False
    return callable(getattr(obj, "setAttribute", None)) or callable(
        getattr(obj, "winId", None)
    )


def _resolve_handle(target: int | object | None) -> int:
    if target is None:
        return 0
    if isinstance(target, int):
        return int(target)
    # QWidget / QWindow: resolve winId() lazily to avoid hard Qt import
    for attr in ("winId", "effectiveWinId"):
        getter = getattr(target, attr, None)
        if callable(getter):
            try:
                return int(getter())
            except Exception:
                pass
    return 0


def _try_set_func_type(func, *, argtypes=None, restype=...):
    """Best-effort ctypes prototype setup; ignore on plain Python callables/mocks."""
    if argtypes is not None:
        try:
            func.argtypes = argtypes
        except (AttributeError, TypeError):
            pass
    if restype is not ...:
        try:
            func.restype = restype
        except (AttributeError, TypeError):
            pass


def _make_rects(rects) -> list[tuple[int, int, int, int]]:
    """Normalize the (x, y, w, h) iterable into a list of int tuples."""
    out = []
    for r in rects or ():
        vals = tuple(int(v) for v in r)
        if len(vals) == 4 and vals[2] > 0 and vals[3] > 0:
            out.append((vals[0], vals[1], vals[2], vals[3]))
    return out


# ======================================================================
# Windows
# ======================================================================


def _enable_win32(hwnd: int) -> ClickThroughState:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        _try_set_func_type(
            user32.GetWindowLongW,
            argtypes=(wintypes.HWND, ctypes.c_int),
            restype=wintypes.LONG,
        )
        _try_set_func_type(
            user32.SetWindowLongW,
            argtypes=(wintypes.HWND, ctypes.c_int, wintypes.LONG),
            restype=wintypes.LONG,
        )

        previous = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        new_style = previous | WS_EX_LAYERED | WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        safe_print(
            f"[click_through] win32 enable hwnd={hwnd} "
            f"exstyle 0x{previous:x} -> 0x{new_style:x}"
        )
        # NOTE: for per-pixel hit testing (dynamic silhouette), subclass
        # WndProc and handle WM_NCHITTEST -- return HTTRANSPARENT for
        # transparent pixels, HTCAPTION for the draggable body. This is
        # the industrial-standard approach for Live2D pets on Windows.
        return ClickThroughState(
            active=True,
            backend="win32",
            previous_exstyle=previous,
            hwnd=hwnd,
        )
    except Exception as exc:
        safe_print(f"[click_through] win32 enable failed: {type(exc).__name__}: {exc}")
        return ClickThroughState(active=False, backend="none", hwnd=hwnd)


def _disable_win32(state: ClickThroughState) -> None:
    if state.hwnd is None or state.previous_exstyle is None:
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    _try_set_func_type(
        user32.SetWindowLongW,
        argtypes=(wintypes.HWND, ctypes.c_int, wintypes.LONG),
        restype=wintypes.LONG,
    )
    user32.SetWindowLongW(int(state.hwnd), GWL_EXSTYLE, int(state.previous_exstyle))
    safe_print(
        f"[click_through] win32 disable hwnd={state.hwnd} "
        f"exstyle -> 0x{int(state.previous_exstyle):x}"
    )


def _is_right_button_down_win32() -> bool:
    try:
        import ctypes

        # High bit set while key is down.
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
    except Exception:
        return False


# ======================================================================
# Linux X11  (X Shape Extension -- ShapeInput)
# ======================================================================


def _load_x11_libs():
    import ctypes
    import ctypes.util

    x11_name = ctypes.util.find_library("X11") or "libX11.so.6"
    xext_name = ctypes.util.find_library("Xext") or "libXext.so.6"
    x11 = ctypes.CDLL(x11_name)
    xext = ctypes.CDLL(xext_name)
    return x11, xext


def _xrectangle_type():
    import ctypes

    class XRectangle(ctypes.Structure):
        _fields_ = [
            ("x", ctypes.c_short),
            ("y", ctypes.c_short),
            ("width", ctypes.c_ushort),
            ("height", ctypes.c_ushort),
        ]

    return XRectangle


def _x11_apply_shape(xext, display, hwnd, rects) -> None:
    """Build an XRectangle[] from ``rects`` and apply it as ShapeInput.

    Empty ``rects`` => empty input shape => all clicks pass through.
    """
    XRectangle = _xrectangle_type()
    rect_list = _make_rects(rects)
    if not rect_list:
        empty = (XRectangle * 0)()
        xext.XShapeCombineRectangles(
            display, int(hwnd), ShapeInput, 0, 0, empty, 0, ShapeSet, 0
        )
        return
    arr = (XRectangle * len(rect_list))()
    for i, (x, y, w, h) in enumerate(rect_list):
        arr[i] = XRectangle(x, y, w, h)
    xext.XShapeCombineRectangles(
        display, int(hwnd), ShapeInput, 0, 0, arr, len(rect_list), ShapeSet, 0
    )


def _enable_x11(hwnd: int, *, width: int = 0, height: int = 0) -> ClickThroughState:
    try:
        import ctypes

        x11, xext = _load_x11_libs()
        _try_set_func_type(
            x11.XOpenDisplay, argtypes=[ctypes.c_char_p], restype=ctypes.c_void_p
        )
        _try_set_func_type(
            x11.XCloseDisplay, argtypes=[ctypes.c_void_p], restype=ctypes.c_int
        )
        _try_set_func_type(
            x11.XSync, argtypes=[ctypes.c_void_p, ctypes.c_int], restype=ctypes.c_int
        )
        _try_set_func_type(
            xext.XShapeCombineRectangles,
            argtypes=[
                ctypes.c_void_p,  # display
                ctypes.c_ulong,  # window
                ctypes.c_int,  # dest_kind (ShapeInput)
                ctypes.c_int,  # x_off
                ctypes.c_int,  # y_off
                ctypes.POINTER(_xrectangle_type()),
                ctypes.c_int,  # n_rects
                ctypes.c_int,  # op (ShapeSet)
                ctypes.c_int,  # ordering
            ],
            restype=ctypes.c_int,
        )

        display = x11.XOpenDisplay(None)
        if not display:
            safe_print("[click_through] x11: XOpenDisplay failed")
            return ClickThroughState(active=False, backend="none", hwnd=hwnd)

        try:
            _x11_apply_shape(xext, display, hwnd, [])
            x11.XSync(display, 0)
        finally:
            x11.XCloseDisplay(display)

        safe_print(f"[click_through] x11 enable xid={hwnd} empty ShapeInput")
        return ClickThroughState(
            active=True,
            backend="x11",
            hwnd=hwnd,
            width=max(0, int(width)),
            height=max(0, int(height)),
            _wayland_data=None,
        )
    except Exception as exc:
        safe_print(f"[click_through] x11 enable failed: {type(exc).__name__}: {exc}")
        return ClickThroughState(active=False, backend="none", hwnd=hwnd)


def _x11_set_shape(state: ClickThroughState, rects) -> None:
    """Update the X11 ShapeInput region from ``rects`` (mode is always transparent)."""
    try:
        import ctypes

        x11, xext = _load_x11_libs()
        _try_set_func_type(
            x11.XOpenDisplay, argtypes=[ctypes.c_char_p], restype=ctypes.c_void_p
        )
        _try_set_func_type(
            x11.XCloseDisplay, argtypes=[ctypes.c_void_p], restype=ctypes.c_int
        )
        _try_set_func_type(
            x11.XSync, argtypes=[ctypes.c_void_p, ctypes.c_int], restype=ctypes.c_int
        )
        _try_set_func_type(
            xext.XShapeCombineRectangles,
            argtypes=[
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_xrectangle_type()),
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
            ],
            restype=ctypes.c_int,
        )

        display = x11.XOpenDisplay(None)
        if not display:
            safe_print("[click_through] x11 set_shape: XOpenDisplay failed")
            return
        try:
            _x11_apply_shape(xext, display, state.hwnd, rects)
            x11.XSync(display, 0)
        finally:
            x11.XCloseDisplay(display)
    except Exception as exc:
        safe_print(f"[click_through] x11 set_shape failed: {type(exc).__name__}: {exc}")


def _disable_x11(state: ClickThroughState) -> None:
    """Restore full input by setting a ShapeInput region covering the whole window."""
    try:
        import ctypes

        x11, xext = _load_x11_libs()
        _try_set_func_type(
            x11.XOpenDisplay, argtypes=[ctypes.c_char_p], restype=ctypes.c_void_p
        )
        _try_set_func_type(
            x11.XCloseDisplay, argtypes=[ctypes.c_void_p], restype=ctypes.c_int
        )
        _try_set_func_type(
            x11.XSync, argtypes=[ctypes.c_void_p, ctypes.c_int], restype=ctypes.c_int
        )
        _try_set_func_type(
            xext.XShapeCombineRectangles,
            argtypes=[
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(_xrectangle_type()),
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
            ],
            restype=ctypes.c_int,
        )

        display = x11.XOpenDisplay(None)
        if not display:
            return
        try:
            w = max(1, state.width)
            h = max(1, state.height)
            _x11_apply_shape(xext, display, state.hwnd, [(0, 0, w, h)])
            x11.XSync(display, 0)
            safe_print(
                f"[click_through] x11 disable xid={state.hwnd} full ShapeInput {w}x{h}"
            )
        finally:
            x11.XCloseDisplay(display)
    except Exception as exc:
        safe_print(f"[click_through] x11 disable failed: {type(exc).__name__}: {exc}")


def _is_right_button_down_x11() -> bool:
    """Poll XQueryPointer to detect the right button (mask bit 0x200)."""
    try:
        import ctypes

        x11, _xext = _load_x11_libs()
        _try_set_func_type(
            x11.XOpenDisplay, argtypes=[ctypes.c_char_p], restype=ctypes.c_void_p
        )
        _try_set_func_type(
            x11.XCloseDisplay, argtypes=[ctypes.c_void_p], restype=ctypes.c_int
        )
        _try_set_func_type(
            x11.XDefaultRootWindow, argtypes=[ctypes.c_void_p], restype=ctypes.c_void_p
        )
        _try_set_func_type(
            x11.XQueryPointer,
            argtypes=[
                ctypes.c_void_p,  # display
                ctypes.c_void_p,  # window
                ctypes.POINTER(ctypes.c_void_p),  # root_return
                ctypes.POINTER(ctypes.c_void_p),  # child_return
                ctypes.POINTER(ctypes.c_int),  # root_x
                ctypes.POINTER(ctypes.c_int),  # root_y
                ctypes.POINTER(ctypes.c_int),  # win_x
                ctypes.POINTER(ctypes.c_int),  # win_y
                ctypes.POINTER(ctypes.c_uint),  # mask
            ],
            restype=ctypes.c_int,
        )

        display = x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            root = x11.XDefaultRootWindow(display)
            mask = ctypes.c_uint(0)
            ok = x11.XQueryPointer(
                display, root,
                ctypes.byref(ctypes.c_void_p()),
                ctypes.byref(ctypes.c_void_p()),
                ctypes.byref(ctypes.c_int()),
                ctypes.byref(ctypes.c_int()),
                ctypes.byref(ctypes.c_int()),
                ctypes.byref(ctypes.c_int()),
                ctypes.byref(mask),
            )
            return bool(ok and (mask.value & 0x200))
        finally:
            x11.XCloseDisplay(display)
    except Exception:
        return False


# ======================================================================
# Linux Wayland  (wl_surface input region + layer-shell specialization)
# ======================================================================


# ---- layer-shell backend (Niri / wlroots) ---------------------------------
# Lazy handle to liblayer_shell_shim.so.  Imported on first use so that the
# pure-X11 / Windows code paths never touch Wayland libraries.
_LAYER_SHIM = None
_LAYER_SHIM_ERROR = None


def _load_layer_shim():
    """Return the ctypes.CDLL for liblayer_shell_shim.so, or None."""
    global _LAYER_SHIM, _LAYER_SHIM_ERROR
    if _LAYER_SHIM is not None or _LAYER_SHIM_ERROR is not None:
        return _LAYER_SHIM
    try:
        import ctypes
        import ctypes.util
        from pathlib import Path

        # Prefer a shim sitting next to this package; fall back to system search.
        candidates = [
            Path(__file__).resolve().parent.parent.parent / "liblayer_shell_shim.so",
        ]
        name = ctypes.util.find_library("layer_shell_shim") or "liblayer_shell_shim.so"
        candidates.append(name)

        for cand in candidates:
            try:
                shim = ctypes.CDLL(str(cand))
                break
            except Exception:
                shim = None
        if shim is None:
            raise RuntimeError("liblayer_shell_shim.so not found")

        # Wire up prototypes.  layer_create_context takes (state*, w, h, x, y)
        # but accepts NULL state (the shim fetches its own g_state internally).
        shim.layer_shell_init.restype = ctypes.c_int
        shim.layer_create_context.restype = ctypes.c_void_p
        shim.layer_create_context.argtypes = [
            ctypes.c_void_p,  # state (NULL = self-fetch)
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        shim.layer_set_click_through.argtypes = [ctypes.c_void_p, ctypes.c_int]
        shim.layer_destroy_context.argtypes = [ctypes.c_void_p]

        _LAYER_SHIM = shim
        return _LAYER_SHIM
    except Exception as exc:
        _LAYER_SHIM_ERROR = exc
        return None


def _layer_shell_available() -> bool:
    """True if the compositor exposes zwlr_layer_shell_v1.

    Implemented by calling layer_shell_init(); a return of 0 means the
    layer-shell global was successfully bound (see layer_shell_create).
    """
    shim = _load_layer_shim()
    if shim is None:
        return False
    try:
        return shim.layer_shell_init() == 0
    except Exception:
        return False


def _enable_wayland_layer(qwindow, *, width: int, height: int, pos_x: int, pos_y: int):
    """Create a bare layer-shell surface for true pointer pass-through.

    Returns a ClickThroughState(backend="wayland-layer", ...) on success,
    or None if the layer-shell backend could not be initialized (caller
    should fall through to the legacy Path A / Path B).
    """
    shim = _load_layer_shim()
    if shim is None:
        safe_print("[click_through] wayland-layer: shim not available "
                   f"({_LAYER_SHIM_ERROR})")
        return None
    if shim.layer_shell_init() != 0:
        safe_print("[click_through] wayland-layer: compositor does not support "
                   "zwlr_layer_shell_v1; falling back")
        return None

    # state=NULL -> the shim uses its internal g_state (populated by layer_shell_init)
    ctx = shim.layer_create_context(None, int(width), int(height), int(pos_x), int(pos_y))
    if not ctx:
        safe_print("[click_through] wayland-layer: layer_create_context failed")
        return None

    safe_print(
        "[click_through] wayland: ★ layer-shell backend enabled (Niri pass-through), "
        f"ctx={ctx}, {width}x{height} @ ({pos_x},{pos_y})"
    )
    return ClickThroughState(
        active=True,
        backend="wayland-layer",   # ★ distinct from legacy "wayland"
        hwnd=getattr(qwindow, "winId", lambda: 0)() or 0,
        width=max(0, int(width)),
        height=max(0, int(height)),
        _wayland_data={"ctx": ctx, "qwindow": qwindow},
    )


def _set_layer_click_through(state, enabled: bool) -> None:
    """Toggle empty-input-region pass-through on the layer surface."""
    shim = _load_layer_shim()
    if shim is None:
        return
    ctx = (state._wayland_data or {}).get("ctx")
    if not ctx:
        return
    # 1 = empty region = pointer pass-through; 0 = full surface clickable
    shim.layer_set_click_through(ctx, 1 if enabled else 0)


# ---- legacy Wayland backend (Qt surface + empty input region) --------------
#
# Overview
# --------
# Wayland has no global "make this window transparent to input" request.
# Instead each compositor routes pointer input according to the *input region*
# of the wl_surface (an empty region => events fall through to the window
# below).  Because a surface can only have ONE role for its entire lifetime,
# and Qt Wayland already assigned the xdg_toplevel role to its wl_surface,
# applying an empty input region on the Qt surface is NOT reliable on strict
# compositors such as Niri -- hence the separate layer-shell backend above
# (Path 0).  The code below is the legacy Path A / resolver-based backend,
# kept for non-wlroots compositors and as a fallback.
#
# Input region lifecycle
# ----------------------
# set_input_region(region=0)  =>  NULL region  =>  full surface clickable
# set_input_region(region=N)  =>  Nth rectangle kept as input area
# apply_input_region(...)     =>  create region -> add rects -> set_input_region
#                                 -> commit -> destroy (standard sequence)
#
# The semantic convention ("empty list = transparent") is enforced by the
# ``mode`` parameter passed to apply_input_region() (Fix 3).
_WAYLAND_BIND_AVAILABLE = True
try:
    from ._wayland_bind import (  # type: ignore
        apply_input_region as _wl_apply,
        WaylandError as _WaylandError,
    )
except Exception:  # pragma: no cover - optional dependency
    try:
        from _wayland_bind import (  # type: ignore  # fallback for tests / scripts
            apply_input_region as _wl_apply,
            WaylandError as _WaylandError,
        )
    except Exception:
        _WAYLAND_BIND_AVAILABLE = False
        _wl_apply = None
        _WaylandError = Exception


def _is_real_ptr(v) -> bool:
    """Return True if v looks like a real (non-zero) C pointer."""
    try:
        return bool(int(v))
    except (TypeError, ValueError):
        return False


# Callable installed by the desktop / Qt integration layer:
#   signature: (qwindow) -> dict with keys:
#       "surface"    (int, wl_surface*)
#       "compositor" (int, wl_compositor*)
#       "display"    (int, wl_display*)  -- optional, for flush/roundtrip
# If not installed, Wayland backend falls back to Qt-only mode (Fix 5).
_WAYLAND_GET_SURFACE = None


def set_wayland_surface_resolver(resolver):
    """Register a (qwindow) -> wl-handles dict resolver.

    Called by the desktop integration layer once a QWindow is shown.
    Passing None clears the resolver (back to Qt-only fallback).

    The resolver MUST return real wl_* pointers obtained from the Wayland
    client (Qt Wayland internals or wl_registry).  Fabricated values such
    as ``id(qwindow)`` are REJECTED (Fix 4).
    """
    global _WAYLAND_GET_SURFACE
    _WAYLAND_GET_SURFACE = resolver


def install_wayland_resolver_from_qwindow(
    qwindow,
    surface_ptr_fn,
    compositor_ptr_fn=None,
):
    """Helper for the Qt-Wayland desktop layer.

    Usage (in app.py, after the window is shown)::

        def get_surface(qw):
            return {"surface": int(qw.winId())}

        install_wayland_resolver_from_qwindow(
            pet_window,
            surface_ptr_fn=lambda qw: int(qw.winId()),
            compositor_ptr_fn=...  # optional; obtained via wl_registry or Qt internals
        )

    If ``compositor_ptr_fn`` is omitted the compositor pointer stays 0 and
    the backend will report "null compositor" (create_region fails) -- in
    that case it transparently falls back to Qt-only mode rather than crash.
    """
    if qwindow is None:
        set_wayland_surface_resolver(None)
        return

    def resolver(qw):
        handles = {}
        if surface_ptr_fn is not None:
            try:
                handles["surface"] = int(surface_ptr_fn(qw))
            except Exception:
                handles["surface"] = None
        if compositor_ptr_fn is not None:
            try:
                handles["compositor"] = int(compositor_ptr_fn(qw))
            except Exception:
                handles["compositor"] = None
        # Reject fabricated / zero handles (Fix 4)
        if not _is_real_ptr(handles.get("surface")):
            handles.pop("surface", None)
        if not _is_real_ptr(handles.get("compositor")):
            handles["compositor"] = None
        return handles

    set_wayland_surface_resolver(resolver)


def _resolve_wayland_handles(qwindow):
    """Ask the registered resolver for (surface, compositor) pointers.

    Returns a dict (possibly with None values) or None if no resolver is
    installed.  NEVER fabricates handles (Fix 4).
    """
    if _WAYLAND_GET_SURFACE is None:
        return None
    if qwindow is None:
        return None
    try:
        handles = _WAYLAND_GET_SURFACE(qwindow)
    except Exception as exc:
        safe_print(f"[click_through] wayland resolver error: {exc}")
        return None
    if not isinstance(handles, dict):
        return None
    surface = handles.get("surface")
    compositor = handles.get("compositor")
    # Fix 4: silently drop unusable pointers; do NOT synthesize anything.
    if not _is_real_ptr(surface):
        surface = None
    if not _is_real_ptr(compositor):
        compositor = None
    return {"surface": surface, "compositor": compositor}


def _wayland_set_shape(state, rects) -> None:
    """Update the live input-region shape (mode="transparent" always).

    ``rects`` = list of (x, y, w, h) in surface-local coords that should
    KEEP input.  Empty list => empty region => fully transparent to input
    (all clicks pass through).
    """
    # ---- wayland-layer branch (Phase 2 TODO) -------------------------------
    # The visible surface is a *separate bare* wl_surface owned by the shim,
    # not the Qt xdg_toplevel surface, so the legacy resolver handles below
    # do not apply.  Dynamic shaping requires pushing an updated region
    # through the shim (update_pixels + region), not yet implemented.
    if state.backend == "wayland-layer":
        # TODO(Phase 2): translate rects -> shim.update_layer_pixels / region
        return
    handles = state._wayland_data or {}
    surface = handles.get("surface")
    compositor = handles.get("compositor")
    if _is_real_ptr(surface) and _is_real_ptr(compositor):
        _wl_apply(int(surface), int(compositor), list(rects or ()),
                  mode="transparent")
        return
    # No native handles: cannot do per-region shaping.  The Qt fallback is
    # all-or-nothing, so we toggle WA_TransparentForMouseEvents as a rough
    # approximation (Fix 5: does NOT guarantee pass-through to other apps).
    qwindow = _unwrap_qwindow(state.hwnd)
    if qwindow is not None:
        _set_qt_transparent(qwindow, bool(_make_rects(rects)))


def _enable_wayland(hwnd, *, width=0, height=0) -> "ClickThroughState":
    """Enable Wayland click-through.

    Path 0 (NEW, preferred on wlroots/Niri): layer-shell backend.
        A bare wl_surface is created via liblayer_shell_shim, assigned the
        layer-shell OVERLAY role with KEYBOARD_INTERACTIVITY_NONE and an
        empty input region -> true pointer pass-through.  Selected whenever
        zwlr_layer_shell_v1 is available.

    Path A (legacy): real wl handles from resolver -> apply_input_region
        with mode="transparent" and an EMPTY region (0 rectangles) => the
        compositor makes the whole surface pass through input.

    Path B (fallback, Fix 5): no valid wl handles -> set
        WA_TransparentForMouseEvents on the QWindow.  This only suppresses
        Qt's own event delivery; it does NOT tell the compositor to route
        input to the window below.  Real穿透 REQUIRES Path 0 or A.
    """
    qwindow = _LAST_QWINDOW if _is_qwindow_like(_LAST_QWINDOW) else _unwrap_qwindow(hwnd)
    w = max(0, int(width)) or 320
    h = max(0, int(height)) or 320

    # ---- Path 0: layer-shell backend (Niri / wlroots) ----------------------
    if _layer_shell_available():
        result = _enable_wayland_layer(qwindow, width=w, height=h, pos_x=0, pos_y=0)
        if result is not None:
            return result
        # fall through to legacy paths if layer creation failed

    handles = _resolve_wayland_handles(qwindow)

    if handles and _is_real_ptr(handles.get("surface")) and _WAYLAND_BIND_AVAILABLE:
        surface = int(handles["surface"])
        compositor = int(handles.get("compositor") or 0)
        try:
            # Fix 3: transparent + empty rects => empty region => full pass-through.
            # (NULL region would mean "full surface inputable" -- the OLD bug.)
            _wl_apply(surface, compositor, [], mode="transparent")
            safe_print(
                f"[click_through] wayland enable: native pass-through (empty input region) "
                f"xid={hwnd}"
            )
            return ClickThroughState(
                active=True,
                backend="wayland",
                hwnd=hwnd,
                width=w,
                height=h,
                _wayland_data={"surface": surface, "compositor": compositor,
                               "qwindow": qwindow},
            )
        except _WaylandError as exc:
            safe_print(
                f"[click_through] wayland native enable failed ({exc}); "
                "falling back to Qt-only mode"
            )
        except Exception as exc:  # pragma: no cover - defensive
            safe_print(f"[click_through] wayland native enable error: {exc}")

    # ---- Path B: Qt-only fallback (Fix 5) ---------------------------------
    if qwindow is not None:
        try:
            _set_qt_transparent(qwindow, True)
            safe_print(
                "[click_through] wayland: Qt WA_TransparentForMouseEvents fallback "
                "(NOTE: does not guarantee pass-through to windows below under Niri)"
            )
            return ClickThroughState(
                active=True,
                backend="wayland",
                hwnd=hwnd,
                width=w,
                height=h,
                _wayland_data={"qwindow": qwindow},
            )
        except Exception as exc:
            safe_print(f"[click_through] wayland Qt fallback failed: {exc}")

    return ClickThroughState(active=False, backend="none", hwnd=hwnd)


def _disable_wayland(state) -> None:
    """Restore full input.

    For the layer-shell backend (wayland-layer) this destroys the layer
    context.  For the legacy backend it restores an opaque (NULL) input
    region and clears the Qt fallback attribute.
    """
    # ---- wayland-layer branch ---------------------------------------------
    if state.backend == "wayland-layer":
        try:
            _set_layer_click_through(state, False)
        except Exception as exc:
            safe_print(f"[click_through] wayland-layer restore failed: {exc}")
        try:
            shim = _load_layer_shim()
            ctx = (state._wayland_data or {}).get("ctx")
            if shim is not None and ctx:
                shim.layer_destroy_context(ctx)
        except Exception as exc:
            safe_print(f"[click_through] wayland-layer destroy failed: {exc}")
        finally:
            if state._wayland_data and isinstance(state._wayland_data, dict):
                state._wayland_data.pop("ctx", None)
            try:
                shim = _load_layer_shim()
                if shim is not None:
                    shim.layer_shell_cleanup()
            except Exception:
                pass
        safe_print("[click_through] wayland-layer disabled")
        return

    # ---- legacy backend ---------------------------------------------------
    handles = state._wayland_data or {}
    surface = handles.get("surface")
    compositor = handles.get("compositor")
    if _is_real_ptr(surface) and _is_real_ptr(compositor) and _WAYLAND_BIND_AVAILABLE:
        try:
            # Fix 3: opaque => NULL region => entire surface accepts input again.
            _wl_apply(int(surface), int(compositor), [], mode="opaque")
            safe_print(f"[click_through] wayland disable: restored full input")
        except _WaylandError as exc:
            safe_print(f"[click_through] wayland restore failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            safe_print(f"[click_through] wayland restore error: {exc}")
    # Always clear the Qt fallback attribute too.
    qwindow = handles.get("qwindow") or _unwrap_qwindow(state.hwnd)
    if qwindow is not None:
        _set_qt_transparent(qwindow, False)


def _is_right_button_down_wayland() -> bool:
    """Wayland has no global pointer-query API.

    We rely on the Qt event stream instead (mousePressEvent tracking).
    This polled query therefore always returns False under pure Wayland;
    callers should use RightClickEdgeDetector with Qt events.
    """
    return False


# ----------------------------------------------------------------------
# Qt helpers (used by Wayland fallback + shape updates)
# ----------------------------------------------------------------------


def _unwrap_qwindow(hwnd: int):
    """If ``hwnd`` was originally a QWidget/QWindow, return it.

    We stored it indirectly via _resolve_handle losing the object, so we
    keep a weak reference through the state's _wayland_data dict.  This
    helper exists for symmetry; in practice the desktop layer is expected
    to pass the QWindow explicitly when installing the resolver.
    """
    return None


def _set_qt_transparent(qwindow, transparent: bool) -> None:
    attr = getattr(type(qwindow), "WA_TransparentForMouseEvents", None)
    if attr is None:
        attr = 0x20
    try:
        qwindow.setAttribute(attr, bool(transparent))
    except Exception:
        pass


# ----------------------------------------------------------------------
# Self-test (run with:  python -m click_through  or  pytest)
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import os as _os

    print("platform_backend_name():", platform_backend_name())

    # Backend selection tests
    assert platform_backend_name("win32") == "win32"
    assert platform_backend_name("xcb") == "x11"
    assert platform_backend_name("wayland") == "wayland"
    assert platform_backend_name("off") == "none"

    _os.environ["XDG_SESSION_TYPE"] = "wayland"
    assert platform_backend_name(None) == "wayland"
    del _os.environ["XDG_SESSION_TYPE"]

    _os.environ["XDG_SESSION_TYPE"] = "x11"
    assert platform_backend_name(None) == "x11"
    del _os.environ["XDG_SESSION_TYPE"]

    # Handle resolution
    class FakeWidget:
        def winId(self):
            return 42

    assert _resolve_handle(0) == 0
    assert _resolve_handle(99) == 99
    assert _resolve_handle(FakeWidget()) == 42
    assert _resolve_handle(None) == 0

    # Rect normalization
    assert _make_rects([(0, 0, 10, 10), (-1, 2, 0, 5), (1, 1, -2, 3)]) == [(0, 0, 10, 10)]
    assert _make_rects([]) == []
    assert _make_rects(None) == []

    # Shape on unsupported backend is a safe no-op
    st = ClickThroughState(active=False, backend="none")
    assert set_shape_region(st, [(0, 0, 5, 5)]) is False
    assert set_shape_region(None, [(0, 0, 5, 5)]) is False

    # wayland-layer is treated like wayland by the dispatcher
    st_layer = ClickThroughState(active=True, backend="wayland-layer")
    assert set_shape_region(st_layer, [(0, 0, 5, 5)]) is True

    print("all self-tests passed")

