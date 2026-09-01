"""MeaPet 的跨窗口语义化 UI 设计令牌。"""

from __future__ import annotations

import os

import math
import re
import sys
import weakref
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


# 曜石夜（Obsidian Night，深色）：ZCode/Codex 式中性炭黑底衬暖橙强调；
# surface_input 比 canvas 更深，输入区读作“凹陷井”；border 承担静态分隔、
# border_strong 承担交互控件边（≥3:1）。
_DARK_PALETTE = {
    "canvas": "#171717",
    "surface": "#1F1F1F",
    "surface_elevated": "#272727",
    "surface_input": "#111111",
    "primary": "#FF7A1A",
    "primary_hover": "#FF9040",
    "on_primary": "#201000",
    "secondary": "#FFB366",
    "accent": "#FFA94D",
    "text_primary": "#F5F5F5",
    "text_secondary": "#C9C9C9",
    "text_muted": "#9A9A9A",
    "border": "#2E2E2E",
    "border_strong": "#737373",
    "focus": "#FFB366",
    "success": "#4ADE80",
    "warning": "#FBBF24",
    "danger": "#FF8080",
    "on_danger": "#2B0808",
    # 材质/装饰扩展令牌（随主题成对切换）
    "paper_top": "#262626",
    "paper_bottom": "#1B1B1B",
    "raised_top": "#333333",
    "raised_bottom": "#222222",
    "card_grad_top": "#2A2A2A",
    "card_grad_bottom": "#222222",
    "shell_top": "#1D1D1D",
    "shell_bottom": "#101010",
    "sidebar_bg": "#141414",
    "nav_selected_bg": "#272727",
    "sec_btn_top": "#2B2B2B",
    "sec_btn_bottom": "#222222",
    "sec_btn_hover_top": "#343434",
    "sec_btn_hover_bottom": "#2A2A2A",
    "sec_btn_pressed": "#1A1A1A",
    "primary_grad_end": "#FFC08A",
}

# 晨白（Porcelain，浅色）：Codex 浅色式暖白底，同系深橙强调；
# 交互边框同样满足 ≥3:1，输入区相对 canvas 略深保持“凹陷井”语义。
_LIGHT_PALETTE = {
    "canvas": "#F4F4F6",
    "surface": "#FFFFFF",
    "surface_elevated": "#EFEFF2",
    "surface_input": "#ECECEF",
    "primary": "#E05E10",
    "primary_hover": "#C24E0A",
    "on_primary": "#2B1200",
    "secondary": "#F5A95C",
    "accent": "#C2410C",
    "text_primary": "#1C1C1E",
    "text_secondary": "#48484C",
    "text_muted": "#68686E",
    "border": "#E2E2E7",
    "border_strong": "#7C7C82",
    "focus": "#C2410C",
    "success": "#15803D",
    "warning": "#B45309",
    "danger": "#D92D20",
    "on_danger": "#FFFFFF",
    "paper_top": "#FAFAFB",
    "paper_bottom": "#EDEDF0",
    "raised_top": "#F7F7F9",
    "raised_bottom": "#EAEAEE",
    "card_grad_top": "#FFFFFF",
    "card_grad_bottom": "#F1F1F4",
    "shell_top": "#FBFBFC",
    "shell_bottom": "#ECECEF",
    "sidebar_bg": "#E9EAEE",
    "nav_selected_bg": "#FFFFFF",
    "sec_btn_top": "#F1F1F4",
    "sec_btn_bottom": "#E7E7EB",
    "sec_btn_hover_top": "#EAEAEF",
    "sec_btn_hover_bottom": "#DFDFE4",
    "sec_btn_pressed": "#E3E3E8",
    "primary_grad_end": "#F5A95C",
}

# 活动调色板：dict 就地切换（PALETTE 代理视图始终反映当前主题），
# 便于运行时在 深色/浅色 之间热切换而无需重建 import 引用。
_PALETTE = dict(_DARK_PALETTE)
PALETTE: Mapping[str, str] = MappingProxyType(_PALETTE)

PALETTES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {"dark": MappingProxyType(_DARK_PALETTE), "light": MappingProxyType(_LIGHT_PALETTE)}
)

# ---- 主题模式（跟随系统 / 浅色 / 深色）----
THEME_MODES = ("system", "light", "dark")
UI_THEME_MODE_DEFAULT = "system"
_ui_theme_mode = UI_THEME_MODE_DEFAULT
_active_scheme = "dark"

