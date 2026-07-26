"""OS-level elliptical window shape (visual + hit) without pywin32.

Qt ``setMask`` often only affects hit-testing for ``QOpenGLWidget`` on
packaged Windows (ANGLE / DWM). Setting a native window region clips the
actual window outline so the transparent rectangular “collision box”
disappears.

- Windows: ``CreateEllipticRgn`` + ``SetWindowRgn`` via ctypes
- Linux xcb: XShape ``ShapeBounding`` via horizontal scanline rectangles
"""
from __future__ import annotations

import math
import sys
from typing import Optional

from meapet.config.store import (
    DEFAULT_LIVE2D_WINDOW_MASK,
    normalize_live2d_window_mask,
)
from meapet.utils import safe_print


def ellipse_physical_bounds(
    width: int,
    height: int,
    params: dict | None = None,
    *,
    dpr: float = 1.0,
) -> tuple[int, int, int, int]:
    """Return inclusive-exclusive ellipse AABB in physical pixels.

    Same normalized ratios as ``ellipse_mask_region`` / config
    ``live2d.window_mask``. Coordinates are for the top-level window
    client area at the given device pixel ratio.
    """
    mask = normalize_live2d_window_mask(params or DEFAULT_LIVE2D_WINDOW_MASK)
    scale = max(0.01, float(dpr) if dpr else 1.0)
    w = max(1, int(round(max(1, int(width)) * scale)))
    h = max(1, int(round(max(1, int(height)) * scale)))
    cx_px = int(round(float(mask["cx"]) * w))
    cy_px = int(round(float(mask["cy"]) * h))
    rw_px = max(1, int(round(float(mask["rw"]) * w)))
    rh_px = max(1, int(round(float(mask["rh"]) * h)))
    left = cx_px - rw_px
    top = cy_px - rh_px
    right = cx_px + rw_px
    bottom = cy_px + rh_px
    return left, top, right, bottom


def ellipse_scanline_rects(
    width: int,
    height: int,
    params: dict | None = None,
    *,
    dpr: float = 1.0,
) -> list[tuple[int, int, int, int]]:
    """Approximate ellipse as horizontal rects ``(x, y, w, h)`` in physical px.

    Used by X11 ShapeBounding (no elliptic region API).
    """
    left, top, right, bottom = ellipse_physical_bounds(
        width, height, params, dpr=dpr
    )
    # Ellipse inscribed in [left, right) x [top, bottom) — match GDI CreateEllipticRgn
    # which uses the bounding box inclusively on the edges in practice.
    box_w = max(1, right - left)
    box_h = max(1, bottom - top)
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0
    rx = box_w / 2.0
    ry = box_h / 2.0
    if rx < 0.5 or ry < 0.5:
        return [(left, top, box_w, box_h)]

    rects: list[tuple[int, int, int, int]] = []
    y0 = max(0, top)
    y1 = bottom
    for y in range(y0, y1):
        # row center
        ny = (y + 0.5 - cy) / ry
        if abs(ny) > 1.0:
            continue
        half = rx * math.sqrt(max(0.0, 1.0 - ny * ny))
        x0 = int(math.floor(cx - half))
        x1 = int(math.ceil(cx + half))
        if x1 <= x0:
            x1 = x0 + 1
        rects.append((x0, y, x1 - x0, 1))
    return rects or [(left, top, box_w, box_h)]


def apply_ellipse_window_shape(
    hwnd: int,
    width: int,
    height: int,
    params: dict | None = None,
    *,
    dpr: float = 1.0,
    platform: Optional[str] = None,
) -> bool:
    """Clip native top-level window to the Live2D ellipse. Return True on success."""
    handle = int(hwnd or 0)
    if handle <= 0 or width <= 0 or height <= 0:
        return False

    plat = (platform or sys.platform).lower()
    if plat.startswith("win"):
        return _apply_win32_ellipse(handle, width, height, params, dpr=dpr)
    if plat.startswith("linux"):
        return _apply_x11_ellipse(handle, width, height, params, dpr=dpr)
    return False


def clear_window_shape(
    hwnd: int,
    *,
    width: int = 0,
    height: int = 0,
    platform: Optional[str] = None,
) -> bool:
    """Remove native window shape (full rectangular client area)."""
    handle = int(hwnd or 0)
    if handle <= 0:
        return False

    plat = (platform or sys.platform).lower()
    if plat.startswith("win"):
        return _clear_win32(handle)
    if plat.startswith("linux"):
        return _clear_x11(handle, width=width, height=height)
    return False


# ── Windows (ctypes only) ───────────────────────────────────────────────


