"""Standby click-through helpers for Windows / Linux X11.

Native backends make the pet window ignore mouse input so clicks reach
windows below. Right-click is re-captured out-of-band (see host poll)
because full-window transparent-for-input also drops RMB.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

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


@dataclass
class ClickThroughState:
    """Opaque state returned by enable_click_through; pass back to disable."""

    active: bool = False
    backend: str = "none"  # win32 | x11 | none
    previous_exstyle: Optional[int] = None
    hwnd: Optional[int] = None
    # X11: restore full-window input shape using these dimensions if known
    width: int = 0
    height: int = 0


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
    """Return preferred backend id: win32 | x11 | none.

    Explicit ``platform_name`` overrides auto-detect (useful in tests).
    """
    if platform_name is not None:
        forced = str(platform_name).strip().lower()
        if forced in ("win32", "windows"):
            return "win32"
        if forced in ("xcb", "x11"):
            return "x11"
        if forced in ("wayland", "none", "off", "unsupported"):
            return "none"
        # empty string falls through to auto-detect
        if forced:
            return "none"
    if sys.platform == "win32":
        return "win32"
    name = _qt_platform_name(None)
    # xcb is the supported Linux path; empty name is treated as xcb when
    # helpers run without a QApplication (unit tests on Linux).
    if name in ("xcb", "") and sys.platform.startswith("linux"):
        return "x11"
    if name == "xcb":
        return "x11"
    return "none"


def enable_click_through(
    hwnd: int,
    *,
    platform_name: str | None = None,
    width: int = 0,
    height: int = 0,
) -> ClickThroughState:
    """Enable OS-level mouse pass-through for the given native window handle.

    Returns a state object that must be passed to disable_click_through.
    On failure / unsupported platforms returns inactive state (backend=none).
    """
    handle = int(hwnd or 0)
    if handle <= 0:
        safe_print("[click_through] skip enable: invalid hwnd")
        return ClickThroughState(active=False, backend="none", hwnd=None)

    backend = platform_backend_name(platform_name)
    if backend == "win32":
        return _enable_win32(handle)
    if backend == "x11":
        return _enable_x11(handle, width=width, height=height)
    safe_print(
        f"[click_through] no native backend for platform={platform_name!r}; "
        "Qt interaction guards only"
    )
    return ClickThroughState(active=False, backend="none", hwnd=handle)


def disable_click_through(state: ClickThroughState | None) -> None:
    """Restore normal hit-testing; safe to call with None / inactive state."""
    if state is None or not state.active:
        return
    try:
        if state.backend == "win32":
            _disable_win32(state)
        elif state.backend == "x11":
            _disable_x11(state)
    except Exception as exc:
        safe_print(f"[click_through] disable failed: {type(exc).__name__}: {exc}")
    finally:
        state.active = False


def is_right_button_down(*, platform_name: str | None = None) -> bool:
    """Query whether the physical right mouse button is currently down."""
    backend = platform_backend_name(platform_name)
    if backend == "win32":
        return _is_right_button_down_win32()
    if backend == "x11":
        return _is_right_button_down_x11()
    return False


# ── Windows ──────────────────────────────────────────────────────────


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


# ── Linux X11 ────────────────────────────────────────────────────────


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
            x11.XDefaultRootWindow,
            argtypes=[ctypes.c_void_p],
            restype=ctypes.c_ulong,
        )

        XRectangle = _xrectangle_type()
        _try_set_func_type(
            xext.XShapeCombineRectangles,
            argtypes=[
                ctypes.c_void_p,  # display
                ctypes.c_ulong,  # window
                ctypes.c_int,  # dest_kind (ShapeInput)
                ctypes.c_int,  # x_off
                ctypes.c_int,  # y_off
                ctypes.POINTER(XRectangle),
                ctypes.c_int,  # n_rects
                ctypes.c_int,  # op (ShapeSet)
                ctypes.c_int,  # ordering
            ],
            restype=None,
        )

        display = x11.XOpenDisplay(None)
        if not display:
            safe_print("[click_through] x11: XOpenDisplay failed")
            return ClickThroughState(active=False, backend="none", hwnd=hwnd)

        try:
            # Empty ShapeInput → all mouse events pass through.
            empty = (XRectangle * 0)()
            xext.XShapeCombineRectangles(
                display,
                int(hwnd),
                ShapeInput,
                0,
                0,
                empty,
                0,
                ShapeSet,
                0,
            )
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
        )
    except Exception as exc:
        safe_print(f"[click_through] x11 enable failed: {type(exc).__name__}: {exc}")
        return ClickThroughState(active=False, backend="none", hwnd=hwnd)


def _disable_x11(state: ClickThroughState) -> None:
    if state.hwnd is None:
        return
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

    XRectangle = _xrectangle_type()
    _try_set_func_type(
        xext.XShapeCombineRectangles,
        argtypes=[
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(XRectangle),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ],
        restype=None,
    )

    display = x11.XOpenDisplay(None)
    if not display:
        safe_print("[click_through] x11 disable: XOpenDisplay failed")
        return
    try:
        w = max(1, int(state.width or 1))
        h = max(1, int(state.height or 1))
        # Prefer actual geometry if caller stored it; otherwise use a large
        # rectangle so the whole window accepts input again.
        if state.width <= 0 or state.height <= 0:
            w, h = 10000, 10000
        rect = XRectangle(0, 0, w, h)
        rects = (XRectangle * 1)(rect)
        xext.XShapeCombineRectangles(
            display,
            int(state.hwnd),
            ShapeInput,
            0,
            0,
            rects,
            1,
            ShapeSet,
            0,
        )
        x11.XSync(display, 0)
        safe_print(
            f"[click_through] x11 disable xid={state.hwnd} full ShapeInput {w}x{h}"
        )
    finally:
        x11.XCloseDisplay(display)


def _is_right_button_down_x11() -> bool:
    """Use XQueryPointer button mask (Button3Mask = 1<<10)."""
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
            x11.XDefaultRootWindow,
            argtypes=[ctypes.c_void_p],
            restype=ctypes.c_ulong,
        )

        root_return = ctypes.c_ulong()
        child_return = ctypes.c_ulong()
        root_x = ctypes.c_int()
        root_y = ctypes.c_int()
        win_x = ctypes.c_int()
        win_y = ctypes.c_int()
        mask_return = ctypes.c_uint()

        _try_set_func_type(
            x11.XQueryPointer,
            argtypes=[
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_uint),
            ],
            restype=ctypes.c_int,
        )

        display = x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            root = x11.XDefaultRootWindow(display)
            ok = x11.XQueryPointer(
                display,
                root,
                ctypes.byref(root_return),
                ctypes.byref(child_return),
                ctypes.byref(root_x),
                ctypes.byref(root_y),
                ctypes.byref(win_x),
                ctypes.byref(win_y),
                ctypes.byref(mask_return),
            )
            if not ok:
                return False
            # Button3Mask
            return bool(int(mask_return.value) & (1 << 10))
        finally:
            x11.XCloseDisplay(display)
    except Exception:
        return False