if sys.platform == "win32":
    FALLBACK_BODY_FONT_NAME = "Microsoft YaHei UI"
elif sys.platform == "darwin":
    FALLBACK_BODY_FONT_NAME = "PingFang SC"
else:
    FALLBACK_BODY_FONT_NAME = "Noto Sans CJK SC"

# 正文和展示文字统一使用系统无障碍 UI 字体（Windows 为微软雅黑），
# 不再分发手写风格的内置字体，保证各平台都是清晰易读的“正常字体”。
DISPLAY_FONT_NAME = FALLBACK_BODY_FONT_NAME
DISPLAY_FONT_FAMILY = f'"{DISPLAY_FONT_NAME}"'
BUNDLED_DISPLAY_FONT_PATH = (
    Path(__file__).resolve().parent / "assets" / "fonts" / "LXGWWenKai-Regular.ttf"
)
BUNDLED_CHEVRON_UP_PATH = (
    Path(__file__).resolve().parent / "assets" / "icons" / "chevron-up.svg"
).as_posix()
BUNDLED_CHEVRON_DOWN_PATH = (
    Path(__file__).resolve().parent / "assets" / "icons" / "chevron-down.svg"
).as_posix()

BODY_FONT_NAME = DISPLAY_FONT_NAME
FONT_FAMILY = f'"{BODY_FONT_NAME}"'
MONO_FONT_FAMILY = '"Cascadia Code", "JetBrains Mono", "Cascadia Mono", Consolas, monospace'

_APPLICATION_FONT_FAMILIES: tuple[str, ...] = ()
_APPLICATION_BASE_FONT_POINT_SIZE: float | None = None
_APPLICATION_BASE_FONT_PIXEL_SIZE: int | None = None

UI_FONT_SCALE_MIN = 0.8
UI_FONT_SCALE_MAX = 1.5
UI_FONT_SCALE_DEFAULT = 1.0
_UI_FONT_SCALE = UI_FONT_SCALE_DEFAULT

# 桌宠窗口缩放（display.size_factor）：桌宠窗口与立绘一起按百分比缩放。
PET_SIZE_FACTOR_MIN = 0.3
PET_SIZE_FACTOR_MAX = 3.0
PET_SIZE_FACTOR_DEFAULT = 1.0
PET_SIZE_FACTOR_STEP = 0.05
# 右键菜单里的快捷百分比档位。
PET_SIZE_PRESETS = (50, 75, 100, 125, 150, 200)
_BASE_STYLESHEET_PROPERTY = "_meapetBaseStylesheet"
_SCALED_STYLESHEET_PROPERTY = "_meapetScaledStylesheet"
_FONT_SIZE_PATTERN = re.compile(
    r"(?P<prefix>font-size\s*:\s*)(?P<size>\d+(?:\.\d+)?)px",
    re.IGNORECASE,
)

MIN_TARGET_SIZE = 44

SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 20
SPACE_6 = 24
SPACE_8 = 32

RADIUS_SMALL = 10
RADIUS_MEDIUM = 14
RADIUS_LARGE = 20

# ---- 主题共享渐变（方向约定：主行动 135°、材质 90°、进度/扫光 0°）----
# 渐变由活动调色板派生，随 rebuild_gradients() 在主题切换时重建。
GRADIENT_PRIMARY = ""
GRADIENT_PRIMARY_HOVER = ""
GRADIENT_PROGRESS = ""
GRADIENT_PAPER = ""
GRADIENT_RAISED = ""
BUTTON_SECONDARY_BG = ""
BUTTON_SECONDARY_BG_HOVER = ""
BUTTON_SECONDARY_BG_PRESSED = ""