def _apply_win32_ellipse(
    hwnd: int,
    width: int,
    height: int,
    params: dict | None,
    *,
    dpr: float,
) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        user32.SetWindowRgn.argtypes = [
            wintypes.HWND,
            wintypes.HRGN,
            wintypes.BOOL,
        ]
        user32.SetWindowRgn.restype = wintypes.BOOL
        gdi32.CreateEllipticRgn.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        gdi32.CreateEllipticRgn.restype = wintypes.HRGN

        left, top, right, bottom = ellipse_physical_bounds(
            width, height, params, dpr=dpr
        )
        # CreateEllipticRgn: right/bottom are exclusive in docs; match Qt ellipse box.
        hrgn = gdi32.CreateEllipticRgn(int(left), int(top), int(right), int(bottom))
        if not hrgn:
            safe_print("[window_shape] CreateEllipticRgn failed")
            return False
        # System owns hrgn after a successful SetWindowRgn — do not DeleteObject.
        ok = bool(user32.SetWindowRgn(wintypes.HWND(hwnd), hrgn, True))
        if not ok:
            try:
                gdi32.DeleteObject(hrgn)
            except Exception:
                pass
            safe_print("[window_shape] SetWindowRgn(ellipse) failed")
            return False
        return True
    except Exception as exc:
        safe_print(
            f"[window_shape] win32 ellipse failed: {type(exc).__name__}: {exc}"
        )
        return False


def _clear_win32(hwnd: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetWindowRgn.argtypes = [
            wintypes.HWND,
            wintypes.HRGN,
            wintypes.BOOL,
        ]
        user32.SetWindowRgn.restype = wintypes.BOOL
        return bool(user32.SetWindowRgn(wintypes.HWND(hwnd), wintypes.HRGN(0), True))
    except Exception as exc:
        safe_print(
            f"[window_shape] win32 clear failed: {type(exc).__name__}: {exc}"
        )
        return False


# ── Linux X11 ───────────────────────────────────────────────────────────


def _load_x11():
    import ctypes
    import ctypes.util

    x11_name = ctypes.util.find_library("X11") or "libX11.so.6"
    xext_name = ctypes.util.find_library("Xext") or "libXext.so.6"
    return ctypes.CDLL(x11_name), ctypes.CDLL(xext_name)


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


def _apply_x11_ellipse(
    hwnd: int,
    width: int,
    height: int,
    params: dict | None,
    *,
    dpr: float,
) -> bool:
    """ShapeBounding = ellipse scanlines so the window outline is not a full rect."""
    try:
        import ctypes

        # ShapeBounding = 0, ShapeSet = 0 (same as click_through)
        shape_bounding = 0
        shape_set = 0

        x11, xext = _load_x11()
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XSync.restype = ctypes.c_int

        XRectangle = _xrectangle_type()
        xext.XShapeCombineRectangles.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(XRectangle),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        xext.XShapeCombineRectangles.restype = None

        rects_data = ellipse_scanline_rects(width, height, params, dpr=dpr)
        if not rects_data:
            return False

        n = len(rects_data)
        arr = (XRectangle * n)()
        for i, (x, y, w, h) in enumerate(rects_data):
            arr[i].x = int(x)
            arr[i].y = int(y)
            arr[i].width = max(1, int(w))
            arr[i].height = max(1, int(h))

        display = x11.XOpenDisplay(None)
        if not display:
            safe_print("[window_shape] x11: XOpenDisplay failed")
            return False
        try:
            xext.XShapeCombineRectangles(
                display,
                int(hwnd),
                shape_bounding,
                0,
                0,
                arr,
                n,
                shape_set,
                0,
            )
            x11.XSync(display, 0)
        finally:
            x11.XCloseDisplay(display)
        return True
    except Exception as exc:
        safe_print(
            f"[window_shape] x11 ellipse failed: {type(exc).__name__}: {exc}"
        )
        return False


def _clear_x11(hwnd: int, *, width: int = 0, height: int = 0) -> bool:
    """Restore full rectangular ShapeBounding."""
    try:
        import ctypes

        shape_bounding = 0
        shape_set = 0
        x11, xext = _load_x11()
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XSync.restype = ctypes.c_int

        XRectangle = _xrectangle_type()
        xext.XShapeCombineRectangles.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(XRectangle),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        xext.XShapeCombineRectangles.restype = None

        w = max(1, int(width) or 1)
        h = max(1, int(height) or 1)
        arr = (XRectangle * 1)()
        arr[0].x = 0
        arr[0].y = 0
        arr[0].width = w
        arr[0].height = h

        display = x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            xext.XShapeCombineRectangles(
                display,
                int(hwnd),
                shape_bounding,
                0,
                0,
                arr,
                1,
                shape_set,
                0,
            )
            x11.XSync(display, 0)
        finally:
            x11.XCloseDisplay(display)
        return True
    except Exception as exc:
        safe_print(
            f"[window_shape] x11 clear failed: {type(exc).__name__}: {exc}"
        )
        return False
