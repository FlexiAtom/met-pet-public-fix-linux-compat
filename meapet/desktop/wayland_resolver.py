"""
wayland_resolver
================

Glue between the Qt Wayland client and :mod:`click_through`'s native
Wayland backend.

The native backend needs **real** ``wl_surface*`` / ``wl_compositor*``
opaque handles.  We cannot fabricate them in pure Python (Fix 4).  This
module obtains them from the running Qt Wayland client and registers a
resolver via :func:`click_through.set_wayland_surface_resolver`.

How to use (in app.py, AFTER the pet window is shown)::

    from meapet.desktop.wayland_resolver import install_resolver_for
    install_resolver_for(self)   # self is the QWindow/QWidget pet window

If this is called, :func:`click_through.enable_click_through` on Wayland
will go through the real ``wl_surface.set_input_region`` path and you
will see the log line::

    wayland enable: native pass-through (empty input region)

If it is NOT called (or the pointers cannot be obtained), the backend
falls back to ``Qt.WA_TransparentForMouseEvents`` (Fix 5, no real穿透).

Getting the pointers
--------------------
Three strategies are tried in order:

1. ``QWindow::winId()`` -- on Wayland Qt returns an opaque wrapper that
   is often the ``wl_surface*`` itself under the hood.  Works for many
   PyQt5 / PySide2 bindings.
2. Qt private API ``QWaylandWindow::wlSurface()`` reached via
   ``QWindow::handle()``.  Requires the ``wayland`` platform plugin and
   is binding-dependent, hence wrapped in try/except.
3. A user-supplied ``surface_ptr_fn`` / ``compositor_ptr_fn`` for full
   control (e.g. obtained through a ``wl_registry`` bind).

The compositor pointer is optional; if unavailable the backend reports
"null compositor" and transparently falls back to Qt-only mode rather
than crash.
"""
from __future__ import annotations

import sys

from PyQt5.QtCore import QObject

try:
    from meapet.desktop.click_through import (
        install_wayland_resolver_from_qwindow,
        set_wayland_surface_resolver,
    )
    _HAS_CT = True
except Exception:  # pragma: no cover
    _HAS_CT = False


def _win_id_surface(qwindow) -> int | None:
    """Strategy 1: QWindow::winId() as wl_surface*."""
    try:
        wid = int(qwindow.winId())
        return wid if wid else None
    except Exception:
        return None


def _qwayland_surface(qwindow) -> int | None:
    """Strategy 2: Qt private QWaylandWindow::wlSurface()."""
    try:
        handle = qwindow.handle()
        if handle is None:
            return None
        # PyQt5: handle.wlSurface() may exist; PySide2 differs.
        for attr in ("wlSurface", "wl_surface", "surface"):
            fn = getattr(handle, attr, None)
            if callable(fn):
                ptr = int(fn())
                if ptr:
                    return ptr
        # Some builds expose a `wlSurface()` on the window directly.
        wls = getattr(qwindow, "wlSurface", None)
        if callable(wls):
            ptr = int(wls())
            if ptr:
                return ptr
    except Exception:
        pass
    return None


def _make_surface_fn(qwindow):
    """Return the first strategy that yields a real pointer."""
    def fn(qw):
        return _qwayland_surface(qw) or _win_id_surface(qw)
    return fn


def install_resolver_for(qwindow, compositor_ptr_fn=None):
    """Install a Wayland surface resolver for ``qwindow``.

    Call this once after the window has been shown and the Wayland
    platform plugin has initialized (i.e. after ``show()`` / ``raise_()``).

    Parameters
    ----------
    qwindow:
        The pet ``QWidget`` / ``QWindow``.
    compositor_ptr_fn:
        Optional callable ``(qwindow) -> int`` returning a
        ``wl_compositor*`` (e.g. from a ``wl_registry`` bind).  Most
        setups can leave this as ``None`` -- the backend tolerates a
        null compositor and falls back gracefully.
    """
    if not _HAS_CT or qwindow is None:
        return False
    if sys.platform != "linux" or _platform_name() != "wayland":
        # Only relevant under Wayland.  On X11 / Windows the native
        # backends do not need this resolver.
        return False

    install_wayland_resolver_from_qwindow(
        qwindow,
        surface_ptr_fn=_make_surface_fn(qwindow),
        compositor_ptr_fn=compositor_ptr_fn,
    )
    return True


def uninstall_resolver():
    """Clear the registered resolver (e.g. on window teardown)."""
    if _HAS_CT:
        set_wayland_surface_resolver(None)


def _platform_name() -> str:
    try:
        from PyQt5.QtGui import QGuiApplication
        return str(QGuiApplication.platformName() or "")
    except Exception:
        return ""


__all__ = ["install_resolver_for", "uninstall_resolver"]