def rebuild_gradients() -> None:
    """依据活动调色板重建全部共享渐变常量（就地更新模块全局）。"""
    global GRADIENT_PRIMARY, GRADIENT_PRIMARY_HOVER, GRADIENT_PROGRESS
    global GRADIENT_PAPER, GRADIENT_RAISED
    global BUTTON_SECONDARY_BG, BUTTON_SECONDARY_BG_HOVER
    global BUTTON_SECONDARY_BG_PRESSED

    GRADIENT_PRIMARY = (
        "qlineargradient(x1:0, y1:0, x2:1, y2:1, "
        f"stop:0 {PALETTE['primary']}, stop:1 {PALETTE['secondary']})"
    )
    GRADIENT_PRIMARY_HOVER = (
        "qlineargradient(x1:0, y1:0, x2:1, y2:1, "
        f"stop:0 {PALETTE['primary_hover']}, stop:1 {PALETTE['primary_grad_end']})"
    )
    GRADIENT_PROGRESS = (
        "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
        f"stop:0 {PALETTE['primary']}, stop:0.55 {PALETTE['primary_hover']}, "
        f"stop:1 {PALETTE['secondary']})"
    )
    # 顶边微亮（装置 A）：竖向材质渐变，顶端亮一档
    GRADIENT_PAPER = (
        "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
        f"stop:0 {PALETTE['paper_top']}, stop:0.45 {PALETTE['surface']}, "
        f"stop:1 {PALETTE['paper_bottom']})"
    )
    GRADIENT_RAISED = (
        "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
        f"stop:0 {PALETTE['raised_top']}, stop:0.45 {PALETTE['surface_elevated']}, "
        f"stop:1 {PALETTE['raised_bottom']})"
    )
    # 次级按钮的三态底色
    BUTTON_SECONDARY_BG = (
        "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
        f"stop:0 {PALETTE['sec_btn_top']}, stop:1 {PALETTE['sec_btn_bottom']})"
    )
    BUTTON_SECONDARY_BG_HOVER = (
        "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
        f"stop:0 {PALETTE['sec_btn_hover_top']}, "
        f"stop:1 {PALETTE['sec_btn_hover_bottom']})"
    )
    BUTTON_SECONDARY_BG_PRESSED = PALETTE["sec_btn_pressed"]


rebuild_gradients()


def seam_highlight(alpha: int) -> str:
    """顶边高光色（装置 A），alpha 随表面层级 75–110。"""
    return rgba(_PALETTE["focus"], alpha)


def rgb_triplet(color: str) -> str:
    """把 ``#RRGGBB`` 转成 ``"R, G, B"``，供 QSS rgba() 拼接。"""
    value = color.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"颜色必须使用 #RRGGBB 格式: {color!r}")
    return ", ".join(str(int(value[i : i + 2], 16)) for i in (0, 2, 4))


