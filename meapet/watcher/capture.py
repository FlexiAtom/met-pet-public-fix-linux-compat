"""不落盘的全屏、区域与 Windows 应用窗口截图。"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from PIL import ImageGrab


class CaptureError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


@dataclass(frozen=True)
class CapturedImage:
    image: Any
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class CaptureWindow:
    """可供本机用户选择的顶层可见窗口。"""

    handle: int = field(repr=False, compare=False)
    title: str
    process_name: str = ""
    process_id: int = 0

    @property
    def label(self) -> str:
        if self.process_name:
            process = self.process_name
            pid = f" · PID {self.process_id}" if self.process_id else ""
        else:
            process = f"PID {self.process_id}" if self.process_id else "未知进程"
            pid = ""
        return f"{process}{pid} — {self.title}"


def _normalized_region(region: object) -> dict[str, int]:
    if not isinstance(region, dict):
        raise CaptureError("invalid_region", "region must contain x, y, width and height")
    try:
        result = {
            key: int(region[key])
            for key in ("x", "y", "width", "height")
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise CaptureError(
            "invalid_region",
            "region must contain integer x, y, width and height",
        ) from exc
    if result["width"] <= 0 or result["height"] <= 0:
        raise CaptureError("invalid_region", "region dimensions must be positive")
    return result


def _windows_process_name(process_id: int) -> str:
    """使用 Windows 原生 API 查询可执行文件名；失败只退回 PID。"""
    if process_id <= 0 or not sys.platform.startswith("win"):
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            int(process_id),
        )
        if not handle:
            return ""
        try:
            capacity = 32768
            buffer = ctypes.create_unicode_buffer(capacity)
            size = wintypes.DWORD(capacity)
            if not kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            ):
                return ""
            return Path(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def list_capture_windows(
    *,
    exclude_process_id: int | None = None,
) -> tuple[CaptureWindow, ...]:
    """列出 Windows 上可见、未最小化且具有有效面积的顶层窗口。"""
    if not sys.platform.startswith("win"):
        return ()
    try:
        import win32gui
    except ImportError as exc:
        raise CaptureError(
            "dependency_missing",
            "application capture requires pywin32",
        ) from exc
    try:
        import win32process
    except ImportError:
        win32process = None

    windows: list[CaptureWindow] = []

    def collect(hwnd, _extra) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            if not title:
                return
            left, top, right, bottom = (
                int(value) for value in win32gui.GetWindowRect(hwnd)
            )
            if right <= left or bottom <= top:
                return
            process_id = 0
            if win32process is not None:
                _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
                process_id = int(process_id or 0)
            if exclude_process_id and process_id == int(exclude_process_id):
                return
            windows.append(
                CaptureWindow(
                    int(hwnd),
                    title[:256],
                    _windows_process_name(process_id),
                    process_id,
                )
            )
        except Exception:
            return

    try:
        win32gui.EnumWindows(collect, None)
    except Exception as exc:
        raise CaptureError("capture_failed", "could not enumerate windows") from exc

    unique: dict[tuple[int, str], CaptureWindow] = {}
    for window in windows:
        unique.setdefault((window.process_id, window.title.casefold()), window)
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                (item.process_name or "").casefold(),
                item.title.casefold(),
            ),
        )
    )


def _windows_application_rect(application: str) -> tuple[tuple[int, int, int, int], str]:
    if not sys.platform.startswith("win"):
        raise CaptureError(
            "unsupported_scope",
            "application capture currently requires Windows",
        )
    query = str(application or "").strip()
    if not query:
        raise CaptureError("invalid_application", "application title is required")
    query_folded = query.casefold()
    windows = list_capture_windows()
    exact = [window for window in windows if window.title.casefold() == query_folded]
    if not exact:
        exact = [
            window
            for window in windows
            if window.process_name.casefold() == query_folded
        ]
    matches = exact or [
        window
        for window in windows
        if query_folded in window.title.casefold()
        or query_folded in window.process_name.casefold()
    ]
    if not matches:
        raise CaptureError("window_not_found", "application window was not found")

    selected = matches[0]
    try:
        import win32gui

        if win32gui.IsIconic(selected.handle):
            raise CaptureError("window_unavailable", "application window is minimized")
        left, top, right, bottom = (
            int(value) for value in win32gui.GetWindowRect(selected.handle)
        )
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError("window_unavailable", "application window disappeared") from exc
    if right <= left or bottom <= top:
        raise CaptureError("window_unavailable", "application window has no visible area")
    return (left, top, right, bottom), selected.title


def capture_screen_image(
    *,
    scope: str = "full_screen",
    region: object = None,
    application: str = "",
) -> CapturedImage:
    """采集内存图片；调用者决定是否编码，函数本身绝不写文件。"""
    normalized_scope = str(scope or "full_screen").strip().lower()
    application_title = ""
    try:
        if normalized_scope == "full_screen":
            image = ImageGrab.grab(all_screens=True)
        elif normalized_scope == "region":
            bounds = _normalized_region(region)
            bbox = (
                bounds["x"],
                bounds["y"],
                bounds["x"] + bounds["width"],
                bounds["y"] + bounds["height"],
            )
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
        elif normalized_scope == "application":
            bbox, application_title = _windows_application_rect(application)
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
        else:
            raise CaptureError("unsupported_scope", "unsupported capture scope")
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError("capture_failed", "screen capture failed") from exc

    width, height = image.size
    metadata = {
        "scope": normalized_scope,
        "width": int(width),
        "height": int(height),
    }
    if application_title:
        metadata["application"] = application_title[:256]
    return CapturedImage(image=image, metadata=metadata)
