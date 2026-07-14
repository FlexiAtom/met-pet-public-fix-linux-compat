"""不落盘的全屏、区域与 Windows 应用窗口截图。"""

from __future__ import annotations

import sys  # 新增
from dataclasses import dataclass
from typing import Any, Mapping

from PIL import ImageGrab


class CaptureError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        print(f"[DEBUG capture] CaptureError.__init__: code={code!r}, message={message!r}", file=sys.stderr, flush=True)
        super().__init__(message)
        self.code = str(code)
        print(f"[DEBUG capture] CaptureError.__init__ done", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class CapturedImage:
    image: Any
    metadata: Mapping[str, object]


def _normalized_region(region: object) -> dict[str, int]:
    print(f"[DEBUG capture] _normalized_region called: region type={type(region).__name__}, value={region!r}", file=sys.stderr, flush=True)
    if not isinstance(region, dict):
        print(f"[DEBUG capture] _normalized_region: region is not dict, raising CaptureError", file=sys.stderr, flush=True)
        raise CaptureError("invalid_region", "region must contain x, y, width and height")
    try:
        result = {
            key: int(region[key])
            for key in ("x", "y", "width", "height")
        }
        print(f"[DEBUG capture] _normalized_region: parsed result={result}", file=sys.stderr, flush=True)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"[DEBUG capture] _normalized_region: parsing failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise CaptureError(
            "invalid_region",
            "region must contain integer x, y, width and height",
        ) from exc
    if result["width"] <= 0 or result["height"] <= 0:
        print(f"[DEBUG capture] _normalized_region: invalid dimensions, raising CaptureError", file=sys.stderr, flush=True)
        raise CaptureError("invalid_region", "region dimensions must be positive")
    print(f"[DEBUG capture] _normalized_region returning: {result}", file=sys.stderr, flush=True)
    return result