# ---- 主题模式与运行时切换 ------------------------------------------------
def resolve_system_theme() -> str:
    """探测系统浅色/深色偏好；探测失败时回落到深色。"""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(value) else "dark"
        if sys.platform == "darwin":
            import subprocess

            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
            return "dark" if (out.stdout or "").strip() == "Dark" else "light"
        import shutil
        import subprocess

        if shutil.which("gsettings"):
            out = subprocess.run(
                [
                    "gsettings",
                    "get",
                    "org.freedesktop.appearance",
                    "color-scheme",
                ],
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
            return "light" if "light" in (out.stdout or "") else "dark"
    except Exception:
        pass
    return "dark"


def normalize_theme_mode(value: object) -> str:
    """把任意配置值规范到受支持的主题模式。"""
    mode = str(value or "").strip().lower()
    return mode if mode in THEME_MODES else UI_THEME_MODE_DEFAULT


def resolved_scheme(mode: object | None = None) -> str:
    """把主题模式解析为具体配色方案（system → 系统偏好）。"""
    effective = normalize_theme_mode(mode if mode is not None else _ui_theme_mode)
    if effective == "system":
        return resolve_system_theme()
    return effective


def get_ui_theme_mode() -> str:
    """返回当前主题模式（system/light/dark）。"""
    return _ui_theme_mode


def get_active_scheme() -> str:
    """返回当前实际生效的配色方案（light/dark）。"""
    return _active_scheme


def apply_palette_scheme(scheme: str) -> str:
    """把指定方案（light/dark）就地写入活动调色板，并重建派生渐变。"""
    global _active_scheme
    scheme = "light" if scheme == "light" else "dark"
    if scheme != _active_scheme:
        _active_scheme = scheme
        source = _LIGHT_PALETTE if scheme == "light" else _DARK_PALETTE
        _PALETTE.update(source)
        rebuild_gradients()
    return _active_scheme


def set_ui_theme_mode(mode: object, *, rebuild_styles: bool = True) -> str:
    """设置主题模式（system/light/dark），并重建样式返回生效方案。

    传 ``rebuild_styles=False`` 可只记录模式（例如进程启动早期，
    由入口稍后统一调用 :func:`reapply_theme` 重建）。
    """
    global _ui_theme_mode
    mode = normalize_theme_mode(mode)
    changed = mode != _ui_theme_mode
    scheme_changed = resolved_scheme(mode) != _active_scheme
    _ui_theme_mode = mode
    if changed or scheme_changed or rebuild_styles:
        apply_palette_scheme(resolved_scheme(mode))
        if rebuild_styles:
            rebuild_theme_styles()
    return _active_scheme


def load_ui_theme_mode_from_config(path: object | None = None) -> str:
    """从 config.json 预读 ``ui.theme`` 并应用；任何失败都静默回落默认。

    这里刻意不走 meapet.config.store（它会 import 本模块，存在循环），
    只做一次轻量 JSON 读取。
    """
    try:
        import json

        if path is None:
            from meapet.paths import data_path

            path = data_path("config.json")
        with open(path, "r", encoding="utf-8") as file:
            config = json.load(file)
        ui = config.get("ui") if isinstance(config, dict) else None
        mode = ui.get("theme") if isinstance(ui, dict) else None
        if mode:
            set_ui_theme_mode(mode, rebuild_styles=True)
    except Exception:
        pass
    return _ui_theme_mode


# ---- 可重建样式注册表 ----------------------------------------------------
# 样式模块（wizard.styles / meapet.desktop.theme）通过 rebuild_styles()
# 就地重建 QSS 常量，并把可按名引用的样式登记到各自 NAMED_STYLES；
# 控件经 apply_named_style() 应用后，主题切换时自动重放。
_NAMED_STYLE_BINDINGS: list[tuple[weakref.ref, object, object]] = []
_STYLE_MODULE_NAMES = (
    "wizard.styles",
    "meapet.desktop.theme",
    "meapet.message_dialog",
)


def get_named_style(name: str) -> str:
    """按名在各样式模块的 NAMED_STYLES 中解析当前主题的 QSS。"""
    for module_name in _STYLE_MODULE_NAMES:
        module = sys.modules.get(module_name)
        table = getattr(module, "NAMED_STYLES", None)
        if table and name in table:
            return table[name]
    raise KeyError(f"未注册的命名样式: {name!r}")


def _register_named_binding(widget, source, scale) -> None:
    _NAMED_STYLE_BINDINGS.append((weakref.ref(widget), source, scale))
    if len(_NAMED_STYLE_BINDINGS) > 64:
        _NAMED_STYLE_BINDINGS[:] = [
            entry
            for entry in _NAMED_STYLE_BINDINGS
            if entry[0]() is not None
        ]


def apply_named_style(widget, name: str, *, scale: object | None = None):
    """应用命名主题样式并登记，主题切换时自动重放。"""
    set_scaled_stylesheet(widget, get_named_style(name), scale)
    _register_named_binding(widget, name, scale)
    return widget


def apply_inline_style(widget, builder, *, scale: object | None = None):
    """应用内联 QSS（builder 为返回样式文本的可调用）并登记重放。"""
    set_scaled_stylesheet(widget, builder(), scale)
    _register_named_binding(widget, builder, scale)
    return widget


def rebuild_theme_styles() -> None:
    """重建全部样式模块常量，并把已登记控件重放到当前主题。"""
    for module_name in _STYLE_MODULE_NAMES:
        module = sys.modules.get(module_name)
        rebuild = getattr(module, "rebuild_styles", None) or getattr(
            module, "rebuild_message_dialog_style", None
        )
        if callable(rebuild):
            rebuild()

    alive: list[tuple[weakref.ref, object, object]] = []
    replayed: list[object] = []
    for ref, source, scale in _NAMED_STYLE_BINDINGS:
        widget = ref()
        if widget is None:
            continue
        alive.append((ref, source, scale))
        try:
            if callable(source):
                text = source()
            else:
                text = get_named_style(source)
            set_scaled_stylesheet(widget, text, scale)
            replayed.append(widget)
        except (RuntimeError, KeyError):
            # C++ 对象已销毁，或样式暂未注册（模块尚未导入）。
            continue
    _NAMED_STYLE_BINDINGS[:] = alive

    # 换肤只改颜色不改几何时，Qt 不会自动刷新“级联自对话框样式表”的
    # 子控件缓存；对重放根的整棵子树 unpolish/polish 强制重绘。
    from PyQt5.QtWidgets import QWidget

    roots = list(replayed)
    for root in list(replayed):
        try:
            roots.extend(root.findChildren(QWidget))
        except RuntimeError:
            continue
    for widget in roots:
        try:
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()
        except RuntimeError:
            continue


def reapply_theme(mode: object | None = None) -> str:
    """应用主题模式并重建/重放全部注册样式，返回生效方案。"""
    set_ui_theme_mode(mode if mode is not None else _ui_theme_mode)
    return _active_scheme


# 进程首次导入即按本地配置决定主题，随后构建的样式常量都基于该主题。
load_ui_theme_mode_from_config()


def ensure_application_fonts() -> tuple[str, ...]:
    """把系统 UI 字体设为 Qt 全局默认字体（不再加载内置手写字体）。"""
    global _APPLICATION_FONT_FAMILIES
    global _APPLICATION_BASE_FONT_PIXEL_SIZE
    global _APPLICATION_BASE_FONT_POINT_SIZE

    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return _APPLICATION_FONT_FAMILIES

    if not _APPLICATION_FONT_FAMILIES:
        # 不再扫描 QFontDatabase（offscreen/无头平台字体库为空），
        # 直接采用平台默认 UI 字体名；Qt 渲染时对缺失字体会自动回退。
        _APPLICATION_FONT_FAMILIES = (FALLBACK_BODY_FONT_NAME,)

    resolved_family = (
        _APPLICATION_FONT_FAMILIES[0]
        if _APPLICATION_FONT_FAMILIES
        else FALLBACK_BODY_FONT_NAME
    )
    app_font = app.font()
    if (
        _APPLICATION_BASE_FONT_POINT_SIZE is None
        and _APPLICATION_BASE_FONT_PIXEL_SIZE is None
    ):
        if app_font.pointSizeF() > 0:
            _APPLICATION_BASE_FONT_POINT_SIZE = app_font.pointSizeF()
        elif app_font.pixelSize() > 0:
            _APPLICATION_BASE_FONT_PIXEL_SIZE = app_font.pixelSize()

    app_font.setFamily(resolved_family)
    if _APPLICATION_BASE_FONT_POINT_SIZE is not None:
        app_font.setPointSizeF(
            _APPLICATION_BASE_FONT_POINT_SIZE * _UI_FONT_SCALE
        )
    elif _APPLICATION_BASE_FONT_PIXEL_SIZE is not None:
        app_font.setPixelSize(
            max(1, round(_APPLICATION_BASE_FONT_PIXEL_SIZE * _UI_FONT_SCALE))
        )
    app.setFont(app_font)
    return _APPLICATION_FONT_FAMILIES


def normalize_ui_font_scale(value: object) -> float:
    """把任意配置值规范到受支持的字体缩放范围。"""
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return UI_FONT_SCALE_DEFAULT
    if not math.isfinite(scale):
        return UI_FONT_SCALE_DEFAULT
    return min(max(scale, UI_FONT_SCALE_MIN), UI_FONT_SCALE_MAX)


def normalize_pet_size_factor(value: object) -> float:
    """把任意配置值规范到受支持的桌宠窗口缩放范围（30%–300%）。"""
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return PET_SIZE_FACTOR_DEFAULT
    if not math.isfinite(factor):
        return PET_SIZE_FACTOR_DEFAULT
    factor = min(max(factor, PET_SIZE_FACTOR_MIN), PET_SIZE_FACTOR_MAX)
    return round(factor, 2)


def get_ui_font_scale() -> float:
    """返回当前进程使用的全局界面字体缩放。"""
    return _UI_FONT_SCALE


def set_ui_font_scale(scale: object) -> float:
    """设置全局界面字体缩放，并同步 Qt 默认字体。"""
    global _UI_FONT_SCALE

    _UI_FONT_SCALE = normalize_ui_font_scale(scale)
    ensure_application_fonts()
    return _UI_FONT_SCALE


def scale_stylesheet_font_sizes(
    stylesheet: str,
    scale: object | None = None,
) -> str:
    """只缩放 QSS 中的 ``font-size: Npx``，不改变布局尺寸。"""
    factor = (
        get_ui_font_scale()
        if scale is None
        else normalize_ui_font_scale(scale)
    )

    def replace(match: re.Match[str]) -> str:
        size = max(1, round(float(match.group("size")) * factor))
        return f"{match.group('prefix')}{size}px"

    return _FONT_SIZE_PATTERN.sub(replace, stylesheet or "")


def set_scaled_stylesheet(widget, stylesheet: str, scale: object | None = None) -> str:
    """给控件应用可重复缩放的 QSS，并保留一份未缩放基准。"""
    factor = (
        get_ui_font_scale()
        if scale is None
        else normalize_ui_font_scale(scale)
    )
    base = stylesheet or ""
    scaled = scale_stylesheet_font_sizes(base, factor)
    widget.setProperty(_BASE_STYLESHEET_PROPERTY, base)
    widget.setProperty(_SCALED_STYLESHEET_PROPERTY, scaled)
    widget.setStyleSheet(scaled)
    return scaled


def apply_ui_font_scale(root, scale: object | None = None) -> float:
    """缩放控件树中的显式 QSS；重复预览不会发生倍率累乘。"""
    factor = (
        get_ui_font_scale()
        if scale is None
        else set_ui_font_scale(scale)
    )

    from PyQt5.QtWidgets import QWidget

    widgets = (root, *root.findChildren(QWidget))
    for widget in widgets:
        current = widget.styleSheet()
        if not current:
            continue
        base = widget.property(_BASE_STYLESHEET_PROPERTY)
        last_scaled = widget.property(_SCALED_STYLESHEET_PROPERTY)
        if not isinstance(base, str) or current != last_scaled:
            base = current
        scaled = scale_stylesheet_font_sizes(base, factor)
        widget.setProperty(_BASE_STYLESHEET_PROPERTY, base)
        widget.setProperty(_SCALED_STYLESHEET_PROPERTY, scaled)
        if current != scaled:
            widget.setStyleSheet(scaled)
    return factor


def rgba(color: str, alpha: int) -> str:
    """把 ``#RRGGBB`` 转成 Qt 样式表可用的 ``rgba`` 字符串。"""
    value = color.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"颜色必须使用 #RRGGBB 格式: {color!r}")
    if not 0 <= alpha <= 255:
        raise ValueError(f"alpha 必须在 0..255 之间: {alpha}")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def contrast_ratio(foreground: str, background: str) -> float:
    """返回两个 ``#RRGGBB`` 颜色的 WCAG 2.x 对比度。"""
    foreground_luminance = _relative_luminance(foreground)
    background_luminance = _relative_luminance(background)
    lighter = max(foreground_luminance, background_luminance)
    darker = min(foreground_luminance, background_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(color: str) -> float:
    value = color.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"颜色必须使用 #RRGGBB 格式: {color!r}")
    channels = [int(value[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def resolve_reduced_motion(config_value: object | None = None) -> bool:
    """合并配置项、显式环境变量与常见系统减少动画启发式。

    优先级：
    1. ``config_value`` 若为 True → 开启
    2. 环境变量 ``MEA_PET_REDUCED_MOTION``
    3. Linux: ``gsettings`` / ``org.gnome.desktop.interface enable-animations``
    4. 默认 False
    """
    if config_value is True or str(config_value).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if config_value is False:
        # 用户在配置中明确关闭时，仍允许环境变量强制开启
        env = os.environ.get("MEA_PET_REDUCED_MOTION", "").strip().lower()
        if env in {"1", "true", "yes", "on"}:
            return True
        if env in {"0", "false", "no", "off"}:
            return False
    else:
        env = os.environ.get("MEA_PET_REDUCED_MOTION", "").strip().lower()
        if env in {"1", "true", "yes", "on"}:
            return True
        if env in {"0", "false", "no", "off"}:
            return False

    # 系统启发式（失败则忽略）
    try:
        import subprocess
        import shutil

        if shutil.which("gsettings"):
            out = subprocess.run(
                [
                    "gsettings",
                    "get",
                    "org.gnome.desktop.interface",
                    "enable-animations",
                ],
                capture_output=True,
                text=True,
                timeout=0.4,
                check=False,
            )
            val = (out.stdout or "").strip().lower()
            if val in {"false", "0"}:
                return True
    except Exception:
        pass
    return False


def apply_reduced_motion_env(enabled: bool) -> None:
    """把减少动画偏好写入进程环境，供气泡/输入等模块读取。"""
    if enabled:
        os.environ["MEA_PET_REDUCED_MOTION"] = "1"
    else:
        # 仅在我们写入 1 时清理；若用户外部强制 0/1 也统一落到当前偏好
        os.environ.pop("MEA_PET_REDUCED_MOTION", None)
