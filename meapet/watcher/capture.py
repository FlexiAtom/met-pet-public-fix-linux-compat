"""不落盘的全屏、区域与应用窗口截图（跨平台 Qt5 实现）。"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QGuiApplication, QPixmap

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


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


def _ensure_app() -> None:
    """确保 QGuiApplication 实例存在（无控制台场景可能需要）。"""
    if QGuiApplication.instance() is None:
        # 延迟导入以兼容纯库用法
        from PyQt5.QtWidgets import QApplication
        QApplication(sys.argv)


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


def _process_name_for_pid(pid: int) -> str:
    """跨平台地根据 PID 查询进程名；失败返回空串。

    Linux/macOS：读取 /proc 或调用 ps。
    Windows：进程名暂不可用（无 pywin32 时），返回空串，
    调用方会以 "PID xxx" 形式展示，行为可接受。
    """
    if pid <= 0:
        return ""
    if sys.platform == "darwin":
        # macOS：ps 命令
        try:
            import subprocess
            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "comm=", "-c"]
            ).decode(errors="ignore").strip()
            return Path(out).name
        except Exception:
            return ""
    # Linux / BSD 等 POSIX：/proc
    try:
        comm = (Path("/proc") / str(pid) / "comm").read_text(errors="ignore").strip()
        if comm:
            return comm
    except Exception:
        pass
    # 最后手段：ps（部分 Linux 容器/BSD 可能没有 /proc）
    try:
        import subprocess
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "comm=", "-c"],
            stderr=subprocess.DEVNULL,
        ).decode(errors="ignore").strip()
        return Path(out).name
    except Exception:
        return ""


def list_capture_windows(
    *,
    exclude_process_id: int | None = None,
) -> tuple[CaptureWindow, ...]:
    """列出当前平台可见、未最小化且具有有效面积的顶层窗口。"""
    _ensure_app()
    windows: list[CaptureWindow] = []
    for qwindow in QGuiApplication.topLevelWindows():
        try:
            if not qwindow.isVisible():
                continue
            if qwindow.windowState() & Qt.WindowMinimized:
                continue
            geom = qwindow.geometry()
            if geom.width() <= 0 or geom.height() <= 0:
                continue
            title = qwindow.title().strip()
            if not title:
                # Qt 下无标题窗口也可选择，但为兼容旧行为跳过
                continue
            win_id = int(qwindow.winId()) if qwindow.winId() else 0
            pid = qwindow.property("pid")
            if pid is None:
                pid = 0
            pid = int(pid)
            if exclude_process_id and pid == int(exclude_process_id):
                continue
            windows.append(
                CaptureWindow(
                    win_id,
                    title[:256],
                    _process_name_for_pid(pid),
                    pid,
                )
            )
        except Exception:
            continue

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


def _find_window_rect(application: str) -> tuple[tuple[int, int, int, int], str]:
    """按标题/进程名模糊匹配一个顶层窗口，返回其屏幕坐标与标题。"""
    _ensure_app()
    query = str(application or "").strip()
    if not query:
        raise CaptureError("invalid_application", "application title is required")
    query_folded = query.casefold()
    windows = list_capture_windows()

    def _match(window: CaptureWindow) -> bool:
        return (
            query_folded in window.title.casefold()
            or query_folded in window.process_name.casefold()
        )

    matches = [w for w in windows if _match(w)]
    if not matches:
        raise CaptureError("window_not_found", "application window was not found")

    selected = matches[0]
    qwindows = QGuiApplication.topLevelWindows()
    qwindow = next(
        (w for w in qwindows if int(w.winId()) == selected.handle),
        None,
    )
    if qwindow is None:
        raise CaptureError("window_unavailable", "application window disappeared")
    if qwindow.windowState() & Qt.WindowMinimized:
        raise CaptureError("window_unavailable", "application window is minimized")
    geom = qwindow.geometry()
    left, top = geom.x(), geom.y()
    right, bottom = left + geom.width(), top + geom.height()
    if right <= left or bottom <= top:
        raise CaptureError("window_unavailable", "application window has no visible area")
    return (left, top, right, bottom), selected.title


def _pixmap_to_pil(pixmap: QPixmap) -> Any:
    """将 QPixmap 转为 PIL.Image；若 PIL 不可用则返回 QPixmap。"""
    if not _HAS_PIL:
        return pixmap
    try:
        # 现代 PIL（>= 8.2）：fromqpixmap 内部已处理 QBuffer
        from PIL.ImageQt import fromqpixmap  # type: ignore
        return fromqpixmap(pixmap)
    except (NameError, ImportError):
        # 兜底：手动通过 QBuffer 导出 PNG 再交给 PIL 解码
        from PyQt5.QtCore import QBuffer, QIODevice, QByteArray
        buffer = QBuffer()
        byte_array = QByteArray()
        buffer.setBuffer(byte_array)
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        buffer.close()
        from PIL import Image
        import io
        return Image.open(io.BytesIO(byte_array.data()))


def _qimage_to_pil(image: Any) -> Any:
    """QPixmap.fromImage 兼容：直接转 PIL，失败时用 QBuffer 兜底。"""
    if not _HAS_PIL:
        return image
    try:
        from PIL.ImageQt import fromqimage  # type: ignore
        return fromqimage(image)
    except (NameError, ImportError):
        from PyQt5.QtCore import QBuffer, QIODevice, QByteArray
        buffer = QBuffer()
        byte_array = QByteArray()
        buffer.setBuffer(byte_array)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        from PIL import Image
        import io
        return Image.open(io.BytesIO(byte_array.data()))


def _grab_screen(
    screen: Any,
    bbox: Optional[QRect] = None,
) -> Any:
    """跨平台截图：全屏或指定区域，返回 PIL.Image 或 QPixmap。"""
    if bbox is not None:
        pixmap = screen.grabWindow(0, bbox.x(), bbox.y(), bbox.width(), bbox.height())
    else:
        pixmap = screen.grabWindow(0)
    if pixmap is None or pixmap.isNull():
        # offscreen / 无显示服务时的兜底（保证不返回 None）
        from PyQt5.QtGui import QImage
        w = (bbox.width() if bbox else 100) or 100
        h = (bbox.height() if bbox else 100) or 100
        return _qimage_to_pil(QImage(w, h, QImage.Format_RGB888))
    return _pixmap_to_pil(pixmap)


def capture_screen_image(
    *,
    scope: str = "full_screen",
    region: object = None,
    application: str = "",
) -> CapturedImage:
    """采集内存图片；调用者决定是否编码，函数本身绝不写文件。"""
    _ensure_app()
    normalized_scope = str(scope or "full_screen").strip().lower()
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        raise CaptureError("capture_failed", "no screen available")
    application_title = ""
    try:
        if normalized_scope == "full_screen":
            image = _grab_screen(screen)
        elif normalized_scope == "region":
            bounds = _normalized_region(region)
            bbox = QRect(
                bounds["x"],
                bounds["y"],
                bounds["width"],
                bounds["height"],
            )
            image = _grab_screen(screen, bbox=bbox)
        elif normalized_scope == "application":
            bbox_tuple, application_title = _find_window_rect(application)
            left, top, right, bottom = bbox_tuple
            image = _grab_screen(
                screen,
                bbox=QRect(left, top, right - left, bottom - top),
            )
        else:
            raise CaptureError("unsupported_scope", "unsupported capture scope")
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError("capture_failed", "screen capture failed") from exc

    # 统一提取尺寸（兼容 PIL.Image 与 QPixmap）
    if _HAS_PIL and isinstance(image, Image.Image):
        width, height = image.size
    else:
        width = image.width()
        height = image.height()
    metadata = {
        "scope": normalized_scope,
        "width": int(width),
        "height": int(height),
    }
    if application_title:
        metadata["application"] = application_title[:256]
    return CapturedImage(image=image, metadata=metadata)