def _windows_application_rect(application: str) -> tuple[tuple[int, int, int, int], str]:
    print(f"[DEBUG capture] _windows_application_rect called: application={application!r}", file=sys.stderr, flush=True)
    if not sys.platform.startswith("win"):
        print(f"[DEBUG capture] _windows_application_rect: not Windows, raising CaptureError", file=sys.stderr, flush=True)
        raise CaptureError(
            "unsupported_scope",
            "application capture currently requires Windows",
        )
    query = str(application or "").strip()
    if not query:
        print(f"[DEBUG capture] _windows_application_rect: empty query, raising CaptureError", file=sys.stderr, flush=True)
        raise CaptureError("invalid_application", "application title is required")
    try:
        import win32gui
        print(f"[DEBUG capture] _windows_application_rect: imported win32gui", file=sys.stderr, flush=True)
    except ImportError as exc:
        print(f"[DEBUG capture] _windows_application_rect: ImportError, raising CaptureError", file=sys.stderr, flush=True)
        raise CaptureError(
            "dependency_missing",
            "application capture requires pywin32",
        ) from exc

    matches = []
    query_folded = query.casefold()
    print(f"[DEBUG capture] _windows_application_rect: query_folded={query_folded!r}", file=sys.stderr, flush=True)

    def collect(hwnd, _extra) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            if title and query_folded in title.casefold():
                matches.append((hwnd, title))
                print(f"[DEBUG capture] _windows_application_rect.collect: found hwnd={hwnd}, title={title!r}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[DEBUG capture] _windows_application_rect.collect: exception {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            return

    try:
        print(f"[DEBUG capture] _windows_application_rect: enumerating windows...", file=sys.stderr, flush=True)
        win32gui.EnumWindows(collect, None)
        print(f"[DEBUG capture] _windows_application_rect: enumeration done, matches count={len(matches)}", file=sys.stderr, flush=True)
    except Exception as exc:
        print(f"[DEBUG capture] _windows_application_rect: EnumWindows failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise CaptureError("capture_failed", "could not enumerate windows") from exc
    if not matches:
        print(f"[DEBUG capture] _windows_application_rect: no matching window found, raising CaptureError", file=sys.stderr, flush=True)
        raise CaptureError("window_not_found", "application window was not found")

    hwnd, title = matches[0]
    print(f"[DEBUG capture] _windows_application_rect: using first match: hwnd={hwnd}, title={title!r}", file=sys.stderr, flush=True)
    try:
        if win32gui.IsIconic(hwnd):
            print(f"[DEBUG capture] _windows_application_rect: window is minimized, raising CaptureError", file=sys.stderr, flush=True)
            raise CaptureError("window_unavailable", "application window is minimized")
        left, top, right, bottom = (
            int(value) for value in win32gui.GetWindowRect(hwnd)
        )
        print(f"[DEBUG capture] _windows_application_rect: GetWindowRect returned ({left},{top},{right},{bottom})", file=sys.stderr, flush=True)
    except CaptureError:
        raise
    except Exception as exc:
        print(f"[DEBUG capture] _windows_application_rect: GetWindowRect failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise CaptureError("window_unavailable", "application window disappeared") from exc
    if right <= left or bottom <= top:
        print(f"[DEBUG capture] _windows_application_rect: invalid rect dimensions, raising CaptureError", file=sys.stderr, flush=True)
        raise CaptureError("window_unavailable", "application window has no visible area")
    result = ((left, top, right, bottom), title)
    print(f"[DEBUG capture] _windows_application_rect returning: bbox={result[0]}, title={result[1]!r}", file=sys.stderr, flush=True)
    return result


def capture_screen_image(
    *,
    scope: str = "full_screen",
    region: object = None,
    application: str = "",
) -> CapturedImage:
    """采集内存图片；调用者决定是否编码，函数本身绝不写文件。"""
    print(f"[DEBUG capture] capture_screen_image called: scope={scope!r}, region={region!r}, application={application!r}", file=sys.stderr, flush=True)
    normalized_scope = str(scope or "full_screen").strip().lower()
    application_title = ""
    print(f"[DEBUG capture] capture_screen_image: normalized_scope={normalized_scope!r}", file=sys.stderr, flush=True)
    try:
        if normalized_scope == "full_screen":
            print(f"[DEBUG capture] capture_screen_image: grabbing full screen", file=sys.stderr, flush=True)
            image = ImageGrab.grab(all_screens=True)
            print(f"[DEBUG capture] capture_screen_image: grabbed full screen, size={image.size}", file=sys.stderr, flush=True)
        elif normalized_scope == "region":
            print(f"[DEBUG capture] capture_screen_image: normalizing region...", file=sys.stderr, flush=True)
            bounds = _normalized_region(region)
            bbox = (
                bounds["x"],
                bounds["y"],
                bounds["x"] + bounds["width"],
                bounds["y"] + bounds["height"],
            )
            print(f"[DEBUG capture] capture_screen_image: grabbing region bbox={bbox}", file=sys.stderr, flush=True)
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            print(f"[DEBUG capture] capture_screen_image: grabbed region, size={image.size}", file=sys.stderr, flush=True)
        elif normalized_scope == "application":
            print(f"[DEBUG capture] capture_screen_image: getting application rect...", file=sys.stderr, flush=True)
            bbox, application_title = _windows_application_rect(application)
            print(f"[DEBUG capture] capture_screen_image: grabbing application bbox={bbox}, title={application_title!r}", file=sys.stderr, flush=True)
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            print(f"[DEBUG capture] capture_screen_image: grabbed application window, size={image.size}", file=sys.stderr, flush=True)
        else:
            print(f"[DEBUG capture] capture_screen_image: unsupported scope, raising CaptureError", file=sys.stderr, flush=True)
            raise CaptureError("unsupported_scope", "unsupported capture scope")
    except CaptureError:
        raise
    except Exception as exc:
        print(f"[DEBUG capture] capture_screen_image: grab failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise CaptureError("capture_failed", "screen capture failed") from exc

    width, height = image.size
    metadata = {
        "scope": normalized_scope,
        "width": int(width),
        "height": int(height),
    }
    if application_title:
        metadata["application"] = application_title[:256]
    result = CapturedImage(image=image, metadata=metadata)
    print(f"[DEBUG capture] capture_screen_image returning: metadata={metadata}", file=sys.stderr, flush=True)
    return result

